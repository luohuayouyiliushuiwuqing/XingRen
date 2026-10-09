/* Raindrop 风格 Web 看板 — 交互逻辑 */

"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  records: [],
  filter: "",
  selectedDomain: null,    // null = "全部"，字符串 = 选中的域名 / "__other__"（零散域名合并项）
  selectedTag: null,       // null = "全部"，字符串 = 选中的标签
  sidebarExpanded: false,  // 侧栏超出部分是否已通过「更多」展开
  globalProxy: "http://127.0.0.1:7897",  // 全局代理，在「代理」面板里编辑
  proxyDomains: new Set(), // 最终会走代理的域名（侧边栏标记用）
  renameTarget: null,
  tagTarget: null,         // 标签弹窗的目标记录
  domainTarget: null,      // 域名管理弹窗的目标域名
  detailTarget: null,
};

/* 单域名少于该条数：侧边栏与看板分组都收进「其他」类别 */
const MIN_GROUP = 10;

function setStatus(msg, kind) {
  const el = $("statusText");
  el.parentElement.className = "statusbar" + (kind ? " " + kind : "");
  el.textContent = msg;
}

function hostOf(url) {
  try {
    return new URL(url).host || url;
  } catch (e) {
    return url;
  }
}

/* 多段公共后缀：co.uk / com.cn / com.au … —— 必须与后端 records._MULTI_TLDS 一致，
   否则前端分组与后端域名代理规则对不上（bar.co.uk 会被截成 co.uk） */
const MULTI_TLDS = [
  ".co.uk", ".org.uk", ".ac.uk", ".gov.uk", ".me.uk",
  ".com.cn", ".net.cn", ".org.cn", ".gov.cn", ".edu.cn",
  ".co.jp", ".ne.jp", ".or.jp", ".ac.jp",
  ".com.au", ".net.au", ".org.au",
  ".co.nz", ".com.hk", ".com.tw", ".com.sg",
  ".com.br", ".com.mx", ".co.kr", ".co.in", ".com.ar",
];

/* 提取可注册域名：chat.deepseek.com → deepseek.com，bar.co.uk → bar.co.uk */
function rootDomain(host) {
  const h = (host || "").toLowerCase();
  for (const tld of MULTI_TLDS) {
    if (h.endsWith(tld)) {
      const head = h.slice(0, -tld.length);
      return head ? head.split(".").pop() + tld : h;
    }
  }
  const parts = h.split(".");
  return parts.length <= 2 ? h : parts.slice(-2).join(".");
}

/* ---------- 数据加载与渲染 ---------- */

async function loadRecords() {
  try {
    const data = await (await fetch("/api/records")).json();
    if (data.error) { // 服务端异常（如存储目录不存在）：原样提示
      setStatus("加载失败：" + data.error, "err");
      return;
    }
    state.records = data.records || [];
    render();
    setStatus(`共 ${state.records.length} 条`);
  } catch (e) {
    setStatus("加载失败：" + e.message, "err");
  }
}

function upsert(record) {
  const i = state.records.findIndex((r) => r.url === record.url);
  if (i >= 0) state.records[i] = record;
  else state.records.unshift(record);
}

function matchesFilter(record) {
  if (!state.filter) return true;
  const hay = `${record.title || ""} ${record.url}`.toLowerCase();
  return hay.includes(state.filter);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function createCard(record) {
  const card = el("div", "card");
  const host = hostOf(record.url);
  const pending = !record.title;   // 没标题（TXT 快照/抓取未完成）就灰着，别装作有标题
  const proxy = state.globalProxy;
  const imgSrc = (u) =>
    "/api/img?src=" + encodeURIComponent(u) +
    (proxy ? "&proxy=" + encodeURIComponent(proxy) : "");

  /* 缩略图：图 → 字母占位；可选 favicon 角标（图片走服务端代理加载）。
     打开链接用内部 <a>，操作按钮是它的兄弟节点，避免点按钮时触发跳转。 */
  const thumb = el("div", "thumb");
  const open = el("a", "open");
  open.href = record.url;
  open.target = "_blank";
  open.rel = "noopener";
  open.title = record.url;
  open.appendChild(el("span", "letter", host.slice(0, 1).toUpperCase()));
  if (record.thumbnail) {
    const img = new Image();
    img.className = "cover";
    img.loading = "lazy";
    img.src = imgSrc(record.thumbnail);
    img.onerror = () => img.remove();
    open.appendChild(img);
  }
  if (record.favicon && record.favicon.startsWith("http")) {
    const fav = new Image();
    fav.className = "favicon";
    fav.loading = "lazy";
    fav.src = imgSrc(record.favicon);
    fav.onerror = () => fav.remove();
    open.appendChild(fav);
  }
  thumb.appendChild(open);

  /* 右上角快捷按钮（详情 / 重新抓取） */
  const topActions = el("div", "thumb-actions");
  const detailBtn = el("button", null, "详情");
  detailBtn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); openDetail(record); });
  const refreshBtn = el("button", null, "重新抓取");
  refreshBtn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); fetchRecord(record.url); });
  topActions.append(detailBtn, refreshBtn);
  thumb.appendChild(topActions);

  /* 底部操作条（重命名 / 标签 / 删除） */
  const actions = el("div", "actions");
  const renameBtn = el("button", null, "重命名");
  renameBtn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); openRename(record); });
  const tagBtn = el("button", null, "标签");
  tagBtn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); openTag(record); });
  const deleteBtn = el("button", null, "删除");
  deleteBtn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); removeRecord(record); });
  actions.append(renameBtn, tagBtn, deleteBtn);
  thumb.appendChild(actions);

  /* 文本区 */
  const body = el("div", "body");
  const title = el(
    "a",
    "title" + (pending ? " pending" : ""),
    record.title || "（标题待补充）"
  );
  title.href = record.url;
  title.target = "_blank";
  title.rel = "noopener";
  const domainRow = el("div", "domain");
  const dot = !record.fetched ? "pending" : (record.success ? "ok" : "fail");
  domainRow.appendChild(el("span", "dot " + dot));
  domainRow.appendChild(el("span", null, host));
  if (record.tags && record.tags.length) {
    for (const t of record.tags) domainRow.appendChild(el("span", "tag-badge", t.name));
  }
  body.append(title, domainRow);

  card.append(thumb, body);
  return card;
}

/* ---------- 侧边栏 ---------- */

