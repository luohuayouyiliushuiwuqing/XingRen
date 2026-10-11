"""主项目 Web 看板服务：Raindrop 风格书签卡片，默认监听 0.0.0.0:4000。

数据存 SQLite（仓库根 metadata.db）；抓取复用 almond.core.fetcher 的三级降级链。

用法：python -m almond.web.webui.server [--host 0.0.0.0] [--port 4000]   （在仓库根目录执行）
     或安装后直接 almond-webui [--host …] [--port …]
"""

import json
import multiprocessing
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from almond.core import confirm, fetch_hints, fetchpool
from almond.core.log import logger
from almond.core.proxy import detect_proxy
from almond.core.records import (
    add_tag_to_record, count_records, create_tag, delete_proxy_rule, delete_record,
    delete_tag, get_domain_fetch_config, get_domain_need_proxy, get_storage_paths,
    insert_quick_records, list_domains, list_pending_urls, list_proxy_domains,
    list_proxy_rules, list_records, list_tags,
    match_proxy_rule, remove_storage_history, remove_tag_from_record, replace_domain,
    set_storage_dir, set_title, storage_epoch, update_domain, upsert_proxy_rule,
    upsert_record_if_epoch,
)
from almond.web.webui import fsbrowse, imgproxy
from almond.web.webui.staticfiles import CONTENT_TYPES, resolve_static

def effective_proxy(url: str, global_proxy: str) -> str | None:
    """代理优先级：URL 模式规则 > 域名规则 > 全局代理默认值。

    映射只表达「要不要走代理」，实际地址统一取 global_proxy。
    规则命中即为强制值——need_proxy=0 表示强制直连，不再向下兜底。

    放在模块级而不是 Handler 方法上：`imgproxy` 也要用同一套判定，
    而它不能 import 本模块（会循环）——由 Handler 解析好再传进去。
    """
    hit, need = match_proxy_rule(url)
    if hit:
        return (global_proxy or None) if need else None
    domain_need = get_domain_need_proxy(url)
    if domain_need is not None:
        return (global_proxy or None) if domain_need else None
    return global_proxy or None


