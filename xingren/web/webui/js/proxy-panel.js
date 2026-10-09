/* 代理映射面板 + 域名管理弹窗（它唯一的开启方）。 */
import { render } from "./board.js";
import { loadProxyDomains } from "./records.js";
import { $, el, setStatus, state } from "./state.js";

/* ---------- 域名管理 ---------- */

function openDomain(domainName) {
  state.domainTarget = domainName;
  $("domainTitle").textContent = `域名管理：${domainName}`;
  // 从 API 获取域名信息
  fetch("/api/domains").then(r => r.json()).then(data => {
    const domain = (data.domains || []).find(d => d.name === domainName);
    $("domainNameInput").value = domain?.display_name || "";
    const np = domain?.need_proxy;   // null=跟随全局, true/false
    $("domainNeedSelect").value = np === null || np === undefined ? "" : (np ? "1" : "0");
  });
  $("domainMask").hidden = false;
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

  /* 域名规则 */
  const domBox = $("domainProxyList");
  domBox.innerHTML = "";
  try {
    const data = await (await fetch("/api/domains")).json();
    const withRule = (data.domains || []).filter(d => d.need_proxy !== null);
    for (const d of withRule) {
      const row = el("div", "rule-row");
      const nameBtn = el("span", "rule-pattern link", d.name);
      nameBtn.addEventListener("click", () => { $("proxyMask").hidden = true; openDomain(d.name); });
      row.appendChild(nameBtn);
      row.appendChild(el("span", "rule-proxy" + (d.need_proxy ? "" : " direct"),
        d.need_proxy ? "用代理" : "直连"));
      row.appendChild(el("span", "rule-note", "点域名可编辑"));
      domBox.appendChild(row);
    }
    if (!withRule.length) domBox.appendChild(el("div", "rule-empty", "暂无域名级规则（都在跟随全局）"));
  } catch { domBox.textContent = "加载失败"; }
}

export async function saveGlobalProxy() {
  const val = $("globalProxyInput").value.trim();
  state.globalProxy = val;
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