function buildSidebar() {
  const sb = $("sidebar");
  const keepScroll = sb.scrollTop;   // 展开态重绘（如代理标记到达）不丢滚动位置
  sb.innerHTML = "";
  sb.classList.remove("expanded");   // 先按未展开测量，避免滚动条宽度干扰

  const allActive = state.selectedDomain === null && state.selectedTag === null;
  const all = el("div", "sidebar-item" + (allActive ? " active" : ""));
  all.innerHTML = `<span>全部</span><span class="sidebar-count">${state.records.length}</span>`;
  all.addEventListener("click", () => { state.selectedDomain = null; state.selectedTag = null; render(); });
  sb.appendChild(all);

  /* ── 域名（少于 MIN_GROUP 条的零散域名收进「其他」） ── */
  sb.appendChild(el("div", "sidebar-label", "域名"));
  const domainCounts = new Map();
  for (const r of state.records) {
    const d = rootDomain(hostOf(r.url)) || "unknown";
    domainCounts.set(d, (domainCounts.get(d) || 0) + 1);
  }
  const sortedDomains = [...domainCounts.entries()].sort((a, b) => b[1] - a[1]);

  const proxyItem = (label, count, active, onClick, badgeTitle) => {
    const proxied = state.proxyDomains.has(label);
    const item = el("div", "sidebar-item" + (active ? " active" : "") + (proxied ? " proxy" : ""));
    let inner = `<span>${label}</span>`;
    if (proxied) inner += `<span class="proxy-badge" title="${badgeTitle || "匹配代理规则：走代理"}">代理</span>`;
    inner += `<span class="sidebar-count">${count}</span>`;
    item.innerHTML = inner;
    item.addEventListener("click", onClick);
    sb.appendChild(item);
    return item;
  };

  let minorTotal = 0;
  let minorProxied = 0;
  const minorNames = [];
  for (const [domain, count] of sortedDomains) {
    if (count < MIN_GROUP) {
      minorNames.push(domain);
      minorTotal += count;
      if (state.proxyDomains.has(domain)) minorProxied++;
      continue;
    }
    const active = state.selectedDomain === domain;
    proxyItem(domain, count, active, () => {
      state.selectedDomain = active ? null : domain;
      render();
    });
  }
  if (minorNames.length) {
    const active = state.selectedDomain === "__other__";
    // 「其他」本身不在 proxyDomains 里：有任一成员走代理就打标
    const proxied = minorProxied > 0;
    const item = el("div", "sidebar-item" + (active ? " active" : "") + (proxied ? " proxy" : ""));
    let inner = `<span>其他</span>`;
    if (proxied) {
      inner += `<span class="proxy-badge" title="包含 ${minorProxied} 个走代理的域名">代理</span>`;
    }
    inner += `<span class="sidebar-count">${minorTotal}</span>`;
    item.innerHTML = inner;
    item.addEventListener("click", () => {
      state.selectedDomain = active ? null : "__other__";
      render();
    });
    sb.appendChild(item);
  }

  /* ── 标签 ── */
  sb.appendChild(el("div", "sidebar-label", "标签"));
  const tagCounts = new Map();
  let untagged = 0;
  for (const r of state.records) {
    if (!r.tags || !r.tags.length) { untagged++; continue; }
    for (const t of r.tags) tagCounts.set(t.name, (tagCounts.get(t.name) || 0) + 1);
  }
  const sortedTags = [...tagCounts.entries()].sort((a, b) => b[1] - a[1]);
  for (const [name, count] of sortedTags) {
    const active = state.selectedTag === name;
    const item = el("div", "sidebar-item" + (active ? " active" : ""));
    item.innerHTML = `<span>${name}</span><span class="sidebar-count">${count}</span>`;
    item.addEventListener("click", () => { state.selectedTag = active ? null : name; render(); });
    sb.appendChild(item);
  }
  if (untagged) {
    const active = state.selectedTag === "__untagged__";
    const item = el("div", "sidebar-item" + (active ? " active" : ""));
    item.innerHTML = `<span>未标签</span><span class="sidebar-count">${untagged}</span>`;
    item.addEventListener("click", () => { state.selectedTag = active ? null : "__untagged__"; render(); });
    sb.appendChild(item);
  }

  applySidebarOverflow(sb);
  if (sb.classList.contains("expanded")) sb.scrollTop = keepScroll;
}

/* 侧栏固定高度、不随滚轮滚动：装不下的条目收进底部「更多」，点开才整列展开。
   展开态改用侧栏自身滚动条（overflow-y: auto），滚轮此时作用于侧栏。
   高度用 offsetTop 实测——它相对定位后的侧栏 padding 盒，已含各元素外边距。 */
let sidebarOverflowTop = 0;   // 首个被收起条目的位置，展开后滚到这里

function applySidebarOverflow(sb) {
  const kids = [...sb.children];
  if (!kids.length) return;

  const cs = getComputedStyle(sb);
  const padBottom = parseFloat(cs.paddingBottom) || 0;
  const probe = sb.querySelector(".sidebar-item");
  const rowH = probe ? probe.offsetHeight : 33;
  // 内容区底边（相对侧栏 border 盒），再减一行留给「更多」
  const limit = Math.max(sb.clientHeight - padBottom - rowH, 0);

  let split = kids.length;
  for (let i = 0; i < kids.length; i++) {
    if (kids[i].offsetTop + kids[i].offsetHeight > limit) { split = i; break; }
  }
  if (split >= kids.length) return;            // 装得下，无需「更多」
  sidebarOverflowTop = kids[split].offsetTop;

  const more = el("div", "sidebar-more");
  if (state.sidebarExpanded) {
    sb.classList.add("expanded");
    more.innerHTML = `<span>收起</span><span class="arrow">▴</span>`;
    more.addEventListener("click", () => { state.sidebarExpanded = false; buildSidebar(); });
  } else {
    for (let i = split; i < kids.length; i++) kids[i].hidden = true;
    more.innerHTML = `<span>更多 (${kids.length - split})</span><span class="arrow">▾</span>`;
    more.addEventListener("click", () => {
      state.sidebarExpanded = true;
      buildSidebar();
      sb.scrollTop = sidebarOverflowTop;   // 直接落在刚展开的位置
    });
  }
  sb.appendChild(more);
}

/* ---------- 当前可见记录（搜索 + 域名 + 标签过滤，render 与导出共用） ---------- */

