/* 补抓队列：自动补抓只跑 fetched=0（从未抓过的记录）；同一个按钮的人工点击
   是**强制补抓**，连抓过失败的一起重跑。两者都不改服务端，只是入队集合不同。 */
import { updateCard } from "./cards.js";
import { openDetail } from "./detail.js";
import { postFetch } from "./fetch.js";
import { detectProxy } from "./proxy-panel.js";
import { loadRecords, upsert } from "./records.js";
import { $, hostOf, rootDomain, setStatus, state } from "./state.js";

/* urls=待抓（出队即删）；total/ok/fail 是本轮计数；
   running=worker 在跑；paused=已暂停（暂停位落 localStorage，
   否则关掉页面再打开又自己跑起来，用户按不住） */
/* inflight：已出队、请求还没回来的 url。轮询每 10 秒来一次，而一次抓取可能更久
   （三级降级 + 代理），只按 urls 去重的话，「在途」的记录会被再次入队——
   计数虚高、甚至发出重复请求。出队即入集、收尾即出集。 */
export const backfillState = {
  urls: [], total: 0, ok: 0, fail: 0, skip: 0, running: false, paused: false, note: "",
  inflight: new Set(),
  /* gen：**轮次代际**。切换存储目录时 +1（resetBackfill）——旧轮次的 worker/轮询
     收口时按代际早退，绝不把旧项目的计数、状态、入队带到新目录。 */
  gen: 0,
  /* forceUrls：**人工点进来**要抓的 url —— 强制补抓的失败记录、多选操作条
     「重新抓取」指定的那批（可能含已抓成功的）。出队复查默认跳过「已尝试过」的记录，
     靠这个集合无条件放行；自动路径永远不会往它里面写（入队来源只喂 fetched=0），
     所以「自动不重试失败记录」是由**入队来源**保证的，不是靠成功位判断。
     forceRun：本轮是人工开的（按钮与完成文案显示「强制补抓」），跑完复位。 */
  forceUrls: new Set(),
  forceRun: false,
};

const PAUSE_KEY = "almond.backfill.paused";
const WORKERS = 5;

/* localStorage 只在函数里碰：模块体顶层读它会撞上「顶层零 DOM 调用」的循环导入铁律
   （Node 里做 import 解析时根本没有 localStorage，会直接抛 ReferenceError） */

function loadPaused() {
  try { return localStorage.getItem(PAUSE_KEY) === "1"; } catch (e) { return false; }
}

function savePaused(on) {
  try {
    if (on) localStorage.setItem(PAUSE_KEY, "1");
    else localStorage.removeItem(PAUSE_KEY);
  } catch (e) { /* 隐私模式等：记不住就记不住，不影响本次运行 */ }
}

/* 这条 url 所属域名是否参与自动补抓（domains.auto_fetch）。
   ⚠ 后端把三态压成 JSON 的 true/false/null —— 写成 `!== 0` 会把 false 漏判成「参与」，
   「关」的域名照样被抓。只有**明确为 false** 才排除；null（跟随全局）= 参与；
   没有该域名的配置也视为参与（缺配置不该让补抓停摆）。
   **只管自动路径**：强制补抓、批量重新抓取是人工动作，压过这条规则。 */
function autoFetchEligible(url) {
  const cfg = state.domainConfig.get(rootDomain(hostOf(url)));
  if (!cfg) return true;
  return cfg.auto_fetch !== false;
}

/* ---------- 按钮：进度的权威载体 ----------
   状态栏会被 render() 的「共 N 条…」覆盖，只能当即时提示；
   按钮文案由这里统一刷，入队 / 每条完成 / 暂停与继续都会调。 */

/* 本轮的叫法：人工开的叫强制补抓，自动开的叫自动补抓（状态栏文案共用） */
function kindText() {
  return backfillState.forceRun ? "强制补抓" : "自动补抓";
}

