"""主项目 Web 看板服务：Raindrop 风格书签卡片，默认监听 0.0.0.0:4000。

数据存 SQLite（仓库根 metadata.db）；抓取复用 xingren.core.fetcher 的三级降级链。

用法：python -m xingren.web.webui.server [--host 0.0.0.0] [--port 4000]   （在仓库根目录执行）
     或安装后直接 xingren-webui [--host …] [--port …]
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from xingren.core.fetcher import get_metadata
from xingren.core.proxy import detect_proxy
from xingren.core.records import (
    add_tag_to_record, create_tag, delete_proxy_rule, delete_record, delete_tag,
    get_domain_need_proxy, get_storage_paths, insert_quick_records,
    list_domains, list_proxy_domains, list_proxy_rules, list_records, list_tags,
    match_proxy_rule, remove_tag_from_record, set_storage_dir, set_title, update_domain,
    upsert_proxy_rule, upsert_record,
)
from xingren.web.webui import fsbrowse, imgproxy
from xingren.web.webui.staticfiles import CONTENT_TYPES, resolve_static

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
            self._send_json({"records": list_records()})
            return
        if path == "/api/domains":
            self._send_json({"domains": list_domains()})
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
            # 每次都重新探测（并行，约 0.25s），不缓存：代理可能刚启动
            self._send_json({"ok": True, "proxy": detect_proxy()})
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
            proxy = effective_proxy(url, global_proxy)
            meta = get_metadata(url, proxy=proxy)
            merged = upsert_record(meta)
            self._send_json({"ok": True, "record": merged})
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
            self._send_json({"ok": True, "proxy": detect_proxy()})
            return

        if path in ("/api/storage-dir", "/api/db-path"):
            # 切换存储目录：数据库、图片缓存等本地私有数据整体迁移，立即生效
            new_dir = (data.get("path") or "").strip()
            try:
                result = set_storage_dir(new_dir, migrate=bool(data.get("migrate", True)))
            except (ValueError, OSError) as exc:
                self._send_json({"ok": False, "error": str(exc)}, 400)
                return
            self._send_json({"ok": True, **result, "records": len(list_records())})
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
            kwargs = {}
            if "display_name" in data:
                kwargs["display_name"] = data.get("display_name")
            if "need_proxy" in data:
                # None = 无规则（跟随全局），True/False = 用代理/直连
                kwargs["need_proxy"] = data.get("need_proxy")
            domain = update_domain(name, **kwargs)
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

        self.send_error(404)

    def log_message(self, fmt: str, *args) -> None:
        print("[webui]", self.address_string(), fmt % args, flush=True)


def _opt(name: str, default: str) -> str:
    """取 `--name value` 形式的参数；缺失或没跟值时回退默认值。"""
    if name not in sys.argv:
        return default
    idx = sys.argv.index(name)
    return sys.argv[idx + 1] if idx + 1 < len(sys.argv) else default


def main() -> None:
    host = _opt("--host", "0.0.0.0")
    port = int(_opt("--port", "4000"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Web 看板已启动：http://{host}:{port}/", flush=True)
    if host == "0.0.0.0":
        print(f"  本机访问：http://127.0.0.1:{port}/", flush=True)
        print("  已监听所有网卡且无鉴权；仅本机使用请加 --host 127.0.0.1", flush=True)
    # 自动探测本地代理端口（7889-7899），只做提示，不改变任何配置
    found = detect_proxy()
    print(f"  代理探测（7889-7899）：{found or '未发现本地代理'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