class Handler(BaseHTTPRequestHandler):
    # ---------- 基础 ----------

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _safe(self, handler) -> None:
        """处理器统一兜底：任何异常回 500 JSON，而不是直接掐断连接。"""
        try:
            handler()
        except (BrokenPipeError, ConnectionResetError):
            # 客户端提前断开是常态（图片超时会被浏览器取消）——
            # 放出去只会让 socketserver 打一整段 traceback，这里一行带过
            self.log_message("client disconnected")
        except Exception as exc:
            try:
                self._send_json({"ok": False, "error": str(exc)}, 500)
            except Exception:
                pass

    def do_GET(self) -> None:
        self._safe(self._do_GET)

    def do_POST(self) -> None:
        self._safe(self._do_POST)

    def do_PATCH(self) -> None:
        self._safe(self._do_PATCH)

    def do_DELETE(self) -> None:
        self._safe(self._do_DELETE)

    def _do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/records":
            # epoch 一并返回：前端开抓前把它带上（/api/fetch 的请求级纪元校验），
            # 页面加载与切库后的 loadRecords 都会顺带刷新
            self._send_json({"records": list_records(), "epoch": storage_epoch()})
            return
        if path == "/api/records/pending":
            # 自动补抓的轮询源：页面开着时，别处（另一标签页/插件/导入）新进库的
            # 待抓链接靠它被发现；只回 url，比每 10 秒拉一次全量 /api/records 轻得多
            self._send_json({"urls": list_pending_urls()})
            return
        if path == "/api/domains":
            # tested = 抓取经验缓存里的系统实测结论（fetch_hints，只读；域名无数据则为 null）
            tested = fetch_hints.all_hints()
            domains = list_domains()
            for d in domains:
                d["tested"] = tested.get(d["name"])
            self._send_json({"domains": domains})
            return
        if path == "/api/tags":
            self._send_json({"tags": list_tags()})
            return
        if path == "/api/proxy-rules":
            self._send_json({"rules": list_proxy_rules()})
            return
        if path == "/api/proxy-domains":
            self._send_json({"domains": list_proxy_domains()})
            return
        if path == "/api/proxy-detect":
            # 每次都重新探测：TCP 探开 + **真实转发验证**（并行，坏端口不会被填回去）
            self._send_json({"ok": True, "proxy": fetch_hints.detect_working_proxy()})
            return
        if path in ("/api/storage-dir", "/api/db-path"):  # db-path 为旧路径兼容
            self._send_json(get_storage_paths())
            return
        if path == "/api/fs/list":
            self._send_json(fsbrowse.fs_list((qs.get("path") or [""])[0]))
            return
        if path == "/api/img":
            self.handle_img(qs)
            return
        file_path = resolve_static(path)
        if file_path is None:
            self.send_error(404)
            return
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES[file_path.suffix])
        self.send_header("Content-Length", str(len(body)))
        # 静态文件禁缓存：改完代码浏览器总能拿到新版（否则旧 tab 会一直跑旧 JS）
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_img(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 只有成功才允许浏览器缓存一天：给 4xx/5xx 也发 max-age=86400 的话，
        # 一次超时会让缩略图一整天都修不好（浏览器不再重试）
        self.send_header(
            "Cache-Control", "public, max-age=86400" if 200 <= status < 300 else "no-store"
        )
        self.end_headers()
        self.wfile.write(body)

    def handle_img(self, qs: dict) -> None:
        """服务端代抓图片：解析代理 → 交给 imgproxy 取字节 → 写响应。"""
        src = (qs.get("src") or [""])[0]
        global_proxy = (qs.get("proxy") or [""])[0].strip() or ""
        if not src.startswith(("http://", "https://")):
            self.send_error(400)
            return
        proxy = effective_proxy(src, global_proxy)
        status, body, ctype = imgproxy.handle_img(src, proxy)
        self._send_img(body, ctype, status)

    # ---------- 业务 ----------

    def _do_POST(self) -> None:
        path = urlparse(self.path).path
        data = self._read_json()

        if path == "/api/fetch":
            url = (data.get("url") or "").strip()
            global_proxy = (data.get("proxy") or "").strip() or ""
            if not url.startswith(("http://", "https://")):
                self._send_json({"ok": False, "error": "网址必须以 http:// 或 https:// 开头"}, 400)
                return

            # ① 请求级纪元（先于一切网络动作捕获）：客户端在请求体带上它开抓时的
            #    存储纪元。切库**之后**才到达 / 才被线程调度执行的旧轮请求——
            #    abort() 只断浏览器连接，服务端可能早已收到完整请求体——在这里
            #    直接作废、一个包都不发。没有这层时，这类请求的 start_epoch 读到的
            #    已经是新纪元，会把旧目录的 URL 抓完写进新库（污染 B 库）。
            start_epoch = storage_epoch()
            req_epoch = data.get("epoch")
            if isinstance(req_epoch, int) and req_epoch != start_epoch:
                logger.info(
                    f"丢弃旧存储纪元的抓取请求（epoch {req_epoch} ≠ 当前 {start_epoch}）："
                    f"{urlparse(url).hostname}"
                )
                self._send_json({
                    "ok": True, "stale": True, "success": False,
                    "error": "存储已切换，旧目录的抓取请求已丢弃",
                })
                return

            proxy = effective_proxy(url, global_proxy)
            # 域名级抓取规则：详情选择器 + 封面开关。
            # 代理不用这里的 need_proxy——effective_proxy 已经按
            # 「URL 模式规则 > 域名规则 > 全局」算好了，再覆盖会打乱优先级
            rules = get_domain_fetch_config(url)
            # 代理健康顶替：配置的端口转发不通 → 本机区间找可用端口换上，
            # 并通过 proxy_swapped 回传给前端（前端跟上地址，不再反复撞坏端口）。
            # 顶替动作的日志由 ensure_working_proxy 统一打
            if proxy:
                proxy = fetch_hints.ensure_working_proxy(proxy)
            # 抓取本体跑在**子进程**（fetchpool）：切库时 cancel_all 硬杀进程树
            # ——正在跑的引擎调用当场中断，不用等它超时；Web 进程也不再有抓取的
            # GIL/锁争用。子进程带回的缓存写入（效应）经纪元校验后才落地。
            job = fetchpool.submit(url=url, proxy=proxy,
                                   selector=rules["detail_selector"] or None,
                                   cover=rules["cover"])
            try:
                got = fetchpool.wait(job)
            except TimeoutError:
                self._send_json({"ok": False, "error": "抓取子进程无响应（已放弃本次抓取）"}, 500)
                return
            if got is None:
                # 子进程被硬停（切库）→ 本次抓取作废
                self._send_json({
                    "ok": True, "stale": True, "success": False,
                    "error": "存储已切换，抓取子进程已硬停，结果作废",
                })
                return
            meta, effects = got
            if storage_epoch() != start_epoch:
                self._send_json({
                    "ok": True, "stale": True, "success": False,
                    "error": "存储已切换，本次抓取结果已丢弃",
                })
                return
            fetch_hints.apply_effects(effects, epoch=start_epoch)   # 学习落地（纪元已复核）
            if meta is None:
                # 子进程内部异常（不是切库）：如实报失败，不做写入
                self._send_json({"ok": False, "error": "抓取子进程异常，未取得结果"})
                return
            # 原子写入：比对纪元 + 写库一体执行（与切库互斥），堵住
            # 「守卫比对通过 → 切库发生 → 写进新库」的最后一段窗口
            merged = upsert_record_if_epoch(meta, start_epoch)
            if merged is None:
                self._send_json({
                    "ok": True, "stale": True, "success": False,
                    "error": "存储已切换，本次抓取结果已丢弃",
                })
                return
            fetch_hints.apply_effects(effects, epoch=start_epoch)
            # success 说的是**本次抓取**：record.success 是合并后的值，失败时按合并规则
            # 保留快照/旧内容（可能仍是 1），拿它计数会把失败全算成成功
            payload = {"ok": True, "record": merged, "success": bool(meta.get("success"))}
            if proxy and proxy != global_proxy:
                payload["proxy_swapped"] = proxy
            self._send_json(payload)
            return

        if path == "/api/tag":
            name = (data.get("name") or "").strip()
            if not name:
                self._send_json({"ok": False, "error": "缺少标签名"}, 400)
                return
            tag = create_tag(name)
            self._send_json({"ok": True, "tag": tag})
            return

        if path == "/api/record/tag":
            url = (data.get("url") or "").strip()
            tag_id = data.get("tag_id")
            if not url or not tag_id:
                self._send_json({"ok": False, "error": "缺少 url 或 tag_id"}, 400)
                return
            add_tag_to_record(url, tag_id)
            self._send_json({"ok": True})
            return

        if path == "/api/proxy-rule":
            pattern = (data.get("pattern") or "").strip()
            if not pattern:
                self._send_json({"ok": False, "error": "缺少匹配模式"}, 400)
                return
            rule = upsert_proxy_rule(pattern, bool(data.get("need_proxy", True)))
            self._send_json({"ok": True, "rule": rule})
            return

        if path == "/api/records/quick":
            # 快照导入：只写已有元数据，不联网；details 留待打开详情时按需补抓
            items = data.get("items") or []
            if not isinstance(items, list) or not items:
                self._send_json({"ok": False, "error": "缺少 items"}, 400)
                return
            result = insert_quick_records(items)
            self._send_json({"ok": True, **result})
            return

        if path == "/api/proxy-detect":
            self._send_json({"ok": True, "proxy": fetch_hints.detect_working_proxy()})
            return

        if path == "/api/confirm":
            # 方案确认阶段（自动抓取的最前面）：没有可用方案的域名，各挑一条真实
            # 网址跑一次测试抓取，成功学出方案、失败写持久结论——前端据此整组放行/跳过
            domains = data.get("domains")
            if domains is not None and not isinstance(domains, list):
                self._send_json({"ok": False, "error": "domains 必须是数组"}, 400)
                return
            start_epoch = storage_epoch()
            req_epoch = data.get("epoch")
            if isinstance(req_epoch, int) and req_epoch != start_epoch:
                self._send_json({"ok": True, "stale": True,
                                 "error": "存储已切换，方案确认作废"})
                return
            gp = (data.get("proxy") or "").strip()
            report = confirm.confirm_domains(
                domains=domains,
                proxy_for=lambda u: effective_proxy(u, gp),
                start_epoch=start_epoch,
            )
            if report.get("aborted") or storage_epoch() != start_epoch:
                self._send_json({"ok": True, "stale": True,
                                 "error": "存储已切换，方案确认作废"})
                return
            self._send_json({"ok": True, "epoch": storage_epoch(), **report})
            return

        if path in ("/api/storage-dir", "/api/db-path"):
            # 切换存储目录：**不迁移**——旧数据留在原地，新目录从零开始；
            # 纪元 +1 作废在途抓取，fetch_hints 同步丢弃内存缓存
            new_dir = (data.get("path") or "").strip()
            t0 = time.monotonic()
            try:
                result = set_storage_dir(new_dir)
            except (ValueError, OSError) as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
                return
            t1 = time.monotonic()
            n = count_records()
            # 分段计时：切换慢时先看是 set（配置/目标库校验）还是 count（新库首开
            # 的 domains 回填在 _ensure 里）在耗时，不用猜
            logger.info(
                f"存储目录已切换 → {result['path']}（set {int((t1 - t0) * 1000)}ms，"
                f"count {int((time.monotonic() - t1) * 1000)}ms，{n} 条）"
            )
            self._send_json({"ok": True, **result, "records": n})
            return

        if path == "/api/domain/replace":
            # 域名重置：原域名（含子域名）整体替换为新域名，事务内一次改完
            try:
                result = replace_domain(data.get("old") or "", data.get("new") or "")
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
                return
            self._send_json({"ok": True, **result})
            return

        self.send_error(404)

    def _do_PATCH(self) -> None:
        path = urlparse(self.path).path
        data = self._read_json()

        if path == "/api/record":
            url = (data.get("url") or "").strip()
            if "title" not in data:
                self._send_json({"ok": False, "error": "缺少 title"}, 400)
                return
            record = set_title(url, data.get("title") or "")
            if record is None:
                self._send_json({"ok": False, "error": "记录不存在"}, 404)
                return
            self._send_json({"ok": True, "record": record})
            return

        if path == "/api/domain":
            name = (data.get("name") or "").strip()
            if not name:
                self._send_json({"ok": False, "error": "缺少域名"}, 400)
                return
            # 只透传请求体里出现的字段（后端按 _UNSET 语义跳过没给的）：
            # need_proxy / auto_fetch / cover 是三态（null = 跟随全局 / 默认）
            kwargs = {}
            for key in ("display_name", "need_proxy", "prefs",
                        "auto_fetch", "detail_selector", "cover"):
                if key in data:
                    kwargs[key] = data.get(key)
            try:
                domain = update_domain(name, **kwargs)
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
                return
            self._send_json({"ok": True, "domain": domain})
            return

        self.send_error(404)

    def _do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/api/record":
            url = (qs.get("url") or [""])[0]
            if not delete_record(url):
                self._send_json({"ok": False, "error": "记录不存在"}, 404)
                return
            self._send_json({"ok": True})
            return

        if path == "/api/tag":
            tag_id = (qs.get("id") or [""])[0]
            if not tag_id or not delete_tag(int(tag_id)):
                self._send_json({"ok": False, "error": "标签不存在"}, 404)
                return
            self._send_json({"ok": True})
            return

        if path == "/api/record/tag":
            url = (qs.get("url") or [""])[0]
            tag_id = (qs.get("tag_id") or [""])[0]
            if not url or not tag_id:
                self._send_json({"ok": False, "error": "缺少 url 或 tag_id"}, 400)
                return
            remove_tag_from_record(url, int(tag_id))
            self._send_json({"ok": True})
            return

        if path == "/api/proxy-rule":
            rule_id = (qs.get("id") or [""])[0]
            if not rule_id or not delete_proxy_rule(int(rule_id)):
                self._send_json({"ok": False, "error": "规则不存在"}, 404)
                return
            self._send_json({"ok": True})
            return

        if path == "/api/storage-history":
            # 从历史里移除一条目录记录（当前目录由后端强制置顶，删不掉）
            removed = (qs.get("path") or [""])[0]
            self._send_json({"ok": True, "history": remove_storage_history(removed)})
            return

        self.send_error(404)

    def log_message(self, fmt: str, *args) -> None:
        # 访问日志走 DEBUG：补抓轮询每 10s 打一次，默认 INFO 下不刷屏
        logger.debug(f"{self.address_string()} {fmt % args}")


def _opt(name: str, default: str) -> str:
    """取 `--name value` 形式的参数；缺失或没跟值时回退默认值。"""
    if name not in sys.argv:
        return default
    idx = sys.argv.index(name)
    return sys.argv[idx + 1] if idx + 1 < len(sys.argv) else default


def main() -> None:
    # spawn 拉起抓取子进程时会重入本模块：freeze_support 让子进程在此短路退出，
    # 不会把 Web 服务再启动一遍（console 入口没有 __main__ 保护时尤其需要）
    multiprocessing.freeze_support()
    host = _opt("--host", "0.0.0.0")
    port = int(_opt("--port", "4000"))
    server = ThreadingHTTPServer((host, port), Handler)
    logger.info(f"Web 看板已启动：http://{host}:{port}/")
    if host == "0.0.0.0":
        logger.info(f"本机访问：http://127.0.0.1:{port}/")
        logger.info("已监听所有网卡且无鉴权；仅本机使用请加 --host 127.0.0.1")
    # 自动探测本地代理端口（7889-7899），只做提示，不改变任何配置
    found = detect_proxy()
    logger.info(f"代理探测（7889-7899）：{found or '未发现本地代理'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
