"""统一日志出口（loguru）：收编 Almond 的 print、scrapling 与一切标准 logging。

格式（竖线分栏，含日期毫秒）——用户选定：
    2026-10-10 23:53:46.123 | INFO    | fetcher.py:67 | 消息…

- 级别在终端着色（DEBUG 灰 / INFO 绿 / WARNING 黄 / ERROR 红）；
  colorize 走 loguru 默认的 tty 判定：进终端才带 ANSI，重定向到文件自动去色。
- 出口是 **stdout**：playwright 那两行噪音写 stderr，天然分离——
  `2>/dev/null` 能只屏蔽噪音而不丢日志。
- 默认级别 INFO；环境变量 `ALMOND_LOG_LEVEL=DEBUG` 或 `set_level("DEBUG")` 打开调试行。
- scrapling 走标准 logging（其 `scrapling` logger 在 `_utils.setup_logger()` 装过一份
  `[时间] 级别: 消息` 的 handler，lru_cache 保证只装一次）：这里先促使其装配、
  再清掉全部既有 handler 挂 InterceptHandler——它的日志会以**同一格式**进来，
  `{file}:{line}` 指向 scrapling 里真正发起调用的那一行，不会双打。

用法：各模块 `from almond.core.log import logger`，然后
`logger.debug/info/warning/error("…")`（loguru 还有 `success`，测试 PASS 用它）。
"""
from __future__ import annotations

import logging
import os
import sys

from loguru import logger as _loguru

# {level:<7}：DEBUG/INFO(WARNING=7)/ERROR 全词对齐；<level> 标签 = 按级别着色
_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | <level>{level:<7}</level> | {file}:{line} | {message}"
_SINK_ID: int | None = None

_STDLIB_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class _Intercept(logging.Handler):
    """把标准 logging（scrapling 等）的记录搬进 loguru，文件:行定位到原始调用方。"""

    # playwright 同步 API 关闭时 asyncio 必打的两条，属固有噪音（原文在 stderr 见过无数遍）
    _NOISE = ("Task was destroyed but it is pending!",
              "Future exception was never retrieved")

    def emit(self, record):  # noqa: D102
        try:
            if record.name == "asyncio" and record.getMessage().startswith(self._NOISE):
                return
            level = record.levelname if record.levelname in _STDLIB_LEVELS else "INFO"
            # 栈：emit(本文件) ← Handler.handle ← callHandlers ← Logger.handle ← _log ← logger.x ← 调用方
            # 从 emit 起数：自身 + 全部 logging 内部帧 = 到调用方要跳的层数（本环境实测 6）
            depth = 1
            frame = logging.currentframe().f_back
            while frame and frame.f_code.co_filename == logging.__file__:
                depth += 1
                frame = frame.f_back
            _loguru.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())
        except Exception:  # noqa: BLE001 —— 日志失败绝不炸业务
            self.handleError(record)


def set_level(level: str) -> None:
    """调整输出级别（DEBUG/INFO/WARNING/…）：换 sink 即可，loguru 按 sink 过滤。"""
    global _SINK_ID
    if _SINK_ID is not None:
        _loguru.remove(_SINK_ID)
    _SINK_ID = _loguru.add(
        sys.stdout,
        format=_FORMAT,
        level=(level or "INFO").upper(),
        backtrace=False,
        diagnose=False,
        # colorize 不传 = None → loguru 按 sink 是否 tty 自动决定着色
    )


def _setup() -> None:
    _loguru.remove()                                   # 摘掉 loguru 默认 handler
    set_level(os.environ.get("ALMOND_LOG_LEVEL", "INFO"))

    # 先促使其装好自带 handler（若 scrapling 还没被 import 过）；
    # setup_logger 是 lru_cache，body 只会跑这一次，之后清掉就永远不会再加回来
    try:
        import scrapling.core.utils._utils  # noqa: F401
    except Exception:  # noqa: BLE001 —— 没装 scrapling 的环境照样能用
        pass
    logging.basicConfig(handlers=[_Intercept()], level=logging.DEBUG, force=True)
    for name in list(logging.root.manager.loggerDict):
        lg = logging.getLogger(name)
        lg.handlers.clear()                            # scrapling 自带的那份在这里被清掉
        lg.setLevel(logging.DEBUG)
        lg.propagate = True                             # 统一上浮到根上的拦截器


_setup()

# 各模块统一用：from almond.core.log import logger
logger = _loguru