function getVisibleRecords() {
  let visible = state.records.filter(matchesFilter);
  if (state.selectedDomain === "__other__") {
    /* 零散域名合并项：按全量计数 < MIN_GROUP 的域名成员过滤 */
    const counts = new Map();
    for (const r of state.records) {
      const d = rootDomain(hostOf(r.url)) || "unknown";
      counts.set(d, (counts.get(d) || 0) + 1);
    }
    const minorSet = new Set(
      [...counts.entries()].filter(([, c]) => c < MIN_GROUP).map(([d]) => d)
    );
    visible = visible.filter(r => minorSet.has(rootDomain(hostOf(r.url)) || "unknown"));
  } else if (state.selectedDomain) {
    visible = visible.filter(r => rootDomain(hostOf(r.url)) === state.selectedDomain);
  }
  if (state.selectedTag) {
    if (state.selectedTag === "__untagged__") {
      visible = visible.filter(r => !r.tags || !r.tags.length);
    } else {
      visible = visible.filter(r => r.tags && r.tags.some(t => t.name === state.selectedTag));
    }
  }
  return visible;
}

/* ---------- 走代理域名（侧边栏标记数据源） ---------- */

async function loadProxyDomains() {
  try {
    const data = await (await fetch("/api/proxy-domains")).json();
    state.proxyDomains = new Set(data.domains || []);
  } catch (e) {
    state.proxyDomains = new Set();
  }
  render();
}

/* ---------- 渲染 ---------- */

/* 分组分片建卡片：每帧 100 张，既不卡顿也不会一次建出全部节点。
   onDone 在**全部建完**时调用——补建期间占位高度一直留着，
   高度不随分片增长而变，滚动位置才不会被顶走 */
function appendCardsChunked(grid, recs, start = 0, onDone) {
  const CHUNK = 100;
  const end = Math.min(start + CHUNK, recs.length);
  for (let i = start; i < end; i++) grid.appendChild(createCard(recs[i]));
  if (end < recs.length) {
    requestAnimationFrame(() => {
      if (grid.isConnected) appendCardsChunked(grid, recs, end, onDone);
    });
  } else if (onDone) {
    onDone();
  }
}

/* ---------- 分组卡片按需建：只建视口附近的分组 ---------- */
const CARD_MIN = 240;      // style.css 的 --card-w
const CARD_GAP = 16;       // .board-grid / .domain-grid 的 gap
const CARD_BODY_H = 86.37; // 卡片正文（标题 2 行 + 域名行）实测高度
let groupObserver = null;

/* 所有分组网格同宽，所以**只读一次 clientWidth**：读取会强制重排，
   在循环里逐个「读宽 + 写 minHeight」等于把整块看板重排几十遍 */
function gridMetrics() {
  const g = document.querySelector("#board .domain-grid, #board .board-grid");
  const w = g ? g.clientWidth : 0;
  if (!w) return null;
  const cols = Math.max(1, Math.floor((w + CARD_GAP) / (CARD_MIN + CARD_GAP)));
  const colW = (w - (cols - 1) * CARD_GAP) / cols;
  return { cols, rowH: (colW - 2) * 9 / 16 + CARD_BODY_H };
}

/* 未建卡片的分组先按公式占位高度：缩略图 16:9 + 固定正文。
   三个视口实测与真实值一致（1280→220.81 / 1440→243.31 / 1920→232.63），
   占准了滚动条才不会在补建时跳动 */
function reserveHeight(grid, recs, m) {
  if (!recs.length || !m) return;
  const rows = Math.ceil(recs.length / m.cols);
  grid.dataset.pending = "1";
  grid.style.minHeight = Math.round(rows * m.rowH + (rows - 1) * CARD_GAP) + "px";
}

/* 被 content-visibility 跳过的卡片按 contain-intrinsic-size 占位，而这个值随列宽变
   （1280→218.81 / 1440→241.31 / 1920→230.63，都是**内容盒**）。写死会与真实渲染尺寸
   不一致 → 每行差 1~22px、几十行累计上千像素，占位高度和自然高度对不上。
   这里取一张**视口内**卡片的实测值覆盖；跳过的卡片报告的是估算值，不能当基准 */
let cardIntrinsicKey = "";

function syncCardIntrinsic() {
  let hit = null;
  for (const c of document.querySelectorAll("#board .card")) {
    const r = c.getBoundingClientRect();
    if (r.bottom > 0 && r.top < innerHeight && r.width && r.height) { hit = c; break; }
  }
  if (!hit) return;
  const key = `${hit.clientWidth}x${hit.clientHeight}`;
  if (key === cardIntrinsicKey) return;
  cardIntrinsicKey = key;
  let tag = document.getElementById("cardIntrinsic");
  if (!tag) {
    tag = document.createElement("style");
    tag.id = "cardIntrinsic";
    document.head.appendChild(tag);
  }
  tag.textContent =
    `.card{contain-intrinsic-size:auto ${hit.clientWidth}px auto ${hit.clientHeight}px}`;
}

function fillGrid(grid) {
  if (!grid || grid.dataset.pending !== "1" || grid.dataset.filling === "1") return;
  grid.dataset.filling = "1";
  appendCardsChunked(grid, grid.__recs || [], 0, () => {
    delete grid.dataset.filling;
    delete grid.dataset.pending;
    grid.style.minHeight = "";   // 建完才撤占位：此时自然高度 == 占位高度
  });
  syncCardIntrinsic();           // 首片卡片已同步建出，可以量真实尺寸
}

/* 侧栏宽度 / 窗口尺寸变了 → 列数与行高随之变化，占位要跟着重算（量一次、写一批） */
function recomputePendingGrids() {
  const grids = document.querySelectorAll('[data-pending="1"]');
  if (!grids.length) return;
  const m = gridMetrics();
  for (const g of grids) reserveHeight(g, g.__recs || [], m);
}

function resetGroupObserver() {
  if (groupObserver) groupObserver.disconnect();
  groupObserver = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        if (!e.isIntersecting || e.target.classList.contains("collapsed")) continue;
        groupObserver.unobserve(e.target);   // 折叠中的先留着，等分组头点开再建
        fillGrid(e.target.__grid);
      }
    },
    { root: document.querySelector(".board-wrap"), rootMargin: "800px 0px" }
  );
}

/* 同一视图内重渲（重新抓取完成、改名、删除、代理标记到达……）必须**保住滚动位置**：
   board.innerHTML="" 会把 .board-wrap 的 scrollTop 压到 0，重建后就停在最上面。
   筛选条件变了（搜索词 / 域名 / 标签）则另当别论——换视图从头看，回顶部 */
