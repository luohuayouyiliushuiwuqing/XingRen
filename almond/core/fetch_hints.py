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
**切换存储目录不迁移本文件**——新目录从零开始，旧经验留在旧目录；删掉文件即重置全部学习。
按**根域名**记录：`direct`/`proxy`（三态可达性）+ `strategy`（{id, last_success}）+ `cookie`。

线程安全：模块级 RLock，读写文件原子替换（ThreadingHTTPServer + 补抓 5 并发）。
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener

from almond.core import records
from almond.core.log import logger
from almond.core.proxy import scan_open_ports

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
_PROBE_LOCK = threading.Lock()                              # 只串行化网络探测本身
_DATA: dict | None = None                                   # {"hosts": {域名: 经验行}}
_HEALTH: dict[str, tuple[bool, float]] = {}                 # 代理地址 → (是否可用, 探测时刻)

# 存储纪元写守卫：fetch_page 入口 enter_fetch() 绑定当前纪元；抓取期间切换了存储目录
# → 后续写入（经验/策略/Cookie）全部静默作废，绝不把旧项目的学习写进新目录。
_FETCH_EPOCH = threading.local()


def enter_fetch() -> int:
    """fetch_page 入口调用：绑定本次抓取所属的存储纪元。

    同时丢弃内存缓存——抓取子进程是常驻的，别的进程（主进程/兄弟子进程）
    写过的新策略/Cookie 必须在下一次抓取时读到盘上的最新版本。
    """
    global _DATA
    with _LOCK:
        _DATA = None
    epoch = records.storage_epoch()
    _FETCH_EPOCH.epoch = epoch
    return epoch


def _write_allowed() -> bool:
    """未绑定纪元（脚本直接调 record_* 等）或纪元仍一致 → 允许写。"""
    epoch = getattr(_FETCH_EPOCH, "epoch", None)
    return epoch is None or epoch == records.storage_epoch()


# ---------------------------------------------------------------- 效应收集（子进程模式）
# 抓取跑在独立子进程里（almond.core.fetchpool）——子进程**不直接写本文件**：
# 跨进程没有这把 RLock，各写各的会互相覆盖。子进程把每次要做的写入登记成
# 「效应」带回来，主进程在存储纪元校验通过后统一落地（apply_effects）。

_EFFECTS: list | None = None          # 非 None = 当前处于效应收集模式


def enable_effects() -> None:
    """子进程抓取入口调用：此后本进程内的所有缓存写入改为收集效应、不落盘。"""
    global _EFFECTS
    _EFFECTS = []


def take_effects() -> list:
    """取走本轮收集的效应并退出收集模式（一对一，异常也不会串进下一次）。"""
    global _EFFECTS
    out = _EFFECTS or []
    _EFFECTS = None
    return out


def _record_effect(kind: str, payload: dict) -> bool:
    """收集模式下登记一条效应并短路真正的写入；非收集模式返回 False（照常写）。"""
    if _EFFECTS is None:
        return False
    _EFFECTS.append((kind, payload))
    return True


def apply_effects(effects, epoch: int | None = None) -> int:
    """主进程落地子进程带回的效应；返回实际应用条数。

    与 `set_storage_dir` 互斥（_SWITCH_LOCK）：`epoch` 与当前纪元不一致
    （抓取期间切了库）→ 整批作废，旧项目的学习绝不写进新目录的缓存。
    """
    if not effects:
        return 0
    with records._SWITCH_LOCK:
        if epoch is not None and epoch != records.storage_epoch():
            return 0
        n = 0
        for kind, p in effects:
            try:
                if kind == "touch":
                    _touch(p["url"], p["key"], p["value"])
                elif kind == "save_strategy":
                    save_strategy(p["url"], p["strategy_id"], p.get("ms"))
                elif kind == "drop_strategy":
                    drop_strategy(p["url"], p.get("strategy_id"))
                elif kind == "drop_cookie":
                    drop_cookie(p["url"])
                elif kind == "touch_cookie":
                    touch_cookie(p["url"], p.get("ms"), new_cookies=p.get("cookies") or {})
                elif kind == "store_cookie":
                    _store_cookie(p["url"], p.get("cookies") or {})
                else:
                    continue
                n += 1
            except Exception:  # noqa: BLE001 —— 单条效应坏了不拖垮整批
                continue
        return n


