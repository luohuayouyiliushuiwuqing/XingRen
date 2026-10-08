/* 网页源数据提取 Demo — 交互逻辑 */

"use strict";

/* ---------- 页面里还能拿到的数据（目录卡片） ---------- */
const SOURCE_CARDS = [
  { name: "标题与描述", sel: "title / meta",
    desc: "页面标题、description、keywords、canonical、robots 等 head 信息。",
    eg: '<title>前端周刊 · 第 42 期</title>' },
  { name: "Open Graph / Twitter 卡片", sel: 'meta[property^="og:"]',
    desc: "分享到社交平台时的标题、图片、摘要——CLI 抓的 og:title / og:image 就来自这里。",
    eg: '<meta property="og:image" content=".../cover.png">' },
  { name: "图标 Favicon", sel: 'link[rel*="icon"]',
    desc: "favicon、apple-touch-icon、manifest 链接，href 支持相对路径补全。",
    eg: '<link rel="icon" href="/favicon.ico">' },
  { name: "JSON-LD 结构化数据", sel: 'script[type="application/ld+json"]',
    desc: "搜索引擎用的结构化数据（文章列表、商品、面包屑等），是 JSON 不是 HTML。",
    eg: '{"@type":"ItemList","itemListElement":[…]}' },
  { name: "正文与富文本块", sel: ".space-y-2",
    desc: "按 class/id/语义标签圈定的内容区域——本页实验台演示的主角。",
    eg: '<div class="space-y-2"><article>…</article></div>' },
  { name: "链接与锚文本", sel: "a[href]",
    desc: "所有超链接的地址、文字、rel/target，可进一步做全站链接抽取。",
    eg: '<a href="/post/42">第 42 期文章</a>' },
  { name: "图片与资源", sel: "img[src], img[srcset]",
    desc: "图片地址、alt 文本、srcset 响应式候选、picture/source。",
    eg: '<img src="https://cdn…/1.png" alt="封面">' },
  { name: "表格与列表", sel: "table tr / ul li",
    desc: "规整的行列数据：逐行匹配后按单元格取值，等价于把 HTML 变成表格。",
    eg: "<tr><td>指标</td><td>数值</td></tr>" },
  { name: "data-* 自定义属性", sel: "[data-id]",
    desc: "前端框架常把 ID、分页、状态等埋在 data-* 属性里，选择器直接命中。",
    eg: '<article data-id="42" data-votes="128">' },
  { name: "内嵌脚本数据", sel: 'script#__NEXT_DATA__',
    desc: "Next.js / Nuxt 等把整份页面数据序列化进 script，解析它等于拿到 API 响应。",
    eg: '<script id="__NEXT_DATA__" type="application/json">…</script>' },
  { name: "表单与输入", sel: 'input[name="q"]',
    desc: "表单字段的 name/value/placeholder，可用于分析搜索框或登录结构。",
    eg: '<input name="q" placeholder="搜索…">' },
  { name: "响应层数据（非 DOM）", sel: "response.headers",
    desc: "状态码、Content-Type、Set-Cookie、Last-Modified 等在 Response 对象上，不在 HTML 里。",
    eg: "response.status / response.headers" },
];

/* ---------- 抓取到的页面源码（由服务端回传，供本地改选择器重提取） ---------- */
let lastHtml = "";

/* ---------- 快捷选择器 ---------- */
const CHIPS = [
  ".space-y-2 > *",
  ".text-secondary",
  ".space-y-2 a",
  "a[href]",
  "img[src]",
  "time",
  "script[type='application/ld+json']",
];

/* ---------- 工具 ---------- */
const $ = (id) => document.getElementById(id);

function parseDoc(html) {
  return new DOMParser().parseFromString(html, "text/html");
}

