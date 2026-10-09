/* Raindrop 风格 Web 看板 — 交互逻辑 */

"use strict";

import { recomputePendingGrids, render, syncCardIntrinsic } from "./board.js";
import { updateCard } from "./cards.js";
import { closeDetail } from "./detail.js";
import { addUrl, fetchRecord } from "./fetch.js";
import { doExport, importFromFile } from "./io.js";
import { addNewTag, submitRename } from "./modals.js";
import { addRule, detectProxy, openProxyPanel, saveDomain, saveGlobalProxy } from "./proxy-panel.js";
import { getVisibleRecords, loadProxyDomains, loadRecords } from "./records.js";
import { buildSidebar } from "./sidebar.js";
import { $, setStatus, state } from "./state.js";
import { fsState, loadFs, openDbPanel, saveDbPath } from "./storage.js";


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

/* 控制台调试句柄：模块内函数不再挂 window，排查时从这里取 */
window.XR = { buildSidebar, fetchRecord, getVisibleRecords, loadRecords, render, setStatus, state, updateCard };
