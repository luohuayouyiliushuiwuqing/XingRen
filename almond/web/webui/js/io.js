/* 导入（两阶段：快照入库→后台提取）与导出。 */
import { updateCard } from "./cards.js";
import { getVisibleRecords, loadRecords, upsert } from "./records.js";
import { $, setStatus, state } from "./state.js";

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

export function doExport(format) {
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
        if (data.ok) {
          upsert(data.record);
          extractQueue.ok++;
          // 就地补丁：批量提取期间不整页重绘（原先每 500ms 闪一次）
          updateCard(data.record);
        } else extractQueue.fail++;
      } catch {
        extractQueue.fail++;
      }
    }
  };

  await Promise.all(Array.from({ length: 5 }, () => worker()));
  extractQueue.running = false;
  if (extractQueue.urls.length) { runExtract(); return; }  // 等待期间又入了新任务
  setStatus(
    `提取完成：成功 ${extractQueue.ok}，失败 ${extractQueue.fail}（共 ${extractQueue.total} 条）`,
    extractQueue.ok ? "" : "err"
  );
}

/* ---------- 导入：先把链接全部入库，再提取信息 ---------- */
export async function importFromFile(file) {
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
