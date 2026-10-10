/* 代理映射面板 + 域名管理弹窗（它唯一的开启方）。 */
import { render } from "./board.js";
import { goToDomain } from "./domain-page.js";
import { loadDomainConfig, loadProxyDomains, loadRecords } from "./records.js";
import { $, el, setStatus, state } from "./state.js";

/* ---------- 域名管理 ---------- */

export function openDomain(domainName) {
  state.domainTarget = domainName;
  $("domainTitle").textContent = `域名管理：${domainName}`;
  $("domainResetOld").textContent = domainName;
  $("domainResetInput").value = "";
  // 从 API 获取域名信息
  fetch("/api/domains").then(r => r.json()).then(data => {
    const domain = (data.domains || []).find(d => d.name === domainName);
    $("domainNameInput").value = domain?.display_name || "";
    const np = domain?.need_proxy;   // null=跟随全局, true/false
    $("domainNeedSelect").value = np === null || np === undefined ? "" : (np ? "1" : "0");
  });
  $("domainMask").hidden = false;
}

/* 域名重置：原域名（含子域名）在库里的全部引用整体换成新域名 */
export async function resetDomain() {
  const old = state.domainTarget;
  const next = $("domainResetInput").value.trim();
  if (!old) return;
  if (!next) { setStatus("请填写新域名", "err"); $("domainResetInput").focus(); return; }
  const hint = `确定把 ${old} 全部替换为 ${next}？\n记录链接、封面、域名规则与代理规则都会改写，不可撤销。`;
  if (!confirm(hint)) return;
  try {
    const resp = await fetch("/api/domain/replace", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old, new: next }),
    });
    const data = await resp.json();
    if (!data.ok) {
      // 服务端单事务，走到这里必然是整体回滚——没有半截改动
      setStatus("域名重置失败（未改动任何数据）：" + data.error, "err");
      return;
    }
    $("domainMask").hidden = true;
    state.domainTarget = null;
    if (state.selectedDomain === old) state.selectedDomain = null;
    /* 正看着这个域名的子页 → 把子页切到新域名（旧域名已经不存在了）。
       先写 hash：下面 await 期间 hashchange 会把 state.domainPage 更新好 */
    if (state.domainPage === old) goToDomain(data.new);
    // 都要 await：两者的 render() 都会重写状态栏，提示必须放在最后
    await loadRecords();
    await loadProxyDomains();
    await loadDomainConfig();
    const total = data.total || 0;
    const failed = data.failed || 0;
    const rules = data.rules || 0;
    if (!total) {
      // 一条都没命中：多半是旧域名早就被清理过，别让人以为改了什么
      setStatus(
        rules
          ? `域名重置 ${data.old} → ${data.new}：命中 0 条记录，改写规则 ${rules} 条`
          : `域名重置 ${data.old} → ${data.new}：未找到任何包含该域名的记录，0 条改动`,
        rules ? undefined : "err"
      );
      return;
    }
    setStatus(
      `域名重置 ${data.old} → ${data.new}：共 ${total} 条，成功 ${data.records}` +
      `，重复 ${data.merged || 0}，失败 ${failed}` +
      (rules ? `，改写规则 ${rules} 条` : "") +
      (failed && data.error ? `（${data.error}）` : ""),
      failed ? "err" : undefined
    );
  } catch (e) {
    setStatus("域名重置失败：" + e.message, "err");
  }
}

export async function saveDomain() {
  const name = state.domainTarget;
  if (!name) return;
  const display_name = $("domainNameInput").value.trim();
  const sel = $("domainNeedSelect").value;
  const need_proxy = sel === "" ? null : sel === "1";   // "" → null = 无规则
  $("domainMask").hidden = true;
  try {
    await fetch("/api/domain", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, display_name, need_proxy }),
    });
    render();
    loadProxyDomains(); // 域名规则变化 → 刷新侧边栏代理标记
    loadDomainConfig(); // 别名/域名配置缓存也要跟着新（子页域头直接读它）
    const label = need_proxy === null ? "跟随全局" : (need_proxy ? "用代理" : "直连");
    setStatus(`已更新域名 ${name}（${label}）`);
  } catch (e) {
    setStatus("域名更新失败：" + e.message, "err");
  }
  state.domainTarget = null;
}

/* ---------- 代理映射面板 ---------- */

export async function openProxyPanel() {
  $("globalProxyInput").value = state.globalProxy;
  await renderProxyPanel();
  $("proxyMask").hidden = false;
}

