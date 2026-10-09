/* 数据层：加载、合并、筛选可见记录、代理域名集合、删除。 */
import { render } from "./board.js";
import { $, MIN_GROUP, hostOf, rootDomain, setStatus, state } from "./state.js";

/* ---------- 数据加载与渲染 ---------- */

export async function loadRecords() {
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

export function upsert(record) {
  const i = state.records.findIndex((r) => r.url === record.url);
  if (i >= 0) state.records[i] = record;
  else state.records.unshift(record);
}

export function matchesFilter(record) {
  if (!state.filter) return true;
  const hay = `${record.title || ""} ${record.url}`.toLowerCase();
  return hay.includes(state.filter);
}

/* ---------- 当前可见记录（搜索 + 域名 + 标签过滤，render 与导出共用） ---------- */

export function getVisibleRecords() {
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

export async function loadProxyDomains() {
  try {
    const data = await (await fetch("/api/proxy-domains")).json();
    state.proxyDomains = new Set(data.domains || []);
  } catch (e) {
    state.proxyDomains = new Set();
  }
  render();
}

/* ---------- 删除 ---------- */

export async function removeRecord(record) {
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
