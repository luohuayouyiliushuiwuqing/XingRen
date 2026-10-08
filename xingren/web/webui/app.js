/* Raindrop 风格 Web 看板 — 交互逻辑 */

"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  records: [],
  filter: "",
  selectedDomain: null,    // null = "全部"，字符串 = 选中的域名
  selectedGroup: null,     // null = 该域名下全部，字符串 = 选中的子分组
  renameTarget: null,
  groupTarget: null,       // 分组弹窗的目标记录
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
  const groupBtn = el("button", null, "分组");
  groupBtn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    openGroup(record);
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
  actions.append(detailBtn, renameBtn, groupBtn, refreshBtn, deleteBtn);
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
  if (record.group_name) domainRow.appendChild(el("span", "group-badge", record.group_name));
  body.append(title, domainRow);

  card.append(thumb, body);
  return card;
}

/* ---------- 侧边栏 ---------- */

function buildSidebar() {
  const sb = $("sidebar");
  sb.innerHTML = "";

  /* "全部" 项 */
  const allActive = state.selectedDomain === null;
  const all = el("div", "sidebar-item" + (allActive ? " active" : ""));
  all.innerHTML = `<span>全部</span><span class="sidebar-count">${state.records.length}</span>`;
  all.addEventListener("click", () => { state.selectedDomain = null; state.selectedGroup = null; render(); });
  sb.appendChild(all);

  /* 按域名 → 子分组两级分组 */
  const domainMap = new Map();  // domain → Map(group → [records])
  for (const r of state.records) {
    const d = rootDomain(hostOf(r.url)) || "unknown";
    const g = r.group_name || "未分组";
    if (!domainMap.has(d)) domainMap.set(d, new Map());
    const gMap = domainMap.get(d);
    if (!gMap.has(g)) gMap.set(g, []);
    gMap.get(g).push(r);
  }
  const sortedDomains = [...domainMap.entries()].sort((a, b) => {
    const sum = (m) => [...m.values()].reduce((s, arr) => s + arr.length, 0);
    return sum(b[1]) - sum(a[1]);
  });

  for (const [domain, gMap] of sortedDomains) {
    const domainTotal = [...gMap.values()].reduce((s, arr) => s + arr.length, 0);
    const domainActive = state.selectedDomain === domain && state.selectedGroup === null;
    const domainItem = el("div", "sidebar-item sidebar-domain" + (domainActive ? " active" : ""));
    domainItem.innerHTML = `<span>${domain}</span><span class="sidebar-count">${domainTotal}</span>`;
    domainItem.addEventListener("click", () => { state.selectedDomain = domain; state.selectedGroup = null; render(); });
    sb.appendChild(domainItem);

    /* 子分组（仅当选中该域名时展开显示） */
    if (state.selectedDomain === domain) {
      const sortedGroups = [...gMap.entries()].sort((a, b) => b[1].length - a[1].length);
      for (const [group, recs] of sortedGroups) {
        const groupActive = state.selectedGroup === group;
        const sub = el("div", "sidebar-item sidebar-sub" + (groupActive ? " active" : ""));
        sub.innerHTML = `<span>${group}</span><span class="sidebar-count">${recs.length}</span>`;
        sub.addEventListener("click", () => { state.selectedGroup = group; render(); });
        sb.appendChild(sub);
      }
    }
  }
}

/* ---------- 渲染 ---------- */