function esc(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function truncate(s, n) {
  s = s.replace(/\s+/g, " ").trim();
  return s.length > n ? s.slice(0, n) + "…" : s;
}

/* ---------- 数据源目录 ---------- */
function renderSourceGrid() {
  const grid = $("sourceGrid");
  grid.innerHTML = "";
  for (const item of SOURCE_CARDS) {
    const card = document.createElement("div");
    card.className = "source-card";

    const head = document.createElement("div");
    head.className = "sc-head";
    const name = document.createElement("span");
    name.className = "sc-name";
    name.textContent = item.name;
    const sel = document.createElement("span");
    sel.className = "sc-sel";
    sel.textContent = item.sel;
    sel.title = item.sel;
    head.append(name, sel);

    const desc = document.createElement("p");
    desc.className = "sc-desc";
    desc.textContent = item.desc;

    const eg = document.createElement("div");
    eg.className = "sc-eg";
    eg.textContent = item.eg;

    card.append(head, desc, eg);
    grid.appendChild(card);
  }
}

/* ---------- 快捷 chips ---------- */
function renderChips() {
  const box = $("chips");
  box.innerHTML = "";
  for (const c of CHIPS) {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.type = "button";
    chip.textContent = c;
    chip.addEventListener("click", () => {
      $("selectorInput").value = c;
      runExtract();
    });
    box.appendChild(chip);
  }
}

function syncChipState() {
  const cur = $("selectorInput").value.trim();
  document.querySelectorAll(".chip").forEach((el) => {
    el.classList.toggle("active", el.textContent === cur);
  });
}

/* ---------- 提取（本地 DOM） ---------- */
const PICK_ATTRS = ["href", "src", "alt", "title", "data-id", "data-votes", "value", "content"];

function nodeToDto(el) {
  const attrs = {};
  for (const a of PICK_ATTRS) {
    const v = el.getAttribute && el.getAttribute(a);
    if (v) attrs[a] = v;
  }
  const text = truncate(el.textContent, 220);
  return {
    tag: el.tagName.toLowerCase(),
    classes: Array.from(el.classList).join(" "),
    text: text || "（无文本）",
    attrs,
  };
}

function renderItems(items, total, list) {
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = '<div class="empty-hint">当前源码中没有匹配该选择器的元素</div>';
    return;
  }
  items.forEach((dto, i) => {
    const item = document.createElement("div");
    item.className = "match-item";

    const top = document.createElement("div");
    top.className = "mi-top";
    const idx = document.createElement("span");
    idx.className = "mi-idx";
    idx.textContent = String(i + 1);
    const tag = document.createElement("span");
    tag.className = "mi-tag";
    const cls = dto.classes ? "." + dto.classes.split(/\s+/).filter(Boolean).join(".") : "";
    tag.textContent = dto.tag + cls;
    top.append(idx, tag);

    const text = document.createElement("div");
    text.className = "mi-text";
    text.textContent = dto.text || "（无文本）";

    item.append(top, text);

    const entries = Object.entries(dto.attrs || {});
    if (entries.length) {
      const box = document.createElement("div");
      box.className = "mi-attrs";
      for (const [k, v] of entries.slice(0, 8)) {
        const chipEl = document.createElement("span");
        chipEl.className = "mi-attr";
        chipEl.textContent = k + "=" + truncate(v, 60);
        box.appendChild(chipEl);
      }
      item.appendChild(box);
    }
    list.appendChild(item);
  });

  if (typeof total === "number" && total > items.length) {
    const more = document.createElement("div");
    more.className = "empty-hint";
    more.textContent = `仅展示前 ${items.length} 个，共 ${total} 个匹配`;
    list.appendChild(more);
  }
}

function setMatchCount(count) {
  const badge = $("matchCount");
  badge.classList.toggle("empty", !count);
  badge.textContent = count ? count + " 个匹配" : "0 个匹配";
}

/* ---------- 字段解析（「标签: 值」行结构） ---------- */
function stripLabel(s) {
  return s.replace(/[：:]\s*$/, "").trim();
}

function parseFieldsLocal(doc, selector) {
  const fields = [];
  let rows;
  try {
    rows = doc.querySelectorAll(selector);
  } catch (e) {
    return fields;
  }
  for (const row of rows) {
    const first = row.firstElementChild;
    if (!first || first.tagName !== "SPAN") continue;  // 标签必须是行的第一个子元素
    const labelRaw = first.textContent;
    const label = stripLabel(labelRaw);
    const time = row.querySelector("time");
    const links = Array.from(row.querySelectorAll("a")).map((a) => ({
      text: a.textContent.trim(),
      href: a.getAttribute("href") || "",
    }));
    const full = row.textContent.replace(/\s+/g, " ").trim();
    let value = full.startsWith(labelRaw)
      ? full.slice(labelRaw.length)
      : full.replace(labelRaw, "");
    value = value.replace(/\s+/g, " ").trim();
    // value 原文包含链接文字，去掉重复部分，只留链接之外的剩余文字
    for (const l of links) {
      if (l.text) value = value.replace(l.text, "");
    }
    value = value.replace(/\s+/g, " ").trim().replace(/^[,，、;；\s]+/, "").replace(/[\s,，、;；]+$/, "");
    if (!label || (!value && !links.length && !time)) continue;
    fields.push({
      label,
      value,
      links,
      datetime: time ? time.getAttribute("datetime") || "" : "",
    });
  }
  return fields;
}

