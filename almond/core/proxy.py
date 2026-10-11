"""本地代理端口自动探测。

在 7889-7899 区间并行尝试 TCP 连接，返回第一个可用端口的代理地址，
免去手动填 `http://127.0.0.1:7897` 这类细节。只探测本机回环地址。
"""

import socket
from concurrent.futures import ThreadPoolExecutor

# 待探测端口：7889 ~ 7899（含两端）
PROXY_PORT_RANGE = range(7889, 7900)
_PROBE_TIMEOUT = 0.25  # 单端口超时；并行执行，总耗时约等于单次超时


def _probe(port: int) -> int | None:
    """端口能建立 TCP 连接则返回端口号，否则返回 None。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=_PROBE_TIMEOUT):
            return port
    except OSError:
        return None


def scan_open_ports(host: str = "127.0.0.1") -> list[int]:
    """并行 TCP 探测区间内**端口开着**的端口，按升序返回。

    端口开着 ≠ 代理可用（端口在但上游挂了照样转发不通）——需要可用性结论时用
    `fetch_hints.detect_working_proxy()`（在本函数结果上再做真实转发验证）。
    """
    with ThreadPoolExecutor(max_workers=len(PROXY_PORT_RANGE)) as pool:
        results = list(pool.map(_probe, PROXY_PORT_RANGE))
    return [p for p in results if p is not None]


def detect_proxy(host: str = "127.0.0.1") -> str | None:
    """探测本地代理端口，返回 `http://host:port`；区间内全部不通则返回 None。

    并行探测，按端口升序返回第一个可用的（即区间内最小的可用端口）。
    仅 TCP 层：调用方若要「真正能转发」的端口，改用 `fetch_hints.detect_working_proxy()`。
    """
    ports = scan_open_ports(host)
    return f"http://{host}:{ports[0]}" if ports else None