function paintButton() {
  const btn = $("backfillBtn");
  if (!btn) return;
  const left = backfillState.urls.length;
  const busy = backfillState.running || left > 0;
  btn.classList.toggle("active", busy && !backfillState.paused);

  /* 运行中/排队中：按钮可点（= 暂停 / 继续），文案报进度 */
  if (busy) {
    btn.disabled = false;
    btn.title = backfillState.paused ? "点击继续补抓" : "点击暂停补抓";
    if (backfillState.paused) btn.textContent = `继续补抓 ${left}`;
    else btn.textContent =
      `${backfillState.forceRun ? "强制" : ""}补抓 ${backfillState.total - left}/${backfillState.total}`;
    return;
  }

  /* 空闲：数一数还有没有活——从未抓取的、抓过但失败的。
     都抓完了才禁用（用户要求：均完成后按钮不再使能），按钮本身不隐藏。 */
  let pending = 0;
  let failed = 0;
  for (const r of state.records) {
    /* 被域名规则（auto_fetch=0）排除的待抓记录不计数：
       否则按钮一直显示「补抓 N」却永远不会变灰——自动路径根本不入队它们 */
    if (!r.fetched) { if (autoFetchEligible(r.url)) pending++; }
    else if (!r.success) failed++;
  }
  btn.classList.remove("active");
  if (!pending && !failed) {
    btn.disabled = true;
    btn.textContent = "补抓";
    btn.title = "已全部抓取完成，暂无需要补抓的记录";
    return;
  }
  btn.disabled = false;
  btn.textContent = pending && failed
    ? `补抓 ${pending} · 重试 ${failed}`
    : pending ? `补抓 ${pending}` : `重试 ${failed}`;
  btn.title = "点击强制补抓：从未抓过的和抓取失败的一起重跑（自动补抓只跑从未抓过的）";
}

/* ---------- 切换存储等场景：整队作废 ---------- */

/** 立即清空补抓队列并使旧轮次作废（在途请求由 abortAllFetches 中止、
    服务端以存储纪元丢弃结果；这里的代际让旧 worker/轮询收口时不再碰任何状态）。 */
export function resetBackfill() {
  backfillState.gen++;
  backfillState.urls.length = 0;
  backfillState.inflight.clear();
  backfillState.forceUrls.clear();
  backfillState.forceRun = false;
  backfillState.total = backfillState.ok = backfillState.fail = backfillState.skip = 0;
  backfillState.note = "";
  backfillState.running = false;   // 旧 worker 的 Promise.all 收口按代际早退，不会覆盖这里
  clearTimeout(pollTimer);
  paintButton();                   // 按钮先按当前 records 复位（随后 loadRecords 会再刷）
}

/* ---------- 入队 ---------- */

export function enqueueBackfill(urls, note, explicit) {
  /* explicit = 人工点进来的（强制补抓、批量重新抓取），三件事缺一不可：
     ① 在去重**之前**打 forceUrls 标记——已经在队里、正等着按自动规则被跳过的
        也要放行；② forceRun=true，本轮按钮与完成文案得写「强制补抓」；
     ③ 暂停中直接恢复——用户明确点了动作，就是让它跑 */
  /* 自动入队（开页扫描 / 10 秒轮询 / 导入）先按域名 auto_fetch 规则筛掉；
     explicit（人工点的强制补抓、批量重抓）不过滤——人工动作压过规则 */
  const pool = explicit ? urls : urls.filter(autoFetchEligible);

  if (explicit) {
    for (const u of pool) if (u) backfillState.forceUrls.add(u);
    backfillState.forceRun = true;
    if (backfillState.paused) {
      backfillState.paused = false;
      savePaused(false);
    }
  }

  const queued = new Set(backfillState.urls);
  const add = pool.filter((u) => u && !queued.has(u) && !backfillState.inflight.has(u));
  if (!add.length) {
    /* 早返回也得救一把：暂停态下 workers 已经退出，队里若还排着东西，
       没人会再拉起它——这时必须自己 runBackfill()，否则点了「重新抓取」
       什么都不发生（队列永远停着） */
    if (note) setStatus(note);
    paintButton();
    if (explicit && backfillState.urls.length && !backfillState.running) runBackfill();
    return;
  }

  // 上一轮已经跑空 → 这是新一轮，计数重算（忙时累加，别把进度条清零）。
  // 判据必须带上 urls.length：暂停时 running=false 但队列还留着上一轮的 url，
  // 这时重算会让 total 只等于新批次、进度算式 total - urls.length 直接失真。
  if (!backfillState.running && !backfillState.urls.length) {
    backfillState.total = backfillState.ok = backfillState.fail = backfillState.skip = 0;
    backfillState.note = "";
  }
  /* note（如「已入库 500 条」）跟着这一轮走：入队时那句提示几毫秒后就被
     「补抓 x/y」的进度盖掉，不带到完成文案里，导入结果就再也看不到了 */
  if (note) backfillState.note = note;
  backfillState.urls.push(...add);
  backfillState.total += add.length;

  if (backfillState.paused) {
    setStatus(`${note ? note + "；" : ""}${kindText()}已暂停（待抓 ${backfillState.urls.length} 条）`);
    paintButton();
    return;
  }
  setStatus(
    `${note ? note + "；" : ""}${kindText()}：待抓 ${backfillState.urls.length} 条（${WORKERS} 并发）`,
    "busy"
  );
  paintButton();
  runBackfill();
}

