/* 卡片 DOM：建骨架 + 就地补丁（fillCard/updateCard）。 */
import { render } from "./board.js";
import { openDetail } from "./detail.js";
import { fetchRecord } from "./fetch.js";
import { openRename, openTag } from "./modals.js";
import { matchesFilter, removeRecord } from "./records.js";
import { $, el, hostOf, state } from "./state.js";

function imgSrc(u) {
  const proxy = state.globalProxy;
  return "/api/img?src=" + encodeURIComponent(u) +
    (proxy ? "&proxy=" + encodeURIComponent(proxy) : "");
}

/* src 没变就绝不碰 <img> 节点——换节点要重新解码，肉眼就是一闪白 */

function setCover(open, record) {
  let img = open.querySelector(".cover");
  if (!record.thumbnail) { if (img) img.remove(); return; }
  const src = imgSrc(record.thumbnail);
  if (!img) {
    img = new Image();
    img.className = "cover";
    img.loading = "lazy";
    img.onerror = () => img.remove();
    open.insertBefore(img, open.querySelector(".favicon"));  // 封面要压在 favicon 下面
  }
  if (img.getAttribute("src") !== src) img.src = src;
}

function setFavicon(open, record) {
  let fav = open.querySelector(".favicon");
  const want = record.favicon && record.favicon.startsWith("http") ? record.favicon : "";
  if (!want) { if (fav) fav.remove(); return; }
  const src = imgSrc(want);
  if (!fav) {
    fav = new Image();
    fav.className = "favicon";
    fav.loading = "lazy";
    fav.onerror = () => fav.remove();
    open.appendChild(fav);   // 角标在最上层
  }
  if (fav.getAttribute("src") !== src) fav.src = src;
}

/* 卡片里会随抓取结果变化的部分。抓取完成、改名、批量提取都只动这些节点，
   不重建整张卡也不重绘看板——原先每次都 render()，board.innerHTML="" 把
   几百张卡连同图片全部拔掉再插回去，就是「抓到数据页面闪一下」的来源 */

function fillCard(card, record) {
  card.__record = record;   // 按钮闭包从这里取最新记录，别用建卡时的旧对象

  const title = card.querySelector(".title");
  title.textContent = record.title || "（标题待补充）";   // 没标题就灰着，别装作有标题
  title.classList.toggle("pending", !record.title);

  card.querySelector(".dot").className =
    "dot " + (!record.fetched ? "pending" : record.success ? "ok" : "fail");

  const open = card.querySelector(".open");
  setCover(open, record);
  setFavicon(open, record);

  const row = card.querySelector(".domain");
  for (const b of row.querySelectorAll(".tag-badge")) b.remove();
  if (record.tags && record.tags.length) {
    for (const t of record.tags) row.appendChild(el("span", "tag-badge", t.name));
  }
}

/* 单条记录更新：就地打补丁，不整页重绘 */

export function updateCard(record) {
  const card = document.querySelector(`#board .card[data-url="${CSS.escape(record.url)}"]`);
  if (!card) return;                // 所在分组还没建卡（懒建），建的时候自然用最新数据
  if (!matchesFilter(record)) {     // 改名/抓取后掉出当前筛选 → 只能整页重排
    render();
    return;
  }
  fillCard(card, record);
}

export function createCard(record) {
  const card = el("div", "card");
  card.dataset.url = record.url;    // 单条更新按这个定位
  const host = hostOf(record.url);

  /* 缩略图：图 → 字母占位；可选 favicon 角标（图片走服务端代理加载）。
     打开链接用内部 <a>，操作按钮是它的兄弟节点，避免点按钮时触发跳转。
     封面/favicon 与标题、标签一样交给末尾的 fillCard —— 抓取回来才有。 */
  const thumb = el("div", "thumb");
  const open = el("a", "open");
  open.href = record.url;
  open.target = "_blank";
  open.rel = "noopener";
  open.title = record.url;
  open.appendChild(el("span", "letter", host.slice(0, 1).toUpperCase()));
  thumb.appendChild(open);

  /* 右上角快捷按钮（详情 / 重新抓取） */
  /* 按钮闭包一律取 card.__record（fillCard 会更新它）：
     否则就地补丁后，点「详情/重命名」拿到的还是建卡那一刻的旧数据 */
  const topActions = el("div", "thumb-actions");
  const detailBtn = el("button", null, "详情");
  detailBtn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); openDetail(card.__record); });
  const refreshBtn = el("button", null, "重新抓取");
  refreshBtn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); fetchRecord(card.__record.url); });
  topActions.append(detailBtn, refreshBtn);
  thumb.appendChild(topActions);

  /* 底部操作条（重命名 / 标签 / 删除） */
  const actions = el("div", "actions");
  const renameBtn = el("button", null, "重命名");
  renameBtn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); openRename(card.__record); });
  const tagBtn = el("button", null, "标签");
  tagBtn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); openTag(card.__record); });
  const deleteBtn = el("button", null, "删除");
  deleteBtn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); removeRecord(card.__record); });
  actions.append(renameBtn, tagBtn, deleteBtn);
  thumb.appendChild(actions);

  /* 文本区：结构建一次，标题/状态点/徽标由 fillCard 填 */
  const body = el("div", "body");
  const title = el("a", "title");
  title.href = record.url;
  title.target = "_blank";
  title.rel = "noopener";
  const domainRow = el("div", "domain");
  domainRow.appendChild(el("span", "dot"));
  domainRow.appendChild(el("span", null, host));
  body.append(title, domainRow);

  card.append(thumb, body);
  fillCard(card, record);
  return card;
}
