/* Raindrop 风格 Web 看板 — 交互逻辑 */

"use strict";

const $ = (id) => document.getElementById(id);

const state = {
  records: [],
  filter: "",
  renameTarget: null,
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
  actions.append(renameBtn, refreshBtn, deleteBtn);
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

function render() {
  const board = $("board");
  board.innerHTML = "";
  const visible = state.records.filter(matchesFilter);
  for (const record of visible) board.appendChild(createCard(record));
  $("emptyHint").hidden = state.records.length > 0;
  setStatus(
    state.filter
      ? `${visible.length} / ${state.records.length} 条匹配`
      : `共 ${state.records.length} 条`
  );
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
      return;
    }
    upsert(data.record);
    render();
    setStatus(
      data.record.success
        ? `已抓取：${data.record.title || url}`
        : `三级抓取全部失败，已仅保存 URL：${url}`,
      data.record.success ? "" : "err"
    );
  } catch (e) {
    setStatus("请求失败：" + e.message, "err");
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

  loadRecords();
}

document.addEventListener("DOMContentLoaded", init);
