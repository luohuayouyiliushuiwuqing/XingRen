/* 存储目录面板与目录点选。 */
import { loadRecords } from "./records.js";
import { $, el, setStatus } from "./state.js";

/* ---------- 存储目录（数据库、缓存等本地私有数据的统一存放处） ---------- */
export const fsState = { path: "", parent: "", storage: "" };

function fsJoin(dir, name) {
  if (!dir) return name;
  const sep = dir.includes("\\") ? "\\" : "/";
  return /[\\\/]$/.test(dir) ? dir + name : dir + sep + name;
}

export async function openDbPanel() {
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

export async function loadFs(path) {
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

export async function saveDbPath() {
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