let lastViewKey = null;

function render() {
  const board = $("board");
  const wrap = document.querySelector(".board-wrap");
  const prevScroll = wrap ? wrap.scrollTop : 0;
  const viewKey = [state.filter, state.selectedDomain, state.selectedTag].join(" ");
  const sameView = viewKey === lastViewKey;
  lastViewKey = viewKey;

  board.innerHTML = "";
  buildSidebar();

  const visible = getVisibleRecords();

  /* 按域名分组显示 */
  let domainMap = new Map();
  for (const r of visible) {
    const d = rootDomain(hostOf(r.url)) || "unknown";
    (domainMap.get(d) ?? domainMap.set(d, []).get(d)).push(r);
  }

  /* 少于 MIN_GROUP 条的零散域名收拢成一个「其他」类别；
     已筛选到单一域名时不收拢（否则该域名自己会被吞进「其他」）；
     「__other__」视图本身就是零散域名集合，照常收拢成一组 */
  if (!state.selectedDomain || state.selectedDomain === "__other__") {
    const major = new Map();
    const minor = [];
    for (const [d, recs] of domainMap) {
      if (recs.length < MIN_GROUP) minor.push(...recs);
      else major.set(d, recs);
    }
    if (minor.length) major.set("其他", minor);
    domainMap = major;
  }

  const sortedDomains = [...domainMap.entries()].sort((a, b) => b[1].length - a[1].length);

  /* 分组结构默认展开，但**卡片不一次性建完**：只给分组头 + 占位高度，
     滚到视口附近（rootMargin 800px）才补建该组卡片——
     855 条（或导入 5000 条）时首屏只建看得见的那几百个节点 */
  resetGroupObserver();
  if (sortedDomains.length <= 1 && visible.length <= 20) {
    // 平铺也包一层多列网格：.board 是纵向 flex，直接塞卡片会排成一列
    const grid = document.createElement("div");
    grid.className = "board-grid";
    for (const r of visible) grid.appendChild(createCard(r));
    board.appendChild(grid);
  } else {
    const groups = [];
    for (const [domain, recs] of sortedDomains) {
      const section = document.createElement("div");
      section.className = "domain-group";
      const header = document.createElement("div");
      header.className = "domain-header";
      header.innerHTML = `<span class="domain-toggle">▸</span><span class="domain-name">${domain}</span><span class="domain-count">${recs.length}</span>`;
      const grid = document.createElement("div");
      grid.className = "domain-grid";
      section.append(header, grid);
      section.__grid = grid;
      board.appendChild(section);

      header.addEventListener("click", () => {
        const opening = section.classList.contains("collapsed");
        if (opening) {                  // 手动展开：立即补建，不等滚动
          groupObserver.unobserve(section);
          fillGrid(grid);
        }
        section.classList.toggle("collapsed");
      });
      groups.push({ section, grid, recs });
    }

    /* 分组全部入 DOM 后**统一量一次**宽度，再批量写占位高度——
       占位先于建卡，卡片等滚到视口附近（rootMargin 800px）再补 */
    const m = gridMetrics();
    for (const { section, grid, recs } of groups) {
      grid.__recs = recs;
      reserveHeight(grid, recs, m);
      groupObserver.observe(section);
    }
  }

  $("emptyHint").hidden = visible.length > 0;
  const parts = [`共 ${state.records.length} 条`];
  if (state.selectedDomain) {
    parts.push(state.selectedDomain === "__other__" ? "其他" : state.selectedDomain);
  }
  if (state.selectedTag && state.selectedTag !== "__untagged__") parts.push(state.selectedTag);
  if (state.selectedTag === "__untagged__") parts.push("未标签");
  parts.push(`${visible.length} 条显示`);
  setStatus(parts.join("，"));

  /* 回到原滚动位置：此刻所有分组的占位高度都已写好，scrollHeight 是最终值 */
  if (wrap) wrap.scrollTop = sameView ? prevScroll : 0;
  syncCardIntrinsic();   // 平铺分支此刻已有卡片；分组分支由 fillGrid 触发
}

/* ---------- 抓取（添加 / 重新抓取） ---------- */

async function fetchRecord(url) {
  const proxy = state.globalProxy;
  $("addBtn").disabled = true;
  setStatus(`正在抓取 ${url} …（三级降级，可能需要数秒到一两分钟）`, "busy");
  try {
    const resp = await fetch("/api/fetch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, proxy }),
    });
    const data = await resp.json();
    if (!data.ok) {
      setStatus("抓取失败：" + data.error, "err");
      return null;
    }
    upsert(data.record);
    render();
    if (state.detailTarget && state.detailTarget.url === data.record.url) {
      openDetail(data.record);
    }
    setStatus(
      data.record.success
        ? `已抓取：${data.record.title || url}`
        : `三级抓取全部失败，已仅保存 URL：${url}`,
      data.record.success ? "" : "err"
    );
    return data.record;
  } catch (e) {
    setStatus("请求失败：" + e.message, "err");
    return null;
  } finally {
    $("addBtn").disabled = false;
  }
}

function addUrl() {
  const input = $("urlInput");
  let url = input.value.trim();
  if (!url) return;
  if (!/^https?:\/\//i.test(url)) url = "https://" + url;
  input.value = "";
  fetchRecord(url);
}

/* ---------- 重命名 ---------- */

function openRename(record) {
  state.renameTarget = record;
  $("modalInput").value = record.title || "";
  $("modalMask").hidden = false;
  $("modalInput").focus();
}

async function submitRename() {
  const record = state.renameTarget;
  if (!record) return;
  const title = $("modalInput").value.trim();
  $("modalMask").hidden = true;
  if (title === (record.title || "")) return;
  try {
    const resp = await fetch("/api/record", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: record.url, title }),
    });
    const data = await resp.json();
    if (data.ok) {
      record.title = title;
      render();
      setStatus(`已重命名：${title || "(空)"}`);
    } else {
      setStatus("重命名失败：" + data.error, "err");
    }
  } catch (e) {
    setStatus("重命名失败：" + e.message, "err");
  } finally {
    state.renameTarget = null;
  }
}

/* ---------- 标签管理 ---------- */

async function openTag(record) {
  state.tagTarget = record;
  await renderTagModal();
  $("tagMask").hidden = false;
}

