/* 域名子页：#/domain/<rootDomain> 路由 + 域头设置 + 卡片/列表双视图。
   - 进子页**不改** state.selectedDomain（看板筛选态与侧栏高亮原样保留）；
     getVisibleRecords() 里加了 domainPage 分支，所以「全选/导出」的口径自动正确。
   - 域头（含设置表单）放在**静态容器 #domainHeader**里，不在 #board 内——
     render() 会 board.innerHTML=""，表单在 #board 里的话，任何一次后台 render
     （标签弹窗关闭、删除、搜索…）都会把用户正打的字冲掉。与 #selBar 同一套路。
   - 视图两态（prefs.view：卡片/列表）：卡片 = 复用 cards.createCard 的网格
     （与看板同款，分片建卡），列表 = .dp-row 行（路径/详情摘要等字段展示）。
     行与卡都带 data-url + .card-check，多选与 7 个批量动作在两种视图下原样可用。 */
import { appendCardsChunked, render, syncCardIntrinsic } from "./board.js";
import { createCard } from "./cards.js";
import { openDetail } from "./detail.js";
import { fetchRecord } from "./fetch.js";
import { openRename, openTag } from "./modals.js";
import {
  loadDomainConfig, loadProxyDomains, loadRecords,
  matchesFilter, matchesTag, removeRecord,
} from "./records.js";
import { $, el, setStatus, state } from "./state.js";

const HASH_PREFIX = "#/domain/";
const DEFAULT_FIELDS = ["url", "tags", "details"];
let settingsOpen = false;     // 设置面板开合（跨 render 保持）

/* ---------- 路由 ---------- */

export function goToDomain(domain) {
  if (!domain) return;
  const next = HASH_PREFIX + encodeURIComponent(domain);
  if (location.hash === next) return;
  location.hash = next;       // hashchange → syncDomainRoute() → render()
}

/* 退回看板：置空状态 + 清 hash，并**自己 render 一次**。
   必须显式 render——置空发生在 hashchange 之前，回声到 syncDomainRoute() 时
   「hash 与 state 一致（都是空）」被判为无变化不渲染，画面会留在子页上（曾经的坑）。
   filterTo 等随后还要再 render 的调用方多渲染一次无妨（同一任务内，只画最后一帧） */
export function leaveDomainPage() {
  if (!state.domainPage) return;
  state.domainPage = null;
  if (location.hash) location.hash = "";
  render();
}

/* 地址栏 → state。返回「是否变化」，调用方据此决定要不要 render；
   幂等（state 与 hash 一致时什么都不做），所以 leave 之后再来的 hashchange 不会重复渲染。
   进入某个域名时套用它存的标签偏好（每域名显示偏好之一）。 */
export function syncDomainRoute() {
  const m = /^#\/domain\/(.+)$/.exec(location.hash || "");
  let next = m ? decodeURIComponent(m[1]) : null;
  if (next && (next === "__other__" || next.length > 100)) next = null;   // 非法 hash 当没有
  if (next === state.domainPage) return false;
  const entering = !!next && !state.domainPage;
  state.domainPage = next;
  if (entering) applyPrefsTag();
  return true;
}

/* 进入子页（或冷启动直接落在子页）时套用该域名存的标签偏好。
   冷启动时 domainConfig 可能还没加载，main.js 的加载链会在它到位后再调一次 */
export function applyPrefsTag() {
  if (!state.domainPage) return;
  state.selectedTag = readPrefs(state.domainConfig.get(state.domainPage)).tag || null;
}

/* ---------- 显示偏好（存 domains.prefs，按域名各自记住） ---------- */

function readPrefs(cfg) {
  const p = (cfg && cfg.prefs) || {};
  return {
    sort: p.sort || "default",
    fields: Array.isArray(p.fields) && p.fields.length ? p.fields : DEFAULT_FIELDS,
    tag: p.tag || "",
    view: p.view === "list" ? "list" : "card",   // 默认卡片（与看板同款）；存过 list 才用列表
  };
}