def fetch_stale() -> bool:
    """本次抓取期间切换了存储目录 → 调用方应中止剩余抓取工作。

    `_write_allowed()` 只挡**写入**（旧经验不落进新目录），挡不住已经跑起来的
    三级引擎——不主动检查的话，切库后后台还会继续烧浏览器/超时跑完整条链。
    未绑定纪元（脚本直调）返回 False。
    """
    epoch = getattr(_FETCH_EPOCH, "epoch", None)
    return epoch is not None and epoch != records.storage_epoch()


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
    if not _write_allowed():
        return
    if _record_effect("touch", {"url": url, "key": key, "value": value}):
        return
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
    """全部经验行，供 /api/domains 附带给前端展示（系统实测面板的数据源）。

    每行：`direct`/`proxy`/`updated`（可达性结论）+ **`strategy`**（当前活跃方案 id）+
    **`plan`**（有没有可用抓取方案 = 活跃策略 **或** 已存 Cookie——Cookie 直取也算方案；
    只暴露布尔，Cookie 值本身依旧不进 API）+ **`confirm`**（方案确认阶段的结论
    `{ok, at, reason}`，null = 未确认过）。前端 `autoFetchEligible` 按 `plan` 放行
    自动抓取：先确认、后抓取。
    """
    with _LOCK:
        out = {}
        for domain, row in _load()["hosts"].items():
            sid = (row.get("strategy") or {}).get("id")
            out[domain] = {
                "direct": row.get("direct"),
                "proxy": row.get("proxy"),
                "updated": row.get("updated", ""),
                "strategy": sid,
                "plan": bool(sid) or bool((row.get("cookie") or {}).get("value")),
                "confirm": row.get("confirm"),
            }
        return out


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
    """代理转发是否可用（带 TTL 缓存：通过 30s / 失败 15s）。

    区别于端口探测：端口在 ≠ 能连到远程。这里识别「端口开着但代理坏/上游挂」。
    **探测绝不持有 `_LOCK`**——原来 probe 跑在锁内，一次最多 5 秒，期间所有
    经验读写（含代理面板的 GET /api/domains → all_hints）全部排队，抓取线程
    就这样把面板卡住了。现在只用独立的 `_PROBE_LOCK` 串行化探测本身。
    """
    now = time.time()
    with _LOCK:
        hit = _HEALTH.get(proxy)
        if hit is not None and now - hit[1] < (_PROXY_TTL_OK if hit[0] else _PROXY_TTL_FAIL):
            return hit[0]
    with _PROBE_LOCK:
        with _LOCK:                       # 双检：等锁期间可能已被别人探好
            hit = _HEALTH.get(proxy)
            if hit is not None and now - hit[1] < (_PROXY_TTL_OK if hit[0] else _PROXY_TTL_FAIL):
                return hit[0]
        ok = _probe(proxy, timeout)       # ← 网络 I/O：锁外进行
        with _LOCK:
            _HEALTH[proxy] = (ok, time.time())
    return ok


# ---------------------------------------------------------------- 代理端口顶替

_DETECT_TIMEOUT = 2.0     # 端口逐个验证转发的单端口超时（端口间并行，总耗时≈单端口）
_ENSURE_TTL = 20           # 「坏端口 → 顶替结论」缓存：一条补抓轮里只做一次完整探测
_ENSURE: dict | None = None    # {"in": 输入地址, "out": 顶替结果, "at": 时刻}


def detect_working_proxy(host: str = "127.0.0.1") -> str | None:
    """在 7889-7899 里找一个**转发真正可用**的本机代理端口（编号最小者）。

    两步：① TCP 并行探开（`scan_open_ports`，约 0.25s）；② 对开着的端口**并行**做
    真实转发验证（走 HTTPS CONNECT 的小请求）。只做 ① 的话，端口开着但上游挂了的
    坏端口会被当成可用填回去——正是「代理不好使还一直用旧端口」的来源。
    验证结论顺手写进 `_HEALTH` 缓存，后续 `proxy_reachable` 直接命中。
    """
    ports = scan_open_ports(host)
    if not ports:
        return None
    urls = [f"http://{host}:{p}" for p in ports]
    # 直接并行 _probe（proxy_reachable 的探测在锁内，池化会串行化），结果回填缓存
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        oks = list(pool.map(lambda u: _probe(u, _DETECT_TIMEOUT), urls))
    now = time.time()
    with _LOCK:
        for u, ok in zip(urls, oks):
            _HEALTH[u] = (ok, now)
    for p, ok in zip(ports, oks):
        if ok:
            if p != ports[0]:
                logger.debug(f"端口 {ports[0]} 等转发不通，跳过，选用 {host}:{p}")
            return f"http://{host}:{p}"
    return None


def _is_loopback(proxy: str) -> bool:
    try:
        return urlparse(proxy).hostname in ("127.0.0.1", "localhost", "::1")
    except ValueError:
        return False