/* ---------- 轮询：页面开着时新进库的待抓记录 ----------
   startBackfill 只在加载时扫一次 state.records；别的标签页、浏览器插件、直接调
   /api/records/quick 的导入随后写进库的链接不会自己冒出来——只扫一次的话，
   「打开页面之后才出现的待抓记录」永远轮不到补抓（按钮也就一直不出现）。
   10 秒一次，只回 url：空闲时就是 {"urls":[]} 几十字节，比拉全量记录轻得多。 */
const POLL_MS = 10000;
let pollTimer = 0;
let polling = false;

function schedulePoll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(pollPending, POLL_MS);
}

function pollPending() {
  const gen = backfillState.gen;
  fetch("/api/records/pending")
    .then((r) => r.json())
    .then((data) => {
      if (gen !== backfillState.gen) return;   // 已切存储：本响应作废，不入队也不续轮询
      const urls = (data && data.urls) || [];
      /* 有本地列表里还没有的记录 → 先补一次列表再入队：出队复查会把
         「不在 state.records 里」当成已删除跳过，不等 loadRecords 会白跳一轮 */
      const known = new Set(state.records.map((r) => r.url));
      const ready = urls.some((u) => !known.has(u)) ? loadRecords() : Promise.resolve();
      return ready.then(() => {
        if (gen === backfillState.gen) enqueueBackfill(urls);
      });
    })
    .then(() => { if (gen === backfillState.gen) schedulePoll(); })
    .catch(() => { if (gen === backfillState.gen) schedulePoll(); }); // 瞬时故障下轮再试
}

function startPolling() {
  if (!polling) {
    polling = true;
    // 切回标签页立刻查一次，不必干等下一个 10 秒
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) { clearTimeout(pollTimer); pollPending(); }
    });
  }
  schedulePoll();   // 每次启动都重排一轮（切换存储后重启时靠它重新拉起）
}

/* 页面打开时调：扫一遍库里 fetched=0 的（插入顺序 = 后端 ORDER BY rowid），
   并开始轮询，把之后才进库的待抓记录也接进来 */
export function startBackfill(note) {
  backfillState.paused = loadPaused();   // 以持久化的暂停位为准
  const pending = state.records.filter((r) => !r.fetched).map((r) => r.url);
  enqueueBackfill(pending, note);
  startPolling();
}

/* 人工点击 = **强制补抓**：从未抓过的 + 抓过失败的一起重跑。
   先 loadRecords 再算目标：失败可能来自上一轮或另一个标签页，本地列表未必最新，
   而且出队复查要在 state.records 里找得到这条才放行。 */
export function forceBackfill() {
  setStatus("强制补抓：读取待抓与失败记录…", "busy");
  loadRecords().then(() => {
    const failedUrls = state.records.filter((r) => r.fetched && !r.success).map((r) => r.url);
    const targets = state.records.filter((r) => !r.fetched || !r.success).map((r) => r.url);
    if (!targets.length) {
      setStatus("没有需要补抓的记录");
      paintButton();
      return;
    }
    for (const u of failedUrls) backfillState.forceUrls.add(u);
    backfillState.forceRun = true;
    enqueueBackfill(
      targets,
      `强制补抓 ${targets.length} 条${failedUrls.length ? `（含失败重试 ${failedUrls.length} 条）` : ""}`
    );
  });
}

/* 按钮唯一点击入口：正在跑 / 已排队 / 已暂停 → 暂停或继续；空闲 → 强制补抓 */
export function handleBackfillClick() {
  if (backfillState.running || backfillState.urls.length || backfillState.paused) {
    toggleBackfill();
    return;
  }
  forceBackfill();
}

export function toggleBackfill() {
  backfillState.paused = !backfillState.paused;
  savePaused(backfillState.paused);
  if (backfillState.paused) {
    setStatus(`${kindText()}已暂停（剩余 ${backfillState.urls.length} 条）`);
    paintButton();
    return;
  }
  paintButton();
  runBackfill();   // 已有 worker 在收尾时这里会被守卫挡掉，由它们的收口逻辑接着跑
}

/* ---------- 执行 ---------- */