async function renderTagModal() {
  const record = state.tagTarget;
  const box = $("tagList");
  box.innerHTML = "";
  try {
    const resp = await fetch("/api/tags");
    const data = await resp.json();
    const tags = data.tags || [];
    const recordTagIds = new Set((record.tags || []).map(t => t.id));
    for (const tag of tags) {
      const item = el("div", "tag-item" + (recordTagIds.has(tag.id) ? " active" : ""));
      item.innerHTML = `<span>${tag.name}</span><span class="tag-toggle">${recordTagIds.has(tag.id) ? "✓" : "+"}</span>`;
      item.addEventListener("click", async () => {
        if (recordTagIds.has(tag.id)) {
          await fetch(`/api/record/tag?url=${encodeURIComponent(record.url)}&tag_id=${tag.id}`, { method: "DELETE" });
        } else {
          await fetch("/api/record/tag", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url: record.url, tag_id: tag.id }),
          });
        }
        // 刷新记录数据
        const recResp = await fetch("/api/records");
        const recData = await recResp.json();
        state.records = recData.records || [];
        render();
        renderTagModal();
      });
      box.appendChild(item);
    }
  } catch { box.textContent = "加载标签失败"; }
}

async function addNewTag() {
  const name = $("tagInput").value.trim();
  if (!name) return;
  $("tagInput").value = "";
  await fetch("/api/tag", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  renderTagModal();
}

/* ---------- 域名管理 ---------- */

function openDomain(domainName) {
  state.domainTarget = domainName;
  $("domainTitle").textContent = `域名管理：${domainName}`;
  // 从 API 获取域名信息
  fetch("/api/domains").then(r => r.json()).then(data => {
    const domain = (data.domains || []).find(d => d.name === domainName);
    $("domainNameInput").value = domain?.display_name || "";
    const np = domain?.need_proxy;   // null=跟随全局, true/false
    $("domainNeedSelect").value = np === null || np === undefined ? "" : (np ? "1" : "0");
  });
  $("domainMask").hidden = false;
}

async function saveDomain() {
  const name = state.domainTarget;
  if (!name) return;
  const display_name = $("domainNameInput").value.trim();
  const sel = $("domainNeedSelect").value;
  const need_proxy = sel === "" ? null : sel === "1";   // "" → null = 无规则
  $("domainMask").hidden = true;
  try {
    await fetch("/api/domain", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, display_name, need_proxy }),
    });
    render();
    loadProxyDomains(); // 域名规则变化 → 刷新侧边栏代理标记
    const label = need_proxy === null ? "跟随全局" : (need_proxy ? "用代理" : "直连");
    setStatus(`已更新域名 ${name}（${label}）`);
  } catch (e) {
    setStatus("域名更新失败：" + e.message, "err");
  }
  state.domainTarget = null;
}

/* ---------- 代理映射面板 ---------- */

async function openProxyPanel() {
  $("globalProxyInput").value = state.globalProxy;
  await renderProxyPanel();
  $("proxyMask").hidden = false;
}

async function renderProxyPanel() {
  /* URL 模式规则 */
  const ruleBox = $("ruleList");
  ruleBox.innerHTML = "";
  try {
    const data = await (await fetch("/api/proxy-rules")).json();
    for (const rule of data.rules || []) {
      const row = el("div", "rule-row");
      row.appendChild(el("span", "rule-pattern", rule.pattern));
      row.appendChild(el("span", "rule-proxy" + (rule.need_proxy ? "" : " direct"),
        rule.need_proxy ? "用代理" : "直连"));
      const del = el("button", "rule-del", "删除");
      del.addEventListener("click", async () => {
        await fetch(`/api/proxy-rule?id=${rule.id}`, { method: "DELETE" });
        renderProxyPanel();
        loadProxyDomains(); // 规则删除 → 刷新侧边栏代理标记
      });
      row.appendChild(del);
      ruleBox.appendChild(row);
    }
    if (!(data.rules || []).length) ruleBox.appendChild(el("div", "rule-empty", "暂无规则"));
  } catch { ruleBox.textContent = "加载失败"; }

  /* 域名规则 */
  const domBox = $("domainProxyList");
  domBox.innerHTML = "";
  try {
    const data = await (await fetch("/api/domains")).json();
    const withRule = (data.domains || []).filter(d => d.need_proxy !== null);
    for (const d of withRule) {
      const row = el("div", "rule-row");
      const nameBtn = el("span", "rule-pattern link", d.name);
      nameBtn.addEventListener("click", () => { $("proxyMask").hidden = true; openDomain(d.name); });
      row.appendChild(nameBtn);
      row.appendChild(el("span", "rule-proxy" + (d.need_proxy ? "" : " direct"),
        d.need_proxy ? "用代理" : "直连"));
      row.appendChild(el("span", "rule-note", "点域名可编辑"));
      domBox.appendChild(row);
    }
    if (!withRule.length) domBox.appendChild(el("div", "rule-empty", "暂无域名级规则（都在跟随全局）"));
  } catch { domBox.textContent = "加载失败"; }
}

async function saveGlobalProxy() {
  const val = $("globalProxyInput").value.trim();
  state.globalProxy = val;
  setStatus(val ? `全局代理已设为 ${val}` : "已清除全局代理");
}

/* 自动探测本地代理端口（7889-7899）；detectOnly 时只返回不写状态 */
async function detectProxy({ fill = false, announce = true } = {}) {
  try {
    const data = await (await fetch("/api/proxy-detect", { method: "POST" })).json();
    const found = data.proxy || "";
    if (found) {
      if (fill) {
        state.globalProxy = found;
        const input = $("globalProxyInput");
        if (input) input.value = found;
      }
      if (announce) setStatus(`检测到本地代理：${found}`);
    } else if (announce) {
      setStatus("7889-7899 区间未发现本地代理", "err");
    }
    return found;
  } catch (e) {
    if (announce) setStatus("代理探测失败：" + e.message, "err");
    return "";
  }
}

async function addRule() {
  const pattern = $("rulePatternInput").value.trim();
  const need_proxy = $("ruleNeedSelect").value === "1";
  if (!pattern) { setStatus("请填写匹配模式", "err"); return; }
  $("rulePatternInput").value = "";
  await fetch("/api/proxy-rule", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pattern, need_proxy }),
  });
  renderProxyPanel();
  loadProxyDomains(); // 模式规则变化 → 刷新侧边栏代理标记
  setStatus(`已添加规则：${pattern} → ${need_proxy ? "用代理" : "直连"}`);
}

