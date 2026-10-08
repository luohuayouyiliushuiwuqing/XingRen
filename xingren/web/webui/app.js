/* Raindrop 风格 Web 看板 — 交互逻辑 */

"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  records: [],
  filter: "",
  selectedDomain: null,    // null = "全部"，字符串 = 选中的域名
  renameTarget: null,
  detailTarget: null,
};

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

/* 提取可注册域名：chat.deepseek.com → deepseek.com，www.example.com.cn → example.com.cn */
function rootDomain(host) {
  const parts = host.split(".");
  if (parts.length <= 2) return host;
  const last2 = parts.slice(-2).join(".");
  /* 多段后缀：.com.cn / .net.cn / .org.cn / .co.uk 等 → 取最后 3 段 */
  if (/^(com|net|org|gov|edu)\.\w{2}$/.test(last2)) return parts.slice(-3).join(".");
  return last2;
}

/* ---------- 数据加载与渲染 ---------- */

async function loadRecords() {
  try {
    const data = await (await fetch("/api/records")).json();
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
  const pending = !record.success && !record.title;
  const proxy = $("proxyInput").value.trim();
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

  /* 悬停操作条 */
  const actions = el("div", "actions");
  const detailBtn = el("button", null, "详情");
  detailBtn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    openDetail(record);
  });
  const renameBtn = el("button", null, "重命名");
  renameBtn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    openRename(record);
  });
  const refreshBtn = el("button", null, "重新抓取");
  refreshBtn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    fetchRecord(record.url);
  });
  const deleteBtn = el("button", null, "删除");
  deleteBtn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    removeRecord(record);
  });
  actions.append(detailBtn, renameBtn, refreshBtn, deleteBtn);
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
  domainRow.appendChild(el("span", "dot " + (record.success ? "ok" : "fail")));
  domainRow.appendChild(el("span", null, host));
  body.append(title, domainRow);

  card.append(thumb, body);
  return card;
}

/* ---------- 侧边栏 ---------- */

function buildSidebar() {
  const sb = $("sidebar");
  sb.innerHTML = "";

  /* "全部" 项 */
  const all = el("div", "sidebar-item" + (state.selectedDomain === null ? " active" : ""));
  all.innerHTML = `<span>全部</span><span class="sidebar-count">${state.records.length}</span>`;
  all.addEventListener("click", () => { state.selectedDomain = null; render(); });
  sb.appendChild(all);

  /* 按可注册域名分组 */
  const groups = new Map();
  for (const r of state.records) {
    const d = rootDomain(hostOf(r.url)) || "unknown";
    (groups.get(d) ?? groups.set(d, []).get(d)).push(r);
  }
  const sorted = [...groups.entries()].sort((a, b) => b[1].length - a[1].length);

  for (const [domain, recs] of sorted) {
    const item = el("div", "sidebar-item" + (state.selectedDomain === domain ? " active" : ""));
    item.innerHTML = `<span>${domain}</span><span class="sidebar-count">${recs.length}</span>`;
    item.addEventListener("click", () => { state.selectedDomain = domain; render(); });
    sb.appendChild(item);
  }
}

/* ---------- 渲染 ---------- */

function render() {
  const board = $("board");
  board.innerHTML = "";
  buildSidebar();

  const visible = state.records.filter(matchesFilter);

  if (state.selectedDomain) {
    /* 选中某域名 → 平铺该域名的记录 */
    const filtered = visible.filter(r => rootDomain(hostOf(r.url)) === state.selectedDomain);
    for (const r of filtered) board.appendChild(createCard(r));
    $("emptyHint").hidden = filtered.length > 0;
    setStatus(`共 ${state.records.length} 条，选中 ${state.selectedDomain} (${filtered.length})`);
  } else {
    /* "全部" → 域名分组视图 */
    const groups = new Map();
    for (const r of visible) {
      const d = rootDomain(hostOf(r.url)) || "unknown";
      (groups.get(d) ?? groups.set(d, []).get(d)).push(r);
    }
    const sorted = [...groups.entries()].sort((a, b) => b[1].length - a[1].length);

    if (sorted.length <= 1) {
      for (const r of visible) board.appendChild(createCard(r));
    } else {
      for (const [domain, recs] of sorted) {
        const section = document.createElement("div");
        section.className = "domain-group";
        const header = document.createElement("div");
        header.className = "domain-header";
        header.innerHTML = `<span class="domain-toggle">▸</span><span class="domain-name">${domain}</span><span class="domain-count">${recs.length}</span>`;
        const grid = document.createElement("div");
        grid.className = "domain-grid";
        for (const r of recs) grid.appendChild(createCard(r));
        section.append(header, grid);

        const collapsed = recs.length > 20;
        if (collapsed) section.classList.add("collapsed");
        header.addEventListener("click", () => section.classList.toggle("collapsed"));

        board.appendChild(section);
      }
    }

    $("emptyHint").hidden = visible.length > 0;
    setStatus(
      state.filter
        ? `${visible.length} / ${state.records.length} 条匹配，${sorted.length} 个域名`
        : `共 ${state.records.length} 条，${sorted.length} 个域名`
    );
  }
}

/* ---------- 抓取（添加 / 重新抓取） ---------- */

async function fetchRecord(url) {
  const proxy = $("proxyInput").value.trim();
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

/* ---------- 详情弹层（展示 .space-y-2 提取的标签值字段） ---------- */

function openDetail(record) {
  state.detailTarget = record;
  renderDetail(record);
  $("detailMask").hidden = false;
}

function renderDetail(record) {
  $("detailTitle").textContent = record.title || "（标题待补充）";
  $("detailDomain").textContent = record.url;
  const box = $("detailFields");
  box.innerHTML = "";

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

/* ---------- 事件绑定 ---------- */

function init() {
  $("proxyInput").value = "http://127.0.0.1:7892";

  $("sidebarToggle").addEventListener("click", () => {
    $("sidebar").classList.toggle("collapsed");
  });

  $("addBtn").addEventListener("click", addUrl);
  $("urlInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") addUrl();
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
}

document.addEventListener("DOMContentLoaded", init);