function savePrefs(domain, prefs) {
  const cfg = state.domainConfig.get(domain) || {};
  cfg.prefs = prefs;
  state.domainConfig.set(domain, cfg);
  fetch("/api/domain", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: domain, prefs }),
  }).then((r) => r.json()).catch(() => { /* 偏好丢了下次再存，不打断操作 */ });
}

function statusRank(r) {
  if (!r.fetched) return 0;      // 从没抓过
  if (!r.success) return 1;      // 抓过但失败
  return 2;                      // 成功
}

const SORTS = {
  default: null,                                  // 入库顺序（后端 ORDER BY rowid）
  title: (a, b) => (a.title || "").localeCompare(b.title || "", "zh"),
  url: (a, b) => a.url.localeCompare(b.url),
  status: (a, b) => statusRank(a) - statusRank(b),
};

/* ---------- 渲染：board.render() 分叉调用 ---------- */

export function renderDomainPage(board, visible) {
  const domain = state.domainPage;
  const cfg = state.domainConfig.get(domain) || {};
  const prefs = readPrefs(cfg);

  fillDomainHeader(domain, cfg, visible.length);

  const cmp = SORTS[prefs.sort];
  const recs = cmp ? visible.slice().sort(cmp) : visible;

  board.appendChild(buildToolbar(domain, prefs));

  if (!recs.length) {
    board.appendChild(el("div", "dp-empty", "该域名下没有符合当前条件的记录"));
    return 0;
  }

  /* 卡片视图：复用 createCard 与看板的分片建卡（每帧 100 张）。
     不需要看板那套占位高度/懒建观察者——子页没有分组，content-visibility 自己会跳帧 */
  if (prefs.view === "card") {
    const grid = el("div", "domain-grid");
    board.appendChild(grid);
    appendCardsChunked(grid, recs);
    syncCardIntrinsic();   // 首片已同步建出，量真实尺寸覆盖 contain-intrinsic-size
    return recs.length;
  }

  const list = el("div", "dp-list");
  for (const r of recs) list.appendChild(buildRow(r, prefs));
  board.appendChild(list);
  return recs.length;
}

/* ---------- 域头（静态容器 #domainHeader） ---------- */

function fillDomainHeader(domain, cfg, shown) {
  const box = $("domainHeader");
  if (!box) return;
  box.hidden = false;

  /* 设置面板开着时**绝不重建表单**——否则用户正打的字会被后台 render 冲掉；
     只刷标题与统计这类纯文本。保存/切换设置前会先清空 innerHTML 强制重建 */
  if (settingsOpen && box.querySelector(".dp-settings")) {
    const t = box.querySelector(".dp-title");
    if (t) t.textContent = domain;
    const s = box.querySelector(".dp-stats");
    if (s) s.textContent = statsText(domain, shown);
    return;
  }

  box.innerHTML = "";
  const head = el("div", "dp-head");

  const back = el("button", "dp-back", "← 返回看板");
  back.addEventListener("click", () => leaveDomainPage());

  const mid = el("div", "dp-head-mid");
  mid.appendChild(el("h2", "dp-title", domain));
  if (cfg.display_name) mid.appendChild(el("span", "dp-alias", `别名：${cfg.display_name}`));
  mid.appendChild(el("div", "dp-stats", statsText(domain, shown)));

  const toggle = el("button", "btn-ghost dp-set-toggle", settingsOpen ? "收起设置" : "域名设置");
  toggle.addEventListener("click", () => {
    settingsOpen = !settingsOpen;
    if (!settingsOpen) box.innerHTML = "";    // 关掉时重建，顺带销毁表单
    render();
  });

  head.append(back, mid, toggle);
  box.appendChild(head);
  if (settingsOpen) box.appendChild(buildSettings(domain, cfg));
}

/* 统计全部来自 state.records（本来就全量在内存，不另开接口） */
function statsText(domain, shown) {
  const all = state.records.filter((r) => r.domain === domain);
  const pending = all.filter((r) => !r.fetched).length;
  const failed = all.filter((r) => r.fetched && !r.success).length;
  const tagNames = new Set();
  for (const r of all) for (const t of r.tags || []) tagNames.add(t.name);
  return `共 ${all.length} 条 · 显示 ${shown} 条 · 待抓 ${pending} · 失败 ${failed} · 标签 ${tagNames.size}`;
}

