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


def detect_proxy(host: str = "127.0.0.1") -> str | None:
    """探测本地代理端口，返回 `http://host:port`；区间内全部不通则返回 None。

    并行探测，按端口升序返回第一个可用的（即区间内最小的可用端口）。
    """
    with ThreadPoolExecutor(max_workers=len(PROXY_PORT_RANGE)) as pool:
        results = list(pool.map(_probe, PROXY_PORT_RANGE))
    for port in results:
        if port is not None:
            return f"http://{host}:{port}"
    return None