def ensure_working_proxy(proxy: str | None) -> str | None:
    """代理转发不通时，尝试在本机 7889-7899 找一个**转发可用**的新端口顶替。

    - 传 None / 空 → 原样返回（没配代理不归这里管）；
    - 当前地址健康 → 原样返回（健康结论走 proxy_reachable 的 30s/15s 缓存）；
    - 当前地址是**本机回环**且转发不通 → 顶替成探测到的可用端口（找不到就保持原地址，
      由 fetch_page 照旧降级直连）；远程地址不做本地端口猜测；
    - 结论缓存 `_ENSURE_TTL` 秒：一轮补抓里不会每条记录都重跑完整探测。
    """
    global _ENSURE
    if not proxy:
        return proxy
    now = time.time()
    with _LOCK:
        cached = _ENSURE
        if cached and cached["in"] == proxy and now - cached["at"] < _ENSURE_TTL:
            return cached["out"]
    if proxy_reachable(proxy):
        out = proxy
    elif not _is_loopback(proxy):
        out = proxy                      # 远程代理：本机端口区间与它无关，不瞎换
    else:
        alt = detect_working_proxy()
        out = alt if (alt and alt != proxy) else proxy
        if out != proxy:
            logger.info(f"代理 {proxy} 转发不通，已自动顶替为可用端口 {out}")
    with _LOCK:
        _ENSURE = {"in": proxy, "out": out, "at": time.time()}
    return out


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
    if not _write_allowed():
        return
    if _record_effect("save_strategy", {"url": url, "strategy_id": strategy_id, "ms": ms}):
        return
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
    if not _write_allowed():
        return
    if _record_effect("drop_strategy", {"url": url, "strategy_id": strategy_id}):
        return
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
    if not _write_allowed():
        return
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


def touch_cookie(url: str, ms: int | None = None, response=None, *, new_cookies: dict | None = None) -> None:
    """Cookie 直取成功：刷新 last_success、记耗时（best_ms 取最小）；
    响应里若带了新的 Set-Cookie（会话轮换），合并进已存的值里。

    `new_cookies`：效应重放入口——子进程把响应里的 Cookie 解析成 dict 带回
    （scrapling Response 不可序列化），主进程落地时用它代替 response。
    """
    if not _write_allowed():
        return
    new = dict(new_cookies) if new_cookies is not None else _resp_cookies(response)
    if _record_effect("touch_cookie", {"url": url, "ms": ms, "cookies": new}):
        return
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
    if not _write_allowed():
        return
    if _record_effect("drop_cookie", {"url": url}):
        return
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
    return _store_cookie(url, new)


def _store_cookie(url: str, new: dict) -> bool:
    """把一组已解析的 Set-Cookie 合并进缓存（`store_cookie_from_response` 的本体，
    也是效应重放的落地入口——子进程带回的只能是 dict，不是 Response）。"""
    if not _write_allowed():
        return False
    if _record_effect("store_cookie", {"url": url, "cookies": new}):
        return True
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


# ---------------------------------------------------------------- 方案确认（confirm 阶段）

def domain_plan(url: str) -> bool:
    """该域名当前**有没有可用抓取方案**：活跃策略 或 已存 Cookie（Cookie 直取也算方案）。

    入参既可以是完整 URL 也可以是裸域名（`_domain()` 对裸名原样返回）。
    这是自动抓取的放行判据——`plan=True` 才允许自动抓这一组（先确认、后抓取）。
    """
    with _LOCK:
        row = _load()["hosts"].get(_domain(url), {})
        return bool((row.get("strategy") or {}).get("id")) or bool(
            (row.get("cookie") or {}).get("value")
        )


def confirm_domain(url: str, ok: bool, reason: str = "") -> None:
    """写入该域名的方案确认结论（confirm 阶段专用，主进程直接落盘）。

    结论**持久且一次性**：`ok=False`（多路尝试均不通）→ 自动抓取整组跳过、
    之后不再重测；`ok=True` 后若方案又失效（策略被弃用、Cookie 被清），
    `domain_plan()` 变 False 而 confirm 仍在 → 同样跳过、不再重试（用户语义：
    确认过但当前没有方案，就不要再尝试了）。
    """
    if not _write_allowed():
        return
    domain = _domain(url)
    now = datetime.now().strftime(_TS_FMT)
    with _LOCK:
        row = _load()["hosts"].setdefault(domain, {})
        row["confirm"] = {"ok": bool(ok), "at": now, "reason": reason or ""}
        row["updated"] = now
        _save()


def get_confirm(url: str) -> dict | None:
    """该域名的确认结论 {ok, at, reason}；从未确认过返回 None。"""
    with _LOCK:
        c = _load()["hosts"].get(_domain(url), {}).get("confirm")
        return dict(c) if isinstance(c, dict) else None


def _on_storage_changed() -> None:
    """切库监听（由 records.set_storage_dir 触发）：丢弃内存中的经验缓存——
    下一次 _load 落到**新目录**（新目录没有本文件就从空开始，绝不带旧项目的学习）。"""
    global _DATA
    _DATA = None


records.on_storage_changed(_on_storage_changed)