/* ---------- 域名设置（信息 + 抓取规则，一次 PATCH 提交） ---------- */

function buildSettings(domain, cfg) {
  const box = el("div", "dp-settings");

  const field = (labelText, node, hint) => {
    const wrap = el("div", "dp-field");
    const label = el("label", null, labelText);
    label.appendChild(node);
    wrap.appendChild(label);
    if (hint) wrap.appendChild(el("div", "dp-hint", hint));
    return wrap;
  };

  const alias = el("input", "input");
  alias.value = cfg.display_name || "";
  alias.placeholder = "显示别名，如：深度求索";
  alias.spellcheck = false;

  const proxy = el("select", "input");
  proxy.innerHTML = `<option value="">代理：跟随全局</option>
    <option value="1">代理：强制走代理</option>
    <option value="0">代理：强制直连</option>`;
  proxy.value = cfg.need_proxy === null || cfg.need_proxy === undefined ? "" : (cfg.need_proxy ? "1" : "0");

  const auto = el("select", "input");
  auto.innerHTML = `<option value="">自动补抓：跟随全局</option>
    <option value="1">自动补抓：开</option>
    <option value="0">自动补抓：关（补抓不碰这个域名）</option>`;
  auto.value = cfg.auto_fetch === null || cfg.auto_fetch === undefined ? "" : (cfg.auto_fetch ? "1" : "0");

  const cover = el("select", "input");
  cover.innerHTML = `<option value="">封面：默认（og:image）</option>
    <option value="0">封面：不要封面</option>
    <option value="1">封面：只要图标</option>`;
  cover.value = cfg.cover === null || cfg.cover === undefined ? "" : (cfg.cover ? "1" : "0");

  const selector = el("input", "input");
  selector.value = cfg.detail_selector || "";
  selector.placeholder = "留空用默认 .space-y-2 > *";
  selector.spellcheck = false;

  const save = el("button", "btn-primary", "保存设置");
  save.addEventListener("click", async () => {
    setStatus(`保存 ${domain} 的域名设置…`, "busy");
    const resp = await fetch("/api/domain", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: domain,
        display_name: alias.value.trim(),
        need_proxy: proxy.value === "" ? null : proxy.value === "1",
        auto_fetch: auto.value === "" ? null : auto.value === "1",
        cover: cover.value === "" ? null : cover.value === "1",
        detail_selector: selector.value.trim(),
      }),
    });
    const data = await resp.json();
    if (!data.ok) { setStatus("保存失败：" + data.error, "err"); return; }
    await Promise.all([loadDomainConfig(), loadProxyDomains()]);
    $("domainHeader").innerHTML = "";   // 强制下次重建，表单拿到新值
    setStatus(`已保存 ${domain} 的域名设置`);
    render();
  });

  box.appendChild(field("显示别名", alias));
  box.appendChild(field("代理", proxy));
  box.appendChild(field("自动补抓", auto));
  box.appendChild(field("封面来源", cover,
    "默认取 og:image；「不要封面」抓完后清空封面（显示字母占位），「只要图标」用站点图标当封面。对**之后的重新抓取**生效"));
  box.appendChild(field("详情字段选择器", selector,
    "按站点覆盖默认的 .space-y-2 > *；选择器无效时详情为空（不报错），同样对之后的抓取生效"));

  /* 域名重置：危险操作单独一行 + 二次确认 */
  const resetWrap = el("div", "dp-field");
  const resetLabel = el("label", null, "域名重置（原域名挂了就换到新域名，不可撤销）");
  const line = el("div", "proxy-row");
  const resetInput = el("input", "input");
  resetInput.placeholder = "新域名，如 example.com";
  resetInput.spellcheck = false;
  const resetBtn = el("button", "btn-danger", "替换");
  resetBtn.addEventListener("click", async () => {
    const next = resetInput.value.trim();
    if (!next) { setStatus("请填写新域名", "err"); resetInput.focus(); return; }
    if (!confirm(`确定把 ${domain} 全部替换为 ${next}？\n记录链接、封面、域名规则与代理规则都会改写，不可撤销。`)) return;
    setStatus(`域名重置 ${domain} → ${next}…`, "busy");
    const resp = await fetch("/api/domain/replace", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old: domain, new: next }),
    });
    const data = await resp.json();
    if (!data.ok) { setStatus("域名重置失败：" + data.error, "err"); return; }
    setStatus(
      `域名重置 ${data.old} → ${data.new}：共 ${data.total} 条，成功 ${data.records}，` +
      `重复 ${data.merged}，失败 ${data.failed}` + (data.rules ? `，改写规则 ${data.rules} 条` : "")
    );
    /* 旧域名没了：直接把子页切到新域名（不是退回看板） */
    settingsOpen = false;
    goToDomain(data.new);
    await Promise.all([loadRecords(), loadDomainConfig(), loadProxyDomains()]);
    render();
  });
  line.append(resetInput, resetBtn);
  resetLabel.appendChild(line);
  resetWrap.appendChild(resetLabel);

  const actions = el("div", "dp-settings-actions");
  actions.appendChild(save);
  box.append(resetWrap, actions);
  return box;
}