/* ---------- 详情弹层（展示 .space-y-2 提取的标签值字段） ---------- */

async function openDetail(record) {
  state.detailTarget = record;
  $("detailMask").hidden = false;
  if (record.fetched) { renderDetail(record); return; }

  /* 快照导入的记录没有 details：打开详情时才补抓（按需加载） */
  renderDetailLoading();
  await fetchRecord(record.url);   // 内部 upsert 会替换 state.records 里的对象
  const fresh = state.records.find(r => r.url === record.url) || record;
  if (state.detailTarget) {
    state.detailTarget = fresh;
    renderDetail(fresh);           // 不递归：抓取失败也只提示一次，再点才重试
  }
}

function renderDetailLoading() {
  const box = $("detailFields");
  box.innerHTML = "";
  box.appendChild(el("div", "detail-empty", "正在抓取详情…（首次需要数秒）"));
}

function renderDetail(record) {
  $("detailTitle").textContent = record.title || "（标题待补充）";
  $("detailDomain").textContent = record.url;
  const box = $("detailFields");
  box.innerHTML = "";

  if (!record.fetched) {
    box.appendChild(el("div", "detail-empty", "详情尚未抓取，点「重新抓取」或再次打开本弹层"));
    return;
  }
  if (!record.success) {
    box.appendChild(el("div", "detail-empty", "该记录抓取失败，点「重新抓取」再试"));
    return;
  }
  const details = record.details || [];
  if (!details.length) {
    box.appendChild(el("div", "detail-empty", "页面中未提取到 .space-y-2 详情字段，点「重新抓取」更新"));
    return;
  }
  for (const f of details) {
    const row = el("div", "detail-row");
    row.appendChild(el("span", "detail-label", f.label));
    const value = el("span", "detail-value");
    // 日期类字段直接显示纯文本（2026-09-15），不用徽标样式
    const dateText = f.datetime ? f.datetime.slice(0, 10) : "";
    if (f.links && f.links.length) {
      for (const l of f.links) {
        const a = document.createElement("a");
        a.textContent = l.text || l.href;
        a.href = l.href;
        a.target = "_blank";
        a.rel = "noopener";
        a.title = l.href;
        value.appendChild(a);
      }
      const rest = f.value || dateText;
      if (rest) value.appendChild(document.createTextNode(" " + rest));
    } else if (f.value) {
      value.appendChild(document.createTextNode(f.value));
    } else if (dateText) {
      value.appendChild(document.createTextNode(dateText));
    }
    row.appendChild(value);
    box.appendChild(row);
  }
}

function closeDetail() {
  $("detailMask").hidden = true;
  state.detailTarget = null;
}

/* ---------- 删除 ---------- */

async function removeRecord(record) {
  const label = record.title || record.url;
  if (!confirm(`确定删除「${label}」？`)) return;
  try {
    const resp = await fetch("/api/record?url=" + encodeURIComponent(record.url), {
      method: "DELETE",
    });
    const data = await resp.json();
    if (data.ok) {
      state.records = state.records.filter((r) => r.url !== record.url);
      render();
      setStatus(`已删除：${label}`);
    } else {
      setStatus("删除失败：" + data.error, "err");
    }
  } catch (e) {
    setStatus("删除失败：" + e.message, "err");
  }
}

/* ---------- 导出 ---------- */

