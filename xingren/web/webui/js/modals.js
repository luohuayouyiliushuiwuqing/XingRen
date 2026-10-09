/* 卡片驱动的弹窗：重命名、标签管理。 */
import { render } from "./board.js";
import { updateCard } from "./cards.js";
import { $, el, setStatus, state } from "./state.js";

/* ---------- 重命名 ---------- */

export function openRename(record) {
  state.renameTarget = record;
  $("modalInput").value = record.title || "";
  $("modalMask").hidden = false;
  $("modalInput").focus();
}

export async function submitRename() {
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
      updateCard(record);   // 改名只动标题文字，不整页重绘
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

export async function openTag(record) {
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

export async function addNewTag() {
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
