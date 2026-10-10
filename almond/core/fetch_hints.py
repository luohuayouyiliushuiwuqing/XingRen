"""抓取经验缓存：记住哪些域名直连不通、代理是否可用、反爬用哪个策略。

解决三类时间浪费：
1. 国外站直连必死（超时/被重置），三级抓取器 × scrapling 内部重试 × 超时，
   一条能烧一两分钟——跑过一次就该记住，下次没代理直接跳过。
2. 代理端口在、但转发不通（上游挂了/隧道坏），同样会把每条记录拖到超时——
   先花两秒做一次转发健康检查，不通就别跑，留日志等恢复再说。
3. 常规打法拿到了数据（直连/代理 + 哪个引擎 + 什么参数都算）——**记住这个可行策略**，
   下次遇到该域名直接用它；每次成功刷新使用时间，超过 7 天没用过就强制复验，
   验不过就弃用，等下一轮重新定制。403 反爬拦截只是触发「扩展候选定制」的一种情况。
4. **Cookie 优先**：人工写入（`--cookie`）或浏览器引擎成功后回传的 Cookie 按域名存本文件
   （`hosts.<域名>.cookie`，不会经 `all_hints` 泄露给前端）——有 Cookie 先带它静态直取一发，
   成功即返回；HTTP ≥400 视为失效弃用，回退策略/常规链；网络类异常不弃（不是 Cookie 的错）。

数据放**存储目录的 cache/ 下**（`cache/fetch_hints.json`，JSON 缓存而非关键数据）：
随 `set_storage_dir()` 整体迁移；删掉这个文件即重置全部学习结果。
按**根域名**记录：`direct`/`proxy`（三态可达性）+ `strategy`（{id, last_success}）。

线程安全：模块级 RLock，读写文件原子替换（ThreadingHTTPServer + 补抓 5 并发）。
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta
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
                # utf-8-sig：Windows 记事本/PowerShell 写出的带 BOM 文件也能读
                raw = json.loads(f.read_text(encoding="utf-8-sig"))
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


def all_hints() -> dict:
    """全部经验行（域名 → {direct, proxy, updated}），供 /api/domains 附带给前端展示。"""
    with _LOCK:
        return {
            domain: {
                "direct": row.get("direct"),
                "proxy": row.get("proxy"),
                "updated": row.get("updated", ""),
            }
            for domain, row in _load()["hosts"].items()
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


# ---------------------------------------------------------------- 策略成绩册

STRATEGY_STALE_DAYS = 7      # 超过 N 天没成功用过 → 下次使用时强制复验
_STRATEGY_BOARD_MAX = 6      # 每域名成绩册上限（超出丢最慢的备胎）
_TS_FMT = "%Y-%m-%d %H:%M:%S"


def get_strategy(url: str) -> dict | None:
    """当前应使用的策略 = **成绩册里最快的一套**：{id, last_success, best_ms}。"""
    with _LOCK:
        s = _load()["hosts"].get(_domain(url), {}).get("strategy")
        if isinstance(s, dict) and s.get("id"):
            return {
                "id": s["id"],
                "last_success": s.get("last_success", ""),
                "best_ms": s.get("best_ms"),
            }
    return None


def strategy_stale(url: str) -> bool:
    """距上次成功使用是否超过 STRATEGY_STALE_DAYS（时间缺失/解析不了按过期处理）。"""
    s = get_strategy(url)
    if not s:
        return False
    try:
        last = datetime.strptime(s["last_success"], _TS_FMT)
    except (ValueError, TypeError):
        return True
    return datetime.now() - last > timedelta(days=STRATEGY_STALE_DAYS)


def save_strategy(url: str, strategy_id: str, ms: int | None = None) -> None:
    """记一次成功使用（ms = 本次抓取耗时）：更新成绩册，**更快者当选**。

    strategy_id 编码完整配方：`路由|引擎[|参数…]`，例如
      `direct|static`（直连静态抓取成功）
      `proxy|static|impersonate=firefox147`（走代理 + firefox 指纹）
    成绩册 `strategies`：sid → {runs, best_ms, last_ms, last_success}。
    当选规则：同 id 刷新；当前无策略 / 当前无历史成绩（升级前的老数据）/ 本次更快 → 当选；
    比当前慢 → 只进成绩册当备胎。每次成功都刷新 last_success（7 天复验凭证）。
    """
    domain = _domain(url)
    with _LOCK:
        row = _load()["hosts"].setdefault(domain, {})
        now = datetime.now().strftime(_TS_FMT)
        board = row.setdefault("strategies", {})
        rec = dict(board.get(strategy_id) or {})
        rec["runs"] = int(rec.get("runs") or 0) + 1
        rec["last_success"] = now
        if ms is not None:
            rec["last_ms"] = ms
            if rec.get("best_ms") is None or ms < rec["best_ms"]:
                rec["best_ms"] = ms
        board[strategy_id] = rec

        active = row.get("strategy")
        faster = (
            not active                                   # 第一套 → 当选
            or active.get("id") == strategy_id           # 同一套 → 刷新
            or active.get("best_ms") is None             # 老数据无成绩 → 用这次实测的
            or (ms is not None and ms < active.get("best_ms"))   # 真更快 → 夺位
        )
        if faster:
            row["strategy"] = {
                "id": strategy_id,
                "last_success": now,
                "best_ms": rec.get("best_ms"),
            }
        _trim_board(row)
        row["updated"] = now
        _save()


def _trim_board(row: dict) -> None:
    """成绩册只留最快 N 套（活跃的必留，其余按 best_ms 升序）。"""
    board = row.get("strategies") or {}
    if len(board) <= _STRATEGY_BOARD_MAX:
        return
    active_id = (row.get("strategy") or {}).get("id")
    ranked = sorted(
        board,
        key=lambda k: (board[k].get("best_ms") is None, board[k].get("best_ms") or 0),
    )
    ordered = ([active_id] if active_id in board else []) + [k for k in ranked if k != active_id]
    for k in list(board):
        if k not in ordered[:_STRATEGY_BOARD_MAX]:
            board.pop(k)


def drop_strategy(url: str, strategy_id: str | None = None) -> None:
    """弃用一套策略（缺省 = 当前最快的那套）：从成绩册除名。

    被弃用的正是活跃位时，**顺位给成绩册里第二快的**（曾经成功过、还没被证伪）；
    成绩册清空则整个移除，等下一轮被拦时重新定制。
    """
    domain = _domain(url)
    with _LOCK:
        row = _load()["hosts"].get(domain)
        if not row:
            return
        active = row.get("strategy") or {}
        sid = strategy_id or active.get("id")
        if not sid:
            return
        board = row.get("strategies") or {}
        board.pop(sid, None)
        if active.get("id") == sid:
            if board:
                nid = min(
                    board,
                    key=lambda k: (board[k].get("best_ms") is None,
                                   board[k].get("best_ms") or 0),
                )
                row["strategy"] = {
                    "id": nid,
                    "last_success": board[nid].get("last_success", ""),
                    "best_ms": board[nid].get("best_ms"),
                }
            else:
                row.pop("strategy", None)
        row["updated"] = datetime.now().strftime(_TS_FMT)
        _save()


# ---------------------------------------------------------------- Cookie

COOKIE_BAN_DAYS = 7          # Cookie 被判定失效后，N 天内禁止**自动**回传（防「收了又废」抖动）


def parse_cookie_header(value: str) -> dict:
    """把浏览器复制的 `a=1; b=2` 解析成 dict（值允许含 `=`）。解析不出返回 {}。"""
    out: dict = {}
    for seg in (value or "").split(";"):
        k, eq, v = seg.strip().partition("=")
        if eq and k:
            out[k] = v
    return out


def _header_of(mapping: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in mapping.items())


def _resp_cookies(response) -> dict:
    """从 scrapling Response 提取 {名: 值}（兼容 dict / get_dict() / Morsel 迭代）。"""
    c = getattr(response, "cookies", None)
    if not c:
        return {}
    try:
        if hasattr(c, "get_dict"):
            c = c.get_dict()
        if isinstance(c, dict):
            return {str(k): str(v) for k, v in c.items()}
        return {m.key: m.value for m in c}   # CookieJar → Morsel
    except Exception:  # noqa: BLE001
        return {}


def set_cookie(url: str, value: str) -> None:
    """人工写入 Cookie（原样粘贴浏览器里的 `a=1; b=2`），覆盖旧值。

    人工写入 = 明确意图，顺带**解除**失效防抖（`cookie_failed_at`）。
    """
    domain = _domain(url)
    value = (value or "").strip()
    with _LOCK:
        row = _load()["hosts"].setdefault(domain, {})
        now = datetime.now().strftime(_TS_FMT)
        row["cookie"] = {"value": value, "added": now}
        row.pop("cookie_failed_at", None)
        row["updated"] = now
        _save()


def get_cookie(url: str) -> dict | None:
    """读该域名的 Cookie：{value, added?, last_success?, best_ms?}；没有则 None。"""
    with _LOCK:
        ck = _load()["hosts"].get(_domain(url), {}).get("cookie")
        if isinstance(ck, dict) and ck.get("value"):
            return dict(ck)
    return None


def touch_cookie(url: str, ms: int | None = None, response=None) -> None:
    """Cookie 直取成功：刷新 last_success、记耗时（best_ms 取最小）；
    响应里若带了新的 Set-Cookie（会话轮换），合并进已存的值里。"""
    domain = _domain(url)
    with _LOCK:
        ck = _load()["hosts"].get(domain, {}).get("cookie")
        if not isinstance(ck, dict) or not ck.get("value"):
            return
        now = datetime.now().strftime(_TS_FMT)
        ck["last_success"] = now
        if ms is not None:
            ck["last_ms"] = ms
            if ck.get("best_ms") is None or ms < ck["best_ms"]:
                ck["best_ms"] = ms
        new = _resp_cookies(response)
        if new:
            merged = parse_cookie_header(ck.get("value", ""))
            merged.update(new)
            ck["value"] = _header_of(merged)
        row = _load()["hosts"][domain]
        row["updated"] = now
        _save()


def drop_cookie(url: str) -> None:
    """Cookie 失效（HTTP ≥400）→ 弃用，并记 `cookie_failed_at` 进入 7 天防抖期
    （期间自动回传被禁止；人工 set_cookie 或防抖到期后恢复）。"""
    domain = _domain(url)
    with _LOCK:
        row = _load()["hosts"].get(domain)
        if not row:
            return
        had = row.pop("cookie", None) is not None
        now = datetime.now().strftime(_TS_FMT)
        if had:
            row["cookie_failed_at"] = now
        row["updated"] = now
        _save()


def _cookie_banned(row: dict) -> bool:
    """失效防抖是否仍在生效（时间缺失按生效处理；到期顺带清掉旧标记）。"""
    raw = row.get("cookie_failed_at")
    if not raw:
        return False
    try:
        failed = datetime.strptime(raw, _TS_FMT)
    except (ValueError, TypeError):
        return True
    if datetime.now() - failed > timedelta(days=COOKIE_BAN_DAYS):
        row.pop("cookie_failed_at", None)   # 到期自清
        return False
    return True


def store_cookie_from_response(url: str, response) -> bool:
    """**任意引擎成功**后顺手回传 Cookie：把响应里的 Set-Cookie **合并**进缓存（没有则新建）。

    防抖：该域名的 Cookie 若在 COOKIE_BAN_DAYS 内被判定过失效（`cookie_failed_at`），
    自动回传被禁止——否则「静态成功存入 → 下次带 Cookie 403 → 弃用 → 又存入」会循环
    白跑。人工 set_cookie 不受此限。
    """
    new = _resp_cookies(response)
    if not new:
        return False
    domain = _domain(url)
    with _LOCK:
        row = _load()["hosts"].setdefault(domain, {})
        if _cookie_banned(row):
            return False
        now = datetime.now().strftime(_TS_FMT)
        ck = row.get("cookie") if isinstance(row.get("cookie"), dict) else {}
        merged = parse_cookie_header(ck.get("value", "")) if ck.get("value") else {}
        merged.update(new)
        ck["value"] = _header_of(merged)
        ck.setdefault("added", now)
        ck["harvested"] = now
        row["cookie"] = ck
        row["updated"] = now
        _save()
    return True