/* ---------- 工具栏：排序 / 展示字段 / 标签筛选（= 显示偏好，存库） ---------- */

function buildToolbar(domain, prefs) {
  const bar = el("div", "dp-toolbar");

  /* 视图：卡片 / 列表（默认卡片）。切换即入库，且滚回顶部——
     列表滚到深处再切卡片，停在半空中会像没切换成功 */
  const view = el("select", "input dp-sort");
  view.innerHTML = `<option value="card">视图：卡片</option>
    <option value="list">视图：列表</option>`;
  view.value = prefs.view;
  view.addEventListener("change", () => {
    prefs.view = view.value;
    savePrefs(domain, prefs);
    render();
    const wrap = document.querySelector(".board-wrap");
    if (wrap) wrap.scrollTop = 0;
  });
  bar.appendChild(view);

  const sort = el("select", "input dp-sort");
  sort.innerHTML = `<option value="default">排序：默认（入库顺序）</option>
    <option value="title">排序：标题</option>
    <option value="url">排序：链接</option>
    <option value="status">排序：状态（未抓→失败→成功）</option>`;
  sort.value = prefs.sort;
  sort.addEventListener("change", () => {
    prefs.sort = sort.value;
    savePrefs(domain, prefs);
    render();
  });
  bar.appendChild(sort);

  /* 展示字段：只在列表视图有意义（卡片不显示路径/详情摘要）。
     标题恒显，其余三段可关（改动即入库，下次进这个域名还是这套） */
  if (prefs.view === "list") {
    for (const [key, labelText] of [["url", "路径"], ["tags", "标签"], ["details", "详情摘要"]]) {
      const label = el("label", "dp-field-toggle");
      const cb = el("input");
      cb.type = "checkbox";
      cb.checked = prefs.fields.includes(key);
      cb.addEventListener("change", () => {
        prefs.fields = cb.checked
          ? [...new Set([...prefs.fields, key])]
          : prefs.fields.filter((f) => f !== key);
        savePrefs(domain, prefs);
        render();
      });
      label.append(cb, document.createTextNode(labelText));
      bar.appendChild(label);
    }
  }

  /* 标签筛选：子页内改（侧栏点标签会退回看板，两套入口语义不同） */
  const tag = el("select", "input dp-tag");
  const tagNames = new Set();
  for (const r of state.records) for (const t of r.tags || []) tagNames.add(t.name);
  tag.innerHTML = `<option value="">标签：全部</option><option value="__untagged__">标签：未标签</option>` +
    [...tagNames].sort().map((n) => `<option value="${n}">${n}</option>`).join("");
  tag.value = state.selectedTag || "";
  tag.addEventListener("change", () => {
    state.selectedTag = tag.value || null;
    prefs.tag = state.selectedTag || "";
    savePrefs(domain, prefs);
    render();
  });
  bar.appendChild(tag);
  return bar;
}