function escHtml(s) {
  return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function downloadFile(fileName, content, mime) {
  const blob = new Blob([content], { type: mime });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(a.href);
}

function exportTxt(records) {
  const text = records.map(r => r.url).join("\n") + "\n";
  downloadFile("bookmarks.txt", text, "text/plain;charset=utf-8");
}

function exportHtml(records) {
  const now = Math.floor(Date.now() / 1000);
  const lines = records.map(r => {
    const tags = (r.tags || []).map(t => t.name).join(",");
    const tagAttr = tags ? ` TAGS="${escHtml(tags)}"` : "";
    return `    <DT><A HREF="${escHtml(r.url)}" ADD_DATE="${now}"${tagAttr}>${escHtml(r.title || r.url)}</A>`;
  });
  const html = [
    "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
    '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
    "<TITLE>Bookmarks</TITLE>",
    "<H1>Bookmarks</H1>",
    "<DL><p>",
    ...lines,
    "</DL><p>",
    "",
  ].join("\n");
  downloadFile("bookmarks.html", html, "text/html;charset=utf-8");
}

function doExport(format) {
  const records = getVisibleRecords();
  if (!records.length) { setStatus("当前没有可导出的记录", "err"); return; }
  if (format === "txt") exportTxt(records);
  else if (format === "html") exportHtml(records);
  setStatus(`已导出 ${records.length} 条为 ${format.toUpperCase()}`);
}

/* ---------- TXT 导入 ---------- */

/* 解析导入文件 → {isHtml, items}。
   HTML：读出 HREF + 标题 + 封面 + 标签，可完全离线快照导入。
   TXT ：只有 URL，无元数据可读，仍需走抓取流程。 */
function parseBookmarks(text, fileName) {
  const seen = new Set();
  const items = [];
  const push = (item) => {
    if (!/^https?:\/\//i.test(item.url) || seen.has(item.url)) return;
    seen.add(item.url);
    items.push(item);
  };

  if (/\.html?$/i.test(fileName)) {
    const doc = new DOMParser().parseFromString(text, "text/html");
    for (const a of doc.querySelectorAll("a[href]")) {
      push({
        url: a.href,
        title: (a.textContent || "").replace(/\s+/g, " ").trim(),
        thumbnail: a.getAttribute("data-cover") || "",
        favicon: "",
        tags: (a.getAttribute("tags") || "").split(",").map(s => s.trim()).filter(Boolean),
      });
    }
    return { isHtml: true, items };
  }

  for (const line of text.split(/\r?\n/)) push({ url: line.trim() });
  return { isHtml: false, items };
}

/* ---------- 导入阶段二：后台提取信息（链接已全部入库，这里只补标题/详情） ---------- */
const extractQueue = { urls: [], total: 0, ok: 0, fail: 0, running: false };
let extractRenderTimer = 0;

function scheduleExtractRender() {
  // 一条一渲会把几千条的看板拖垮：限流到每 500ms 最多一次
  if (extractRenderTimer) return;
  extractRenderTimer = setTimeout(() => { extractRenderTimer = 0; render(); }, 500);
}

function enqueueExtract(urls, note) {
  const queued = new Set(extractQueue.urls);
  const add = urls.filter((u) => u && !queued.has(u));
  if (!add.length) { setStatus(note || "没有需要提取的链接"); return; }
  if (!extractQueue.running) {
    extractQueue.total = extractQueue.ok = extractQueue.fail = 0;  // 新一轮重新计数
  }
  extractQueue.urls.push(...add);
  extractQueue.total += add.length;
  setStatus(
    `${note ? note + "；" : ""}待提取 ${extractQueue.total - extractQueue.ok - extractQueue.fail} 条`,
    "busy"
  );
  runExtract();
}

async function runExtract() {
  if (extractQueue.running) return;
  extractQueue.running = true;

  const worker = async () => {
    while (extractQueue.urls.length) {
      const url = extractQueue.urls.shift();
      const done = extractQueue.total - extractQueue.urls.length;
      setStatus(
        `提取信息 ${done}/${extractQueue.total}（成功 ${extractQueue.ok}，失败 ${extractQueue.fail}）`,
        "busy"
      );
      try {
        const resp = await fetch("/api/fetch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url, proxy: state.globalProxy }),
        });
        const data = await resp.json();
        if (data.ok) { upsert(data.record); extractQueue.ok++; }
        else extractQueue.fail++;
      } catch {
        extractQueue.fail++;
      }
      scheduleExtractRender();
    }
  };

  await Promise.all(Array.from({ length: 5 }, () => worker()));
  extractQueue.running = false;
  if (extractQueue.urls.length) { runExtract(); return; }  // 等待期间又入了新任务
  render();
  setStatus(
    `提取完成：成功 ${extractQueue.ok}，失败 ${extractQueue.fail}（共 ${extractQueue.total} 条）`,
    extractQueue.ok ? "" : "err"
  );
}

/* ---------- 导入：先把链接全部入库，再提取信息 ---------- */
async function importFromFile(file) {
  const text = await file.text();
  const { isHtml, items } = parseBookmarks(text, file.name);
  if (!items.length) { setStatus("文件中未找到 http 开头的 URL", "err"); return; }

  // 必须在入库前取快照，才能知道哪些是这次新增的
  const existing = new Set(state.records.map((r) => r.url));
  const fresh = items.filter((i) => !existing.has(i.url));

  $("importBtn").disabled = true;

  /* ── 阶段一：链接全部入库（快照，不联网，5000 条秒级）── */
  let data;
  try {
    setStatus(`导入链接中…（${items.length} 条，不联网）`, "busy");
    const resp = await fetch("/api/records/quick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    });
    data = await resp.json();
    if (!data.ok) { setStatus("导入失败：" + data.error, "err"); return; }
  } catch (e) {
    setStatus("导入失败：" + e.message, "err");
    return;
  } finally {
    $("importBtn").disabled = false;   // 链接一入库界面立刻可用，不等提取
  }

  await loadRecords();
  const parts = [`已入库 ${data.inserted} 条`];
  if (data.skipped) parts.push(`跳过 ${data.skipped} 条（已存在）`);

  /* ── 阶段二：再提取信息 ──
     TXT 只有 URL，不抓就没有标题 → 后台队列逐条补，不阻塞看板、可继续操作；
     HTML 的标题/封面文件里已有，详情字段仍按打开时按需抓（5000 条全抓不划算） */
  if (isHtml) {
    setStatus(`${parts.join("，")}；标题/封面已从文件读取，详情字段打开时按需抓取`);
    return;
  }
  enqueueExtract(fresh.map((i) => i.url), parts.join("，"));
}

/* ---------- 存储目录（数据库、缓存等本地私有数据的统一存放处） ---------- */
const fsState = { path: "", parent: "", storage: "" };

function fsJoin(dir, name) {
  if (!dir) return name;
  const sep = dir.includes("\\") ? "\\" : "/";
  return /[\\\/]$/.test(dir) ? dir + name : dir + sep + name;
}

async function openDbPanel() {
  $("dbMask").hidden = false;
  $("fsPicker").hidden = true;
  $("dbCurrent").textContent = "加载中…";
  try {
    const d = await (await fetch("/api/storage-dir")).json();
    fsState.storage = d.path;
    $("dbCurrent").textContent =
      `目录: ${d.path}\n数据库: ${d.db_path}\n图片缓存: ${d.cache_dir}`;
    $("dbCurrent").style.whiteSpace = "pre-line";
    if (d.exists === false) {
      $("dbCurrent").textContent +=
        "\n⚠ 目录不存在（可能已被移动或删除）——在下方重新选择一个目录，或输入同一路径点保存以重建";
      $("dbCurrent").classList.add("missing");
      setStatus("存储目录不存在：" + d.path, "err");
    } else {
      $("dbCurrent").classList.remove("missing");
    }
  } catch (e) {
    $("dbCurrent").textContent = "读取失败：" + e.message;
  }
  $("dbPathInput").value = "";
}

async function loadFs(path) {
  const box = $("fsList");
  $("fsPicker").hidden = false;
  $("fsPath").textContent = path || "（选择盘符 / 根目录）";
  box.innerHTML = '<div class="fs-empty">加载中…</div>';
  try {
    const d = await (
      await fetch("/api/fs/list?path=" + encodeURIComponent(path))
    ).json();
    if (!d.ok) {
      box.innerHTML = "";
      box.appendChild(el("div", "fs-empty", d.error || "读取失败"));
      return;
    }
    fsState.path = d.path;
    fsState.parent = d.parent;
    $("fsPath").textContent = d.path || "（选择盘符 / 根目录）";
    $("fsUp").disabled = !d.parent;
    box.innerHTML = "";
    if (!d.entries.length) {
      box.appendChild(el("div", "fs-empty", "（无子目录）"));
      return;
    }
    for (const name of d.entries) {
      const btn = el("button", "fs-item", name);
      btn.addEventListener("click", () => loadFs(fsJoin(d.path, name)));
      box.appendChild(btn);
    }
  } catch (e) {
    box.innerHTML = "";
    box.appendChild(el("div", "fs-empty", "读取失败：" + e.message));
  }
}

async function saveDbPath() {
  const path = $("dbPathInput").value.trim();
  if (!path) {
    setStatus("请先选择或输入存储目录", "err");
    return;
  }
  $("dbSave").disabled = true;
  setStatus("正在迁移存储目录（数据库 + 缓存）…", "busy");
  try {
    const resp = await fetch("/api/storage-dir", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, migrate: true }),
    });
    const data = await resp.json();
    if (!data.ok) {
      setStatus("切换失败：" + data.error, "err");
      return;
    }
    fsState.storage = data.path;
    $("dbCurrent").textContent =
      `目录: ${data.path}\n数据库: ${data.db_path}\n图片缓存: ${data.cache_dir}`;
    $("dbPathInput").value = "";
    $("fsPicker").hidden = true;
    await loadRecords(); // 从新存储目录重新加载
    setStatus(
      `存储目录已切换到 ${data.path}` +
        (data.db_used_existing
          ? "（已直接使用该目录现有的数据库）"
          : data.migrated
            ? "（数据与缓存已迁移）"
            : "") +
        `，共 ${data.records} 条`
    );
  } catch (e) {
    setStatus("切换失败：" + e.message, "err");
  } finally {
    $("dbSave").disabled = false;
  }
}

