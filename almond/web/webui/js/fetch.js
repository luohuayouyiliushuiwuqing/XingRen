/* 网络动作：添加 URL、抓取/重新抓取单条。 */
import { updateCard } from "./cards.js";
import { openDetail } from "./detail.js";
import { upsert } from "./records.js";
import { $, setStatus, state } from "./state.js";

/* ---------- 在途请求管理（切存储时全部中止；服务端存储纪元作最终兜底） ---------- */

const controllers = new Set();

/** 中止所有在途抓取请求（切存储目录时调用）。 */
export function abortAllFetches() {
  for (const c of controllers) c.abort();
  controllers.clear();
}

/** 统一 POST：注册 AbortController，返回解析后的 JSON。 */
export async function postFetch(path, body) {
  const ctrl = new AbortController();
  controllers.add(ctrl);
  try {
    const resp = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    return await resp.json();
  } finally {
    controllers.delete(ctrl);
  }
}

/* ---------- 抓取（添加 / 重新抓取） ---------- */

export async function fetchRecord(url) {
  const proxy = state.globalProxy;
  $("addBtn").disabled = true;
  setStatus(`正在抓取 ${url} …（三级降级，可能需要数秒到一两分钟）`, "busy");
  try {
    const data = await postFetch("/api/fetch", { url, proxy });
    if (data.stale) {
      // 抓取期间切换了存储：服务端已丢弃结果，本机也绝不入库
      setStatus("存储已切换，本次抓取结果已丢弃");
      return null;
    }
    if (!data.ok) {
      setStatus("抓取失败：" + data.error, "err");
      return null;
    }
    upsert(data.record);
    updateCard(data.record);   // 就地补丁，别整页重绘（会闪）
    if (state.detailTarget && state.detailTarget.url === data.record.url) {
      openDetail(data.record);
    }
    /* 判成败用响应里的 success（本次抓取），别用 data.record.success：
       快照导入的记录抓失败时按合并规则保留旧内容，success 还是入库时的 1，
       拿它判断会把「三级全失败」报成「已抓取」 */
    const fetched = data.success === undefined ? !!data.record.success : data.success;
    setStatus(
      fetched
        ? `已抓取：${data.record.title || url}`
        : `三级抓取全部失败，已仅保存 URL：${url}`,
      fetched ? "" : "err"
    );
    return data.record;
  } catch (e) {
    if (e && e.name === "AbortError") {
      setStatus("抓取已中止（存储目录已切换）");
      return null;
    }
    setStatus("请求失败：" + e.message, "err");
    return null;
  } finally {
    $("addBtn").disabled = false;
  }
}

export function addUrl() {
  const input = $("urlInput");
  let url = input.value.trim();
  if (!url) return;
  if (!/^https?:\/\//i.test(url)) url = "https://" + url;
  input.value = "";
  fetchRecord(url);
}