/* ---------- 记录行 ---------- */

function prettyPath(url) {
  return url.replace(/^https?:\/\/[^/]+/, "") || "/";
}

function buildRow(record, prefs) {
  const row = el("div", "dp-row");
  row.dataset.url = record.url;

  /* 复用卡片复选框的类名与 #board 上的委托监听：多选、#selBar、
     7 个批量动作在子页里全部原样可用（state.selectedUrls 本来就按 url 存） */
  const cb = el("input", "card-check");
  cb.type = "checkbox";
  cb.checked = state.selectedUrls.has(record.url);
  cb.title = record.url;
  if (cb.checked) row.classList.add("selected");
  row.appendChild(cb);

  const dot = el("span", "dot " + (!record.fetched ? "pending" : record.success ? "ok" : "fail"));
  dot.title = !record.fetched ? "还没抓取" : record.success ? "抓取成功" : "抓取失败";
  row.appendChild(dot);

  const main = el("div", "dp-main");
  const link = el("a", "dp-link" + (record.title ? "" : " pending"));
  link.href = record.url;
  link.target = "_blank";
  link.rel = "noopener";
  link.title = record.url;
  link.textContent = record.title || record.url;
  main.appendChild(link);

  const meta = el("div", "dp-meta");
  if (prefs.fields.includes("url")) meta.appendChild(el("span", "dp-path", prettyPath(record.url)));
  if (prefs.fields.includes("tags") && record.tags && record.tags.length) {
    const tags = el("span", "dp-tags");
    for (const t of record.tags) tags.appendChild(el("span", "tag-badge", t.name));
    meta.appendChild(tags);
  }
  if (prefs.fields.includes("details") && record.details && record.details.length) {
    meta.appendChild(el("span", "dp-brief", briefText(record)));
  }
  main.appendChild(meta);
  row.appendChild(main);

  /* 行内操作：与卡片同款 stopPropagation；闭包取 row.__record（补丁会更新它） */
  row.__record = record;
  const acts = el("div", "dp-acts");
  const mk = (labelText, title, fn) => {
    const b = el("button", "dp-act", labelText);
    b.title = title;
    b.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); fn(); });
    return b;
  };
  acts.append(
    mk("详情", "查看详情字段", () => openDetail(row.__record)),
    mk("重抓", "重新抓取这条", () => fetchRecord(row.__record.url)),
    mk("改名", "重命名", () => openRename(row.__record)),
    mk("标签", "给这一条打标签", () => openTag(row.__record)),
    mk("删", "删除这条记录", () => removeRecord(row.__record)),
  );
  row.appendChild(acts);
  return row;
}

function briefText(record) {
  return (record.details || []).slice(0, 2)
    .map((d) => `${d.label}：${d.value || (d.links && d.links[0] && d.links[0].text) || ""}`)
    .join("　·　");
}

/* 抓取完成/改名后的就地补丁（卡片走 cards.updateCard，行走这里）。
   返回 true = 本视图已处理；false = 不在本视图（调用方不必再管） */
export function updateDomainRow(record) {
  const row = document.querySelector(`#board .dp-row[data-url="${CSS.escape(record.url)}"]`);
  if (!row) return false;
  /* 标题改到筛选条件外 → 只能整页重排（与卡片的兜底一致） */
  if (!matchesFilter(record) || !matchesTag(record)) { render(); return true; }
  row.__record = record;
  const link = row.querySelector(".dp-link");
  if (link) {
    link.textContent = record.title || record.url;
    link.classList.toggle("pending", !record.title);
  }
  const dot = row.querySelector(".dot");
  if (dot) {
    dot.className = "dot " + (!record.fetched ? "pending" : record.success ? "ok" : "fail");
    dot.title = !record.fetched ? "还没抓取" : record.success ? "抓取成功" : "抓取失败";
  }
  const brief = row.querySelector(".dp-brief");
  if (brief) brief.textContent = briefText(record);
  return true;
}
