"""抓取经验缓存：记住哪些域名直连不通、代理是否可用。

解决两类时间浪费：
1. 国外站直连必死（超时/被重置），三级抓取器 × scrapling 内部重试 × 超时，
   一条能烧一两分钟——跑过一次就该记住，下次没代理直接跳过。
2. 代理端口在、但转发不通（上游挂了/隧道坏），同样会把每条记录拖到超时——
   先花两秒做一次转发健康检查，不通就别跑，留日志等恢复再说。

数据放**存储目录的 cache/ 下**（`cache/fetch_hints.json`，JSON 缓存而非关键数据）：
随 `set_storage_dir()` 整体迁移；删掉这个文件即重置全部学习结果。
按**根域名**记录三态经验：`direct`（直连是否可达）、`proxy`（经代理是否可达）。

线程安全：模块级 RLock，读写文件原子替换（ThreadingHTTPServer + 补抓 5 并发）。
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

from almond.core import records

HINTS_FILENAME = "fetch_hints.json"

_PROXY_TTL_OK = 30      # 代理健康结果缓存：通过后 30s 内不再探测
_PROXY_TTL_FAIL = 15    # 失败也缓存，避免一轮补抓每条都先花 2.5s 探测
_PROXY_PROBE_TIMEOUT = 2.5
# 探测地址：走 HTTPS（触发 CONNECT 隧道，最能证明代理能连到远程），两个目标兜底
_PROXY_PROBE_TARGETS = (
    "https://www.gstatic.com/generate_204",
    "https://cp.cloudflare.com/generate_204",
)

# 直连「网络不可达」类错误的关键词（大小写不敏感）。
# 命中才记 direct=False——403/404/反爬拦截说明**连得上**，不能算直连不通。
_NETWORK_MARKERS = (
    "timed out", "timeout", "connection reset", "reset by peer",
    "connection refused", "failed to connect", "connection failed",
    "could not resolve", "cannot resolve", "resolve host", "name_not_resolved",
    "recv failure", "empty reply", "no route", "network is unreachable",
    "connection aborted", "connection closed", "connection aborted",
    "tunnel connection failed", "err_connection", "err_tunnel", "err_proxy",
    "proxyconnect", "socks",
)

_LOCK = threading.RLock()
_DATA: dict | None = None                                   # {"hosts": {域名: 经验行}}
_HEALTH: dict[str, tuple[bool, float]] = {}                 # 代理地址 → (是否可用, 探测时刻)


def path() -> Path:
    """缓存文件位置（跟随存储目录，切换后自动指向新位置）。"""
    return records.DATA_DIR / "cache" / HINTS_FILENAME


def is_network_error(text: str) -> bool:
    """错误文本是否属于「网络不可达」类（可据此记直连失败）。"""
    low = (text or "").lower()
    return any(marker in low for marker in _NETWORK_MARKERS)


# ---------------------------------------------------------------- 经验读写

def _load() -> dict:
    global _DATA
    if _DATA is None:
        parsed: dict = {}
        try:
            f = path()
            if f.exists():
                raw = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    parsed = raw
        except (OSError, json.JSONDecodeError):
            parsed = {}          # 文件损坏当没有经验，下次写入会覆盖
        parsed.setdefault("hosts", {})
        _DATA = parsed
    return _DATA


def _save() -> None:
    f = path()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.parent / (f.name + ".tmp")
    tmp.write_text(json.dumps(_DATA, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)           # 原子替换，读方不会看到半截文件


def _domain(url: str) -> str:
    return records._root_domain(url) or url


def _touch(url: str, key: str, value: bool) -> None:
    """记一条经验；值没变化就不落盘（并发高频调用下避免写放大）。"""
    domain = _domain(url)
    with _LOCK:
        row = _load()["hosts"].setdefault(domain, {})
        if row.get(key) == value:
            return
        row[key] = value
        row["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save()


def hint(url: str) -> dict:
    """读某 URL 的经验行：{direct: bool|None, proxy: bool|None, updated: str}。"""
    with _LOCK:
        row = _load()["hosts"].get(_domain(url), {})
        return {
            "direct": row.get("direct"),
            "proxy": row.get("proxy"),
            "updated": row.get("updated", ""),
        }


def record_direct_success(url: str) -> None:
    """直连收到过 HTTP 响应（任何状态码）= 直连可达。"""
    _touch(url, "direct", True)


def record_direct_failure(url: str) -> None:
    """直连网络不可达（超时/重置/DNS 失败）= 这个域名必须另想办法。"""
    _touch(url, "direct", False)


def record_proxy_success(url: str) -> None:
    """经代理收到过 HTTP 响应 = 代理能到达这个域名。"""
    _touch(url, "proxy", True)


def proxy_needed(url: str) -> bool:
    """已知直连不通 → 抓取应只走代理、不再跑直连兜底。"""
    return hint(url)["direct"] is False


# ---------------------------------------------------------------- 代理健康

def _probe(proxy: str, timeout: float) -> bool:
    """经代理发起真实小请求：收到任何 HTTP 响应（含 4xx）都算代理能连到远程。"""
    for target in _PROXY_PROBE_TARGETS:
        try:
            opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
            opener.open(target, timeout=timeout)
            return True
        except HTTPError:
            return True          # 对端回了状态码 → 隧道是通的
        except Exception:        # noqa: BLE001 — 隧道失败/超时 → 换一个探测点
            continue
    return False


def proxy_reachable(proxy: str, timeout: float = _PROXY_PROBE_TIMEOUT) -> bool:
    """代理转发是否可用（带 TTL 缓存：通过 30s / 失败 15s，探测在锁内只发一次）。

    区别于端口探测：端口在 ≠ 能连到远程。这里识别「端口开着但代理坏/上游挂」。
    """
    now = time.time()
    with _LOCK:
        hit = _HEALTH.get(proxy)
        if hit is not None and now - hit[1] < (_PROXY_TTL_OK if hit[0] else _PROXY_TTL_FAIL):
            return hit[0]
        ok = _probe(proxy, timeout)
        _HEALTH[proxy] = (ok, time.time())
        return ok


# ---------------------------------------------------------------- 跳过判定

def skip_reason(url: str, proxy: str | None) -> str | None:
    """返回「这条现在不该抓」的原因；可以抓则返回 None。

    - 已知直连不通 + 没有代理        → 跳过，等有代理再试
    - 已知直连不通 + 代理转发不通    → 跳过，留日志等代理恢复再试
    - 已知直连不通 + 代理可用        → 返回 None，调用方应**只走代理**（proxy_needed）
    """
    h = hint(url)
    if h["direct"] is not False:
        return None
    domain = _domain(url)
    if not proxy:
        return (f"已知 {domain} 直连不通（经验缓存记录于 {h['updated'] or '未知时间'}），"
                f"当前没有代理 → 本次不抓取，待有代理后重试")
    if not proxy_reachable(proxy):
        return (f"已知 {domain} 必须走代理，但代理 {proxy} 转发不通"
                f"（代理不通导致抓取不成功）→ 本次不抓取，待代理恢复后重试")
    return None