function renderFields(fields) {
  const panel = $("fieldPanel");
  const grid = $("fieldGrid");
  if (!fields.length) {
    panel.hidden = true;
    grid.innerHTML = "";
    return;
  }
  panel.hidden = false;
  $("fieldCount").textContent = fields.length + " 个字段";
  grid.innerHTML = "";
  for (const f of fields) {
    const row = document.createElement("div");
    row.className = "field-row";
    const label = document.createElement("span");
    label.className = "field-label";
    label.textContent = f.label;
    const value = document.createElement("span");
    value.className = "field-value";
    // 日期类字段直接显示纯文本（2026-09-15），不用徽标样式
    const dateText = f.datetime ? f.datetime.slice(0, 10) : "";
    if (f.links.length) {
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
      const tail = document.createElement("span");
      tail.className = "field-plain";
      tail.textContent = rest ? " " + rest : "";
      value.appendChild(tail);
    } else if (f.value) {
      const plain = document.createElement("span");
      plain.className = "field-plain";
      plain.textContent = f.value;
      value.appendChild(plain);
    } else if (dateText) {
      const plain = document.createElement("span");
      plain.className = "field-plain";
      plain.textContent = dateText;
      value.appendChild(plain);
    }
    row.append(label, value);
    grid.appendChild(row);
  }
}

function runExtract() {
  const selector = $("selectorInput").value.trim();
  const list = $("matchList");
  const errBox = $("errorBox");
  errBox.hidden = true;

  if (!lastHtml) {
    setMatchCount(0);
    list.innerHTML = '<div class="empty-hint">输入网址并点「抓取网址」后，在这里展示提取结果</div>';
    renderFields([]);
    updateCode(selector);
    return;
  }
  const doc = parseDoc(lastHtml);

  if (!selector) {
    setMatchCount(0);
    list.innerHTML = '<div class="empty-hint">输入 CSS 选择器后点击「提取」</div>';
    renderFields([]);
    updateCode("");
    return;
  }

  let nodes;
  try {
    nodes = doc.querySelectorAll(selector);
  } catch (e) {
    setMatchCount(0);
    $("matchCount").textContent = "选择器无效";
    errBox.textContent = "选择器语法错误：" + e.message;
    errBox.hidden = false;
    renderFields([]);
    updateCode(selector);
    return;
  }

  const dtos = Array.from(nodes).slice(0, 30).map(nodeToDto);
  setMatchCount(nodes.length);
  renderItems(dtos, nodes.length, list);
  renderFields(parseFieldsLocal(doc, selector));
  updateCode(selector);
}

/* ---------- 远程抓取（服务端 Scrapling） ---------- */
function showFetchStatus(msg, kind) {
  const el = $("fetchStatus");
  el.hidden = false;
  el.className = "fetch-status" + (kind ? " " + kind : "");
  el.textContent = msg;
}

async function fetchRemote() {
  const url = $("urlInput").value.trim();
  const selector = $("selectorInput").value.trim();
  const proxy = $("proxyInput").value.trim();
  if (!url) { showFetchStatus("请先填写网址", "err"); return; }
  if (!selector) { showFetchStatus("请先填写选择器（如 .space-y-2 > *）", "err"); return; }

  $("fetchBtn").disabled = true;
  showFetchStatus(`正在抓取 ${url} …（服务端 Scrapling 三级降级，可能需要数秒到一两分钟）`, "");

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 180000);
  try {
    const api = `/api/fetch?url=${encodeURIComponent(url)}`
      + `&selector=${encodeURIComponent(selector)}`
      + `&proxy=${encodeURIComponent(proxy)}`;
    const resp = await fetch(api, { signal: controller.signal });
    const data = await resp.json();

    if (!data.ok) {
      showFetchStatus("抓取失败：" + data.error, "err");
      return;
    }

    if (data.html) {
      lastHtml = data.html;
      runExtract();
      updateSummary();
    } else {
      lastHtml = "";
      const list = $("matchList");
      $("errorBox").hidden = true;
      setMatchCount(data.count);
      renderItems(data.items, data.count, list);
      renderFields(data.fields || []);
      updateSummary();
    }
    showFetchStatus(
      `成功：HTTP ${data.status} · ${data.finalUrl} · 选择器「${data.selector}」命中 ${data.count} 个元素`
      + (data.html ? "（源码已同步到左侧，可继续改选择器本地试）" : "（页面过大，仅展示提取结果）"),
      "ok"
    );
  } catch (e) {
    const msg = e.name === "AbortError"
      ? "抓取超时（超过 3 分钟），请换更小的页面或检查代理"
      : "无法连接本地服务或网络错误：" + e.message + "（请确认用 python server.py 启动）";
    showFetchStatus(msg, "err");
  } finally {
    clearTimeout(timer);
    $("fetchBtn").disabled = false;
  }
}