/* ---------- 事件绑定 ---------- */

function init() {
  $("sidebarToggle").addEventListener("click", () => {
    $("sidebar").classList.toggle("collapsed");
    // 宽度过渡结束后列数才定下来，届时同步卡片实测尺寸 + 重算占位高度
    clearTimeout(window.__sbReflowTimer);
    window.__sbReflowTimer = setTimeout(() => {
      syncCardIntrinsic();
      recomputePendingGrids();
    }, 240);
  });

  /* 滚轮落在侧边栏上时转发给右侧看板滚动——侧边栏自身固定不滑动。
     用 document 捕获阶段监听：先于一切默认滚动行为执行，防止被子元素干扰。
     「更多」展开后侧栏自己能滚（内容已超出一屏），此时不拦截，交还原生滚动。 */
  document.addEventListener(
    "wheel",
    (e) => {
      const sb = $("sidebar");
      if (!sb || sb.classList.contains("collapsed") || !sb.contains(e.target)) return;
      if (state.sidebarExpanded) return;   // 展开态：滚轮交给侧栏
      e.preventDefault();
      const wrap = document.querySelector(".board-wrap");
      if (wrap) wrap.scrollTop += e.deltaY;
    },
    { capture: true, passive: false }
  );

  /* 窗口尺寸变了 → 重测侧栏可见条目（否则缩小后底部条目被裁掉且没有「更多」），
     同时按新列数重算未建分组的占位高度 */
  let resizeTimer = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      buildSidebar();
      syncCardIntrinsic();
      recomputePendingGrids();
    }, 150);
  });

  $("addBtn").addEventListener("click", addUrl);
  $("urlInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") addUrl();
  });
  $("importBtn").addEventListener("click", () => $("fileInput").click());
  $("fileInput").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) importFromFile(file);
    e.target.value = "";  // 允许重复选同一文件
  });
  $("exportSelect").addEventListener("change", (e) => {
    const format = e.target.value;
    e.target.value = "";  // 复位，允许重复导出同格式
    if (format) doExport(format);
  });
  $("searchInput").addEventListener("input", (e) => {
    state.filter = e.target.value.trim().toLowerCase();
    render();
  });

  $("modalOk").addEventListener("click", submitRename);
  $("modalCancel").addEventListener("click", () => {
    $("modalMask").hidden = true;
    state.renameTarget = null;
  });
  $("modalInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitRename();
    if (e.key === "Escape") $("modalCancel").click();
  });
  $("modalMask").addEventListener("click", (e) => {
    if (e.target === $("modalMask")) $("modalCancel").click();
  });

  $("tagAddBtn").addEventListener("click", addNewTag);
  $("tagInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") addNewTag();
  });
  $("tagClose").addEventListener("click", () => {
    $("tagMask").hidden = true;
    state.tagTarget = null;
  });
  $("tagMask").addEventListener("click", (e) => {
    if (e.target === $("tagMask")) $("tagClose").click();
  });

  $("domainSave").addEventListener("click", saveDomain);
  $("domainCancel").addEventListener("click", () => {
    $("domainMask").hidden = true;
    state.domainTarget = null;
  });
  $("domainMask").addEventListener("click", (e) => {
    if (e.target === $("domainMask")) $("domainCancel").click();
  });

  $("proxyPanelBtn").addEventListener("click", openProxyPanel);
  $("proxyClose").addEventListener("click", () => { $("proxyMask").hidden = true; });
  $("proxyMask").addEventListener("click", (e) => {
    if (e.target === $("proxyMask")) $("proxyMask").hidden = true;
  });
  $("globalProxySave").addEventListener("click", saveGlobalProxy);
  $("proxyDetectBtn").addEventListener("click", () => detectProxy({ fill: true }));
  $("ruleAddBtn").addEventListener("click", addRule);
  $("rulePatternInput").addEventListener("keydown", (e) => { if (e.key === "Enter") addRule(); });

  $("dbPanelBtn").addEventListener("click", openDbPanel);
  $("dbSave").addEventListener("click", saveDbPath);
  $("dbPathInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveDbPath();
  });
  $("dbBrowse").addEventListener("click", () => loadFs(fsState.path || fsState.storage || ""));
  $("fsUp").addEventListener("click", () => {
    if (fsState.parent) loadFs(fsState.parent);
  });
  $("fsPick").addEventListener("click", () => {
    if (fsState.path) {
      $("dbPathInput").value = fsState.path;
      $("fsPicker").hidden = true;
    }
  });
  $("dbClose").addEventListener("click", () => { $("dbMask").hidden = true; });
  $("dbMask").addEventListener("click", (e) => {
    if (e.target === $("dbMask")) $("dbClose").click();
  });

  $("detailClose").addEventListener("click", closeDetail);
  $("detailRefresh").addEventListener("click", async () => {
    const target = state.detailTarget;
    if (!target) return;
    $("detailRefresh").disabled = true;
    try {
      await fetchRecord(target.url);
    } finally {
      $("detailRefresh").disabled = false;
    }
  });
  $("detailMask").addEventListener("click", (e) => {
    if (e.target === $("detailMask")) closeDetail();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("detailMask").hidden) closeDetail();
  });

  loadRecords();
  loadProxyDomains(); // 先拿到走代理域名集合，回来后自动 render 打标
  /* 页面加载时自动探测本地代理端口；探测到就采纳（未探测到保持原配置） */
  detectProxy({ fill: true, announce: false });
}

document.addEventListener("DOMContentLoaded", init);
