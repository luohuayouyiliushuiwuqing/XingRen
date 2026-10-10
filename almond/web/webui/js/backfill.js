/* 补抓队列：自动补抓只跑 fetched=0（从未抓过的记录）；同一个按钮的人工点击
   是**强制补抓**，连抓过失败的一起重跑。两者都不改服务端，只是入队集合不同。 */
import { updateCard } from "./cards.js";
import { openDetail } from "./detail.js";
import { loadRecords, upsert } from "./records.js";
import { $, setStatus, state } from "./state.js";

/* urls=待抓（出队即删）；total/ok/fail 是本轮计数；
   running=worker 在跑；paused=已暂停（暂停位落 localStorage，
   否则关掉页面再打开又自己跑起来，用户按不住） */
/* inflight：已出队、请求还没回来的 url。轮询每 10 秒来一次，而一次抓取可能更久
   （三级降级 + 代理），只按 urls 去重的话，「在途」的记录会被再次入队——
   计数虚高、甚至发出重复请求。出队即入集、收尾即出集。 */
export const backfillState = {
  urls: [], total: 0, ok: 0, fail: 0, skip: 0, running: false, paused: false, note: "",
  inflight: new Set(),
  /* forceUrls：人工「强制补抓」入队的**失败记录**（fetched=1 但 success=0）。
     出队复查默认跳过已尝试过的记录，靠这个集合放行强制那批；
     只记失败记录就够——强制时待抓的那部分本来就会被自动规则放行。
     forceRun：本轮是强制开的（按钮文案显示「强制补抓」），跑完复位。 */
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

/* ---------- 按钮：进度的权威载体 ----------
   状态栏会被 render() 的「共 N 条…」覆盖，只能当即时提示；
   按钮文案由这里统一刷，入队 / 每条完成 / 暂停与继续都会调。 */

function paintButton() {
  const btn = $("backfillBtn");
  if (!btn) return;
  const left = backfillState.urls.length;
  if (!left && !backfillState.running) { btn.hidden = true; btn.classList.remove("active"); return; }
  btn.hidden = false;
  btn.classList.toggle("active", backfillState.running && !backfillState.paused);
  if (backfillState.paused) btn.textContent = `继续补抓 ${left}`;
  else if (backfillState.running) btn.textContent = `补抓 ${backfillState.total - left}/${backfillState.total}`;
  else btn.textContent = `补抓 ${left}`;
}

/* ---------- 入队 ---------- */

export function enqueueBackfill(urls, note) {
  const queued = new Set(backfillState.urls);
  const add = urls.filter((u) => u && !queued.has(u) && !backfillState.inflight.has(u));
  if (!add.length) { if (note) setStatus(note); paintButton(); return; }

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
    setStatus(`${note ? note + "；" : ""}自动补抓已暂停（待抓 ${backfillState.urls.length} 条）`);
    paintButton();
    return;
  }
  setStatus(
    `${note ? note + "；" : ""}自动补抓：待抓 ${backfillState.urls.length} 条（${WORKERS} 并发）`,
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
  fetch("/api/records/pending")
    .then((r) => r.json())
    .then((data) => {
      const urls = (data && data.urls) || [];
      /* 有本地列表里还没有的记录 → 先补一次列表再入队：出队复查会把
         「不在 state.records 里」当成已删除跳过，不等 loadRecords 会白跳一轮 */
      const known = new Set(state.records.map((r) => r.url));
      const ready = urls.some((u) => !known.has(u)) ? loadRecords() : Promise.resolve();
      return ready.then(() => enqueueBackfill(urls));
    })
    .then(schedulePoll)
    .catch(() => schedulePoll());   // 服务重启这类瞬时故障：下轮再试，别把轮询断掉
}

function startPolling() {
  if (polling) return;
  polling = true;
  // 切回标签页立刻查一次，不必干等下一个 10 秒
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { clearTimeout(pollTimer); pollPending(); }
  });
  schedulePoll();
}

/* 页面打开时调：扫一遍库里 fetched=0 的（插入顺序 = 后端 ORDER BY rowid），
   并开始轮询，把之后才进库的待抓记录也接进来 */
export function startBackfill(note) {
  backfillState.paused = loadPaused();   // 以持久化的暂停位为准
  const pending = state.records.filter((r) => !r.fetched).map((r) => r.url);
  enqueueBackfill(pending, note);
  startPolling();
}

export function toggleBackfill() {
  backfillState.paused = !backfillState.paused;
  savePaused(backfillState.paused);
  if (backfillState.paused) {
    setStatus(`自动补抓已暂停（剩余 ${backfillState.urls.length} 条）`);
    paintButton();
    return;
  }
  paintButton();
  runBackfill();   // 已有 worker 在收尾时这里会被守卫挡掉，由它们的收口逻辑接着跑
}

/* ---------- 执行 ---------- */

function runBackfill() {
  if (backfillState.running || backfillState.paused) return;
  if (!backfillState.urls.length) { paintButton(); return; }
  backfillState.running = true;

  const worker = async () => {
    while (backfillState.urls.length && !backfillState.paused) {
      const url = backfillState.urls.shift();
      /* 出队复查：这条在排队期间已经被手动「重新抓取」抓过、或已被删除 → 跳过，
         别再发一次请求（手动抓取与队列并发是常态，不是异常） */
      const rec = state.records.find((r) => r.url === url);
      if (!rec || rec.fetched) { backfillState.skip++; paintButton(); continue; }

      const done = backfillState.total - backfillState.urls.length;
      setStatus(
        `补抓 ${done}/${backfillState.total}（成功 ${backfillState.ok}，失败 ${backfillState.fail}）`,
        "busy"
      );
      paintButton();
      backfillState.inflight.add(url);   // 在途：挡住下一次轮询的重复入队
      try {
        const resp = await fetch("/api/fetch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url, proxy: state.globalProxy }),
        });
        const data = await resp.json();
        if (data.ok) {
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
      } catch {
        backfillState.fail++;
      } finally {
        backfillState.inflight.delete(url);
      }
      paintButton();
    }
  };

  Promise.all(Array.from({ length: WORKERS }, () => worker())).then(() => {
    backfillState.running = false;
    if (backfillState.urls.length) {
      if (backfillState.paused) {
        setStatus(`自动补抓已暂停（剩余 ${backfillState.urls.length} 条）`);
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
    const prefix = backfillState.note ? backfillState.note + "；" : "";
    if (!backfillState.ok && !backfillState.fail) {
      setStatus(`${prefix}自动补抓：${backfillState.total} 条此前都已抓过，无需补抓`);
      paintButton();
      return;
    }
    setStatus(
      `${prefix}自动补抓完成：成功 ${backfillState.ok}，失败 ${backfillState.fail}` +
      `${backfillState.skip ? `，跳过 ${backfillState.skip}` : ""}（共 ${backfillState.total} 条）`,
      backfillState.ok ? "" : "err"
    );
    paintButton();
  });
}