/* ---------- 整页自动摘要 ---------- */
function updateSummary() {
  const grid = $("summaryGrid");
  if (!lastHtml) {
    grid.innerHTML = '<div class="empty-hint">抓取网址后这里会展示页面元数据与资源统计</div>';
    return;
  }
  const doc = parseDoc(lastHtml);
  const meta = (sel, attr) => {
    const el = doc.querySelector(sel);
    return el ? el.getAttribute(attr) || "" : "";
  };
  const first = (sel) => {
    const el = doc.querySelector(sel);
    return el ? truncate(el.textContent, 60) : "";
  };

  const items = [
    { label: "TITLE", value: first("title") },
    { label: "OG:TITLE", value: meta('meta[property="og:title"]', "content") },
    { label: "DESCRIPTION", value: meta('meta[name="description"]', "content") },
    { label: "OG:IMAGE", value: meta('meta[property="og:image"]', "content") },
    { label: "FAVICON", value: meta('link[rel*="icon"]', "href") },
    { label: "JSON-LD 块数", value: String(doc.querySelectorAll('script[type="application/ld+json"]').length), num: true },
    { label: "链接数", value: String(doc.querySelectorAll("a[href]").length), num: true },
    { label: "图片数", value: String(doc.querySelectorAll("img").length), num: true },
    { label: "标题标签数", value: String(doc.querySelectorAll("h1,h2,h3").length), num: true },
    { label: "data-* 元素", value: String(doc.querySelectorAll("[data-id]").length), num: true },
  ];

  grid.innerHTML = "";
  for (const it of items) {
    const card = document.createElement("div");
    card.className = "sum-card";
    const label = document.createElement("div");
    label.className = "sum-label";
    label.textContent = it.label;
    const value = document.createElement("div");
    value.className = "sum-value" + (it.num ? " num" : "") + (it.value ? "" : " none");
    value.textContent = it.value || "（未找到）";
    value.title = it.value || "";
    card.append(label, value);
    grid.appendChild(card);
  }
}

/* ---------- Scrapling 代码同步 ---------- */
function updateCode(selector) {
  const sel = selector || "<选择器>";
  const code = [
    '<span class="cmt"># 三级降级抓取后，直接在 Response 上按选择器提取</span>',
    '<span class="kw">from</span> scrapling.fetchers <span class="kw">import</span> Fetcher, DynamicFetcher, StealthyFetcher',
    "",
    'resp = Fetcher.get(<span class="str">"https://example.com/post/42"</span>)',
    '<span class="kw">if</span> resp <span class="kw">is</span> <span class="kw">None</span>:',
    '    resp = DynamicFetcher.fetch(<span class="str">"https://example.com/post/42"</span>)  <span class="cmt"># JS 渲染降级</span>',
    "",
    `<span class="cmt"># 提取 ${esc(sel)} 里的数据</span>`,
    `items = resp.css(<span class="str">"${esc(sel)}"</span>)`,
    '<span class="kw">for</span> el <span class="kw">in</span> items:',
    "    print(el.text.strip())                    <span class=\"cmt\"># 文本</span>",
    '    print(el.attrib.get(<span class="str">"href"</span>, <span class="str">""</span>))      <span class="cmt"># 属性</span>',
    "",
    '<span class="cmt"># 单个值：取第一个匹配</span>',
    `first = resp.css(<span class="str">"${esc(sel)}"</span>).extract_first()`,
  ].join("\n");
  $("codeBlock").innerHTML = code;
}

/* ---------- 事件绑定 ---------- */
function init() {
  renderSourceGrid();
  renderChips();

  $("runBtn").addEventListener("click", runExtract);
  $("fetchBtn").addEventListener("click", fetchRemote);
  $("proxyInput").value = "http://127.0.0.1:7892";
  $("urlInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") fetchRemote();
  });
  $("selectorInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") runExtract();
  });
  $("selectorInput").addEventListener("input", () => {
    syncChipState();
    runExtract();
  });

  syncChipState();
  runExtract();
  updateSummary();
}

document.addEventListener("DOMContentLoaded", init);