async function renderProxyPanel() {
  /* URL 模式规则 */
  const ruleBox = $("ruleList");
  ruleBox.innerHTML = "";
  try {
    const data = await (await fetch("/api/proxy-rules")).json();
    for (const rule of data.rules || []) {
      const row = el("div", "rule-row");
      row.appendChild(el("span", "rule-pattern", rule.pattern));
      row.appendChild(el("span", "rule-proxy" + (rule.need_proxy ? "" : " direct"),
        rule.need_proxy ? "用代理" : "直连"));
      const del = el("button", "rule-del", "删除");
      del.addEventListener("click", async () => {
        await fetch(`/api/proxy-rule?id=${rule.id}`, { method: "DELETE" });
        renderProxyPanel();
        loadProxyDomains(); // 规则删除 → 刷新侧边栏代理标记
      });
      row.appendChild(del);
      ruleBox.appendChild(row);
    }
    if (!(data.rules || []).length) ruleBox.appendChild(el("div", "rule-empty", "暂无规则"));
  } catch { ruleBox.textContent = "加载失败"; }

  /* 域名：**全部**列出，不再只显示有域名规则的——
     侧栏只收条数 ≥10 的域名，零散域名（以及条数少的小站）只能从这里进子页 */
  const domBox = $("domainProxyList");
  domBox.innerHTML = "";
  try {
    const data = await (await fetch("/api/domains")).json();
    const all = (data.domains || []).slice().sort((a, b) => a.name.localeCompare(b.name));
    for (const d of all) {
      const row = el("div", "rule-row");
      const nameBtn = el("span", "rule-pattern link", d.name);
      nameBtn.title = "点域名名：编辑别名 / 代理规则 / 域名重置";
      nameBtn.addEventListener("click", () => { $("proxyMask").hidden = true; openDomain(d.name); });
      row.appendChild(nameBtn);
      row.appendChild(
        d.need_proxy === null || d.need_proxy === undefined
          ? el("span", "rule-note", "跟随全局")
          : el("span", "rule-proxy" + (d.need_proxy ? "" : " direct"), d.need_proxy ? "用代理" : "直连")
      );
      const enter = el("button", "rule-enter", "详情");
      enter.title = `进入 ${d.name} 的子页面`;
      enter.addEventListener("click", () => {
        $("proxyMask").hidden = true;
        goToDomain(d.name);
      });
      row.appendChild(enter);
      domBox.appendChild(row);
    }
    if (!all.length) domBox.appendChild(el("div", "rule-empty", "还没有任何域名"));
  } catch { domBox.textContent = "加载失败"; }
}

export async function saveGlobalProxy() {
  const val = $("globalProxyInput").value.trim();
  state.globalProxy = val;
  /* 手填过的地址要按住：补抓开跑前会重新探测本机代理端口，
     没有这个标记的话，用户显式填的地址会被探测结果悄悄覆盖掉 */
  state.proxyManual = !!val;
  setStatus(val ? `全局代理已设为 ${val}` : "已清除全局代理");
}

/* 自动探测本地代理端口（7889-7899）；detectOnly 时只返回不写状态 */

export async function detectProxy({ fill = false, announce = true } = {}) {
  try {
    const data = await (await fetch("/api/proxy-detect", { method: "POST" })).json();
    const found = data.proxy || "";
    if (found) {
      if (fill) {
        state.globalProxy = found;
        state.proxyManual = false;   // 自动探测写入的值不算手填，之后仍可被下次探测刷新
        const input = $("globalProxyInput");
        if (input) input.value = found;
      }
      if (announce) setStatus(`检测到本地代理：${found}`);
    } else if (announce) {
      setStatus("7889-7899 区间未发现本地代理", "err");
    }
    return found;
  } catch (e) {
    if (announce) setStatus("代理探测失败：" + e.message, "err");
    return "";
  }
}

export async function addRule() {
  const pattern = $("rulePatternInput").value.trim();
  const need_proxy = $("ruleNeedSelect").value === "1";
  if (!pattern) { setStatus("请填写匹配模式", "err"); return; }
  $("rulePatternInput").value = "";
  await fetch("/api/proxy-rule", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pattern, need_proxy }),
  });
  renderProxyPanel();
  loadProxyDomains(); // 模式规则变化 → 刷新侧边栏代理标记
  setStatus(`已添加规则：${pattern} → ${need_proxy ? "用代理" : "直连"}`);
}