function render() {
  const board = $("board");
  board.innerHTML = "";
  buildSidebar();

  const visible = state.records.filter(matchesFilter);

  if (state.selectedDomain && state.selectedGroup) {
    /* 选中子分组 → 平铺 */
    const filtered = visible.filter(r =>
      rootDomain(hostOf(r.url)) === state.selectedDomain &&
      (r.group_name || "未分组") === state.selectedGroup
    );
    for (const r of filtered) board.appendChild(createCard(r));
    $("emptyHint").hidden = filtered.length > 0;
    setStatus(`共 ${state.records.length} 条，${state.selectedDomain} / ${state.selectedGroup} (${filtered.length})`);

  } else if (state.selectedDomain) {
    /* 选中域名 → 按子分组分组 */
    const domainRecs = visible.filter(r => rootDomain(hostOf(r.url)) === state.selectedDomain);
    const gMap = new Map();
    for (const r of domainRecs) {
      const g = r.group_name || "未分组";
      (gMap.get(g) ?? gMap.set(g, []).get(g)).push(r);
    }
    const sorted = [...gMap.entries()].sort((a, b) => b[1].length - a[1].length);

    for (const [group, recs] of sorted) {
      const section = document.createElement("div");
      section.className = "domain-group";
      const header = document.createElement("div");
      header.className = "domain-header";
      header.innerHTML = `<span class="domain-toggle">▸</span><span class="domain-name">${group}</span><span class="domain-count">${recs.length}</span>`;
      const grid = document.createElement("div");
      grid.className = "domain-grid";
      for (const r of recs) grid.appendChild(createCard(r));
      section.append(header, grid);
      if (recs.length > 20) section.classList.add("collapsed");
      header.addEventListener("click", () => section.classList.toggle("collapsed"));
      board.appendChild(section);
    }
    $("emptyHint").hidden = domainRecs.length > 0;
    setStatus(`共 ${state.records.length} 条，${state.selectedDomain} (${domainRecs.length})`);

  } else {
    /* 全部 → 域名 → 子分组两级分组 */
    const domainMap = new Map();
    for (const r of visible) {
      const d = rootDomain(hostOf(r.url)) || "unknown";
      if (!domainMap.has(d)) domainMap.set(d, new Map());
      const g = r.group_name || "未分组";
      const gMap = domainMap.get(d);
      if (!gMap.has(g)) gMap.set(g, []);
      gMap.get(g).push(r);
    }
    const sortedDomains = [...domainMap.entries()].sort((a, b) => {
      const sum = (m) => [...m.values()].reduce((s, arr) => s + arr.length, 0);
      return sum(b[1]) - sum(a[1]);
    });

    if (sortedDomains.length <= 1 && sortedDomains[0] && [...sortedDomains[0][1]].length <= 1) {
      /* 单域名单子分组：直接平铺 */
      for (const r of visible) board.appendChild(createCard(r));
    } else {
      for (const [domain, gMap] of sortedDomains) {
        const domainTotal = [...gMap.values()].reduce((s, arr) => s + arr.length, 0);
        const section = document.createElement("div");
        section.className = "domain-group";
        const header = document.createElement("div");
        header.className = "domain-header";
        header.innerHTML = `<span class="domain-toggle">▸</span><span class="domain-name">${domain}</span><span class="domain-count">${domainTotal}</span>`;

        const sortedGroups = [...gMap.entries()].sort((a, b) => b[1].length - a[1].length);
        const content = document.createElement("div");
        content.className = "domain-content";

        if (sortedGroups.length === 1 && sortedGroups[0][0] === "未分组") {
          /* 单子分组（未分组）：不显示子分组头，直接平铺 */
          const grid = document.createElement("div");
          grid.className = "domain-grid";
          for (const r of sortedGroups[0][1]) grid.appendChild(createCard(r));
          content.appendChild(grid);
        } else {
          for (const [group, recs] of sortedGroups) {
            const subHeader = document.createElement("div");
            subHeader.className = "sub-group-header";
            subHeader.textContent = `${group} (${recs.length})`;
            const grid = document.createElement("div");
            grid.className = "domain-grid";
            for (const r of recs) grid.appendChild(createCard(r));
            content.append(subHeader, grid);
          }
        }

        section.append(header, content);
        if (domainTotal > 20) section.classList.add("collapsed");
        header.addEventListener("click", () => section.classList.toggle("collapsed"));
        board.appendChild(section);
      }
    }

    $("emptyHint").hidden = visible.length > 0;
    setStatus(
      state.filter
        ? `${visible.length} / ${state.records.length} 条匹配，${sortedDomains.length} 个域名`
        : `共 ${state.records.length} 条，${sortedDomains.length} 个域名`
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

/* ---------- 分组 ---------- */

function openGroup(record) {
  state.groupTarget = record;
  $("groupInput").value = record.group_name || "";
  $("groupMask").hidden = false;
  $("groupInput").focus();
}

async function submitGroup() {
  const record = state.groupTarget;
  if (!record) return;
  const group_name = $("groupInput").value.trim();
  $("groupMask").hidden = true;
  if (group_name === (record.group_name || "")) return;
  try {
    const resp = await fetch("/api/record", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: record.url, group_name }),
    });
    const data = await resp.json();
    if (data.ok) {
      record.group_name = data.record.group_name;
      render();
      setStatus(`已${group_name ? "归入「" + group_name + "」" : "取消分组"}`);
    } else {
      setStatus("分组失败：" + data.error, "err");
    }
  } catch (e) {
    setStatus("分组失败：" + e.message, "err");
  } finally {
    state.groupTarget = null;
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

/* ---------- TXT 导入 ---------- */

function parseUrls(text, fileName) {
  if (/\.html?$/i.test(fileName)) {
    /* HTML（Netscape Bookmark 格式）：提取所有 <A HREF="..."> */
    const doc = new DOMParser().parseFromString(text, "text/html");
    return [...doc.querySelectorAll("a[href]")].map(a => a.href).filter(u => /^https?:\/\//i.test(u));
  }
  /* TXT：一行一个 URL */
  return text.split(/\r?\n/).map(l => l.trim()).filter(l => /^https?:\/\//i.test(l));
}

async function importFromFile(file) {
  const text = await file.text();
  const urls = [...new Set(parseUrls(text, file.name))];  // 去重
  if (!urls.length) { setStatus("文件中未找到 http 开头的 URL", "err"); return; }

  const proxy = $("proxyInput").value.trim();
  const existing = new Set(state.records.map(r => r.url));
  const toFetch = urls.filter(u => !existing.has(u));
  const skipped = urls.length - toFetch.length;

  $("importBtn").disabled = true;
  let done = 0, ok = 0, fail = 0;
  const CONCURRENCY = 5;

  async function worker() {
    while (done < toFetch.length) {
      const i = done++;
      setStatus(`导入中 ${i + 1}/${toFetch.length}（成功 ${ok}，失败 ${fail}，跳过 ${skipped}）`, "busy");
      try {
        const resp = await fetch("/api/fetch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: toFetch[i], proxy }),
        });
        const data = await resp.json();
        if (data.ok) { upsert(data.record); ok++; }
        else fail++;
      } catch { fail++; }
      render();
    }
  }

  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, toFetch.length) }, () => worker()));

  $("importBtn").disabled = false;
  const parts = [`共 ${urls.length} 条`];
  if (ok) parts.push(`成功 ${ok}`);
  if (fail) parts.push(`失败 ${fail}`);
  if (skipped) parts.push(`跳过 ${skipped}（已存在）`);
  setStatus(`导入完成：${parts.join("，")}`, ok > 0 ? "" : "err");
}

/* ---------- 事件绑定 ---------- */

function init() {
  $("proxyInput").value = "http://127.0.0.1:7897";

  $("sidebarToggle").addEventListener("click", () => {
    $("sidebar").classList.toggle("collapsed");
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

  $("groupOk").addEventListener("click", submitGroup);
  $("groupCancel").addEventListener("click", () => {
    $("groupMask").hidden = true;
    state.groupTarget = null;
  });
  $("groupInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitGroup();
    if (e.key === "Escape") $("groupCancel").click();
  });
  $("groupMask").addEventListener("click", (e) => {
    if (e.target === $("groupMask")) $("groupCancel").click();
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