async function runBackfill() {
  if (backfillState.running || backfillState.paused) return;
  if (!backfillState.urls.length) { paintButton(); return; }
  const gen = backfillState.gen;          // 本所属轮次；切存储后 gen+1，一切旧状态作废
  backfillState.running = true;

  /* 开跑前先更新一次代理端口：页面开着期间代理可能才起来、或者换过端口，
     拿着过期地址去抓会整轮走错路（探测 7889-7899，约 0.25s；
     探到才写入，探不到保持原配置；面板里手填过的地址不覆盖）。
     每轮只探一次，不逐条探。 */
  if (!state.proxyManual) await detectProxy({ fill: true, announce: false });
  if (gen !== backfillState.gen) return;   // 等待期间切了存储：绝不碰 running（可能已属新轮）

  const worker = async () => {
    while (gen === backfillState.gen && backfillState.urls.length && !backfillState.paused) {
      const url = backfillState.urls.shift();
      /* 出队复查——自动与人工的分界：
         · 记录已被删除 → 跳过；
         · 已尝试过（fetched=1）**且不是人工点进来的** → 跳过。自动入队的只会是
           fetched=0 的记录，所以这条等价于「自动补抓不碰失败记录」；
           人工点进来的（forceUrls 命中：强制重试、批量重新抓取）无条件放行——
           因此连**已抓成功**的记录也能重抓（旧规则里的 rec.success 会把它拦下） */
      const rec = state.records.find((r) => r.url === url);
      const forced = backfillState.forceUrls.delete(url);
      /* 附带一条：域名的 auto_fetch 规则若在排队期间被改成「关」，
         自动入队的这条就地跳过；人工标记的照抓（explicit 压过规则） */
      if (!rec || (rec.fetched && !forced) || (!forced && !autoFetchEligible(url))) {
        backfillState.skip++;
        paintButton();
        continue;
      }

      const done = backfillState.total - backfillState.urls.length;
      setStatus(
        `补抓 ${done}/${backfillState.total}（成功 ${backfillState.ok}，失败 ${backfillState.fail}）`,
        "busy"
      );
      paintButton();
      backfillState.inflight.add(url);   // 在途：挡住下一次轮询的重复入队
      try {
        const data = await postFetch("/api/fetch", { url, proxy: state.globalProxy });
        if (gen !== backfillState.gen) {
          // 切存储后返回的旧轮结果：不计数、不 upsert、不更新卡片
        } else if (data.stale) {
          // 服务端纪元兜底：抓取期间切库，结果已被丢弃
        } else if (data.ok) {
          upsert(data.record);
          /* 计数按**本次抓取**成败（响应里的 success），不是 data.record.success——
             后者是合并后的值：快照导入的记录抓失败时按合并规则保留旧内容，
             success 仍是入库时的 1，拿它数会把失败全算成成功 */
          const fetched = data.success === undefined ? !!data.record.success : data.success;
          if (fetched) backfillState.ok++;
          else backfillState.fail++;
          updateCard(data.record);   // 就地补丁，不整页重绘（会闪）
          // 详情弹层正开着这条 → 同步刷新，否则还是「详情尚未抓取」的旧文案
          if (state.detailTarget && state.detailTarget.url === data.record.url) {
            openDetail(data.record);
          }
        } else backfillState.fail++;
      } catch (e) {
        // AbortError = 切存储时被中止，不算失败；其余网络错误照常计
        if (gen === backfillState.gen && (!e || e.name !== "AbortError")) backfillState.fail++;
      } finally {
        backfillState.inflight.delete(url);
      }
      if (gen !== backfillState.gen) return;   // 已切存储：立刻退出本 worker
      paintButton();
    }
  };

  Promise.all(Array.from({ length: WORKERS }, () => worker())).then(() => {
    if (gen !== backfillState.gen) return;   // 旧轮收口：状态归新轮/重置所有，一概不碰
    backfillState.running = false;
    if (backfillState.urls.length) {
      if (backfillState.paused) {
        setStatus(`${kindText()}已暂停（剩余 ${backfillState.urls.length} 条）`);
        paintButton();
        return;
      }
      runBackfill();   // 等待期间又入了新任务
      return;
    }
    /* 队列空了就按完成处理：此刻才点下的那次暂停没有意义（已无剩余），
       留着 paused 会让下次开页/入队直接停住，用户却想不起自己按过暂停 */
    if (backfillState.paused) {
      backfillState.paused = false;
      savePaused(false);
    }
    /* 本轮收口：强制标记只对本轮有效，下一轮回到纯自动语义 */
    const forcedRun = backfillState.forceRun;
    backfillState.forceRun = false;
    backfillState.forceUrls.clear();
    const kind = forcedRun ? "强制补抓" : "自动补抓";
    const prefix = backfillState.note ? backfillState.note + "；" : "";
    if (!backfillState.ok && !backfillState.fail) {
      setStatus(`${prefix}${kind}：${backfillState.total} 条此前都已抓过，无需补抓`);
      paintButton();
      return;
    }
    setStatus(
      `${prefix}${kind}完成：成功 ${backfillState.ok}，失败 ${backfillState.fail}` +
      `${backfillState.skip ? `，跳过 ${backfillState.skip}` : ""}（共 ${backfillState.total} 条）`,
      backfillState.ok ? "" : "err"
    );
    paintButton();
  });
}
