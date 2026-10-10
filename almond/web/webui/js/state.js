/* 共享地基：DOM 助手、唯一可变 state、域名/状态栏工具。无 import（叶子模块）。 */

export const $ = (id) => document.getElementById(id);

export const state = {
  records: [],
  filter: "",
  selectedDomain: null,    // null = "全部"，字符串 = 选中的域名 / "__other__"（零散域名合并项）
  selectedTag: null,       // null = "全部"，字符串 = 选中的标签
  sidebarExpanded: false,  // 侧栏超出部分是否已通过「更多」展开
  globalProxy: "http://127.0.0.1:7897",  // 全局代理，在「代理」面板里编辑
  // 手填过代理地址（面板「保存」）→ 后续自动探测不再覆盖它；探测写入的值会把它复位
  proxyManual: false,
  proxyDomains: new Set(), // 最终会走代理的域名（侧边栏标记用）
  renameTarget: null,
  tagTarget: null,         // 标签弹窗的目标记录（单条）
  tagTargets: null,        // 标签弹窗的批量目标：url 数组（null = 单条模式）
  domainTarget: null,      // 域名管理弹窗的目标域名
  detailTarget: null,
  /* 卡片多选：选中的 url（唯一真源，卡片上的复选框只是投影）。
     按 url 存而不是存记录对象——upsert/render 会换对象，存对象会拿到旧数据 */
  selectedUrls: new Set(),
  /* 域名子页：非空 = 正在看某个域名的子页（值是 rootDomain 口径的域名），
     与地址栏 #/domain/<name> 一一对应；进子页**不改** selectedDomain（筛选态原样保留） */
  domainPage: null,
  /* GET /api/domains 的缓存：name → {display_name, need_proxy, prefs, auto_fetch,
     detail_selector, cover}。补抓按它过滤 auto_fetch=0 的域名，域头设置也从它读 */
  domainConfig: new Map(),
};

/* 单域名少于该条数：侧边栏与看板分组都收进「其他」类别 */

export const MIN_GROUP = 10;

export function setStatus(msg, kind) {
  const el = $("statusText");
  el.parentElement.className = "statusbar" + (kind ? " " + kind : "");
  el.textContent = msg;
}

export function hostOf(url) {
  try {
    return new URL(url).host || url;
  } catch (e) {
    return url;
  }
}

/* 多段公共后缀：co.uk / com.cn / com.au … —— 必须与后端 records._MULTI_TLDS 一致，
   否则前端分组与后端域名代理规则对不上（bar.co.uk 会被截成 co.uk） */

const MULTI_TLDS = [
  ".co.uk", ".org.uk", ".ac.uk", ".gov.uk", ".me.uk",
  ".com.cn", ".net.cn", ".org.cn", ".gov.cn", ".edu.cn",
  ".co.jp", ".ne.jp", ".or.jp", ".ac.jp",
  ".com.au", ".net.au", ".org.au",
  ".co.nz", ".com.hk", ".com.tw", ".com.sg",
  ".com.br", ".com.mx", ".co.kr", ".co.in", ".com.ar",
];

/* 提取可注册域名：chat.deepseek.com → deepseek.com，bar.co.uk → bar.co.uk */

export function rootDomain(host) {
  /* 先剥掉显式端口：hostOf() 返回的是 host:port，而后端 _root_domain 用的是
     urlparse().hostname（不带端口）。两边口径必须一致——否则同一站点会被分成
     「0.1」和「0.1:4176」两组，域名子页按 record.domain 过滤时一条都对不上；
     端口还会让 MULTI_TLDS 的 endsWith 失效（bar.co.uk:8080 匹配不到 .co.uk）。 */
  const h = (host || "").replace(/:\d+$/, "").toLowerCase();
  for (const tld of MULTI_TLDS) {
    if (h.endsWith(tld)) {
      const head = h.slice(0, -tld.length);
      return head ? head.split(".").pop() + tld : h;
    }
  }
  const parts = h.split(".");
  return parts.length <= 2 ? h : parts.slice(-2).join(".");
}

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}
