"""主项目 Web 看板服务：Raindrop 风格书签卡片，默认监听 0.0.0.0:4000。

数据存 SQLite（仓库根 metadata.db）；抓取复用 xingren.core.fetcher 的三级降级链。

用法：python -m xingren.web.webui.server [--host 0.0.0.0] [--port 4000]   （在仓库根目录执行）
     或安装后直接 xingren-webui [--host …] [--port …]
"""

import json
import os
import sys
import time
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from curl_cffi.requests import get as http_get

from xingren.core.fetcher import _is_domestic, get_metadata
from xingren.core.proxy import detect_proxy
from xingren.core.records import (
    add_tag_to_record, create_tag, delete_proxy_rule, delete_record, delete_tag,
    get_cache_dir, get_domain_need_proxy, get_storage_paths, insert_quick_records,
    list_domains, list_proxy_domains, list_proxy_rules, list_records, list_tags,
    match_proxy_rule, remove_tag_from_record, set_storage_dir, set_title, update_domain,
    upsert_proxy_rule, upsert_record,
)

ROOT = Path(__file__).parent  # 静态文件与本 server.py 同目录，与是否安装无关

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
STATIC_FILES = {"/": "index.html", "/index.html": "index.html", "/style.css": "style.css", "/app.js": "app.js"}

# 图片代抓：单次尝试秒数（代理挂起时要快点认输，直连给慢 CDN 留余量）+
# 失败负缓存 TTL——浏览器每次 render 都会重建 <img>，没有负缓存的话
# 一张挂掉的图会反复拖住工作线程（实测一次 5~10s）
IMG_TIMEOUT_PROXY = 5
IMG_TIMEOUT = 8
IMG_FAIL_TTL = 60
_IMG_FAIL: dict[str, tuple[float, int, bytes, str]] = {}  # digest -> (失效时刻, status, body, ctype)


def _img_try(src: str, proxy: str | None) -> tuple[int, bytes, str]:
    """一次图片请求，返回 (status, body, content-type)；失败抛异常交给调用方降级。"""
    resp = http_get(src, proxy=proxy, timeout=IMG_TIMEOUT_PROXY if proxy else IMG_TIMEOUT)
    body = resp.content
    ctype = (resp.headers.get("content-type") or "application/octet-stream").split(";")[0]
    status = resp.status_code if 100 <= (resp.status_code or 0) < 600 else 502
    return status, body, ctype


def _remember_img_fail(digest: str, status: int, body: bytes, ctype: str) -> None:
    if len(_IMG_FAIL) > 400:  # 顺手清掉过期的，别无限涨
        now = time.monotonic()
        for k in [k for k, v in _IMG_FAIL.items() if v[0] <= now]:
            del _IMG_FAIL[k]
    _IMG_FAIL[digest] = (time.monotonic() + IMG_FAIL_TTL, status, body[:8192], ctype)


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

    def _effective_proxy(self, url: str, global_proxy: str) -> str | None:
        """代理优先级：URL 模式规则 > 域名规则 > 全局代理默认值。

        映射只表达「要不要走代理」，实际地址统一取 global_proxy。
        规则命中即为强制值——need_proxy=0 表示强制直连，不再向下兜底。
        """
        hit, need = match_proxy_rule(url)
        if hit:
            return (global_proxy or None) if need else None
        domain_need = get_domain_need_proxy(url)
        if domain_need is not None:
            return (global_proxy or None) if domain_need else None
        return global_proxy or None

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
            self._send_json(self._fs_list((qs.get("path") or [""])[0]))
            return
        if path == "/api/img":
            self.handle_img(qs)
            return
        name = STATIC_FILES.get(path)
        if name is None:
            self.send_error(404)
            return
        file_path = ROOT / name
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
        """服务端代抓图片（代理优先、失败回退直连），结果落本地缓存。"""
        src = (qs.get("src") or [""])[0]
        global_proxy = (qs.get("proxy") or [""])[0].strip() or ""
        if not src.startswith(("http://", "https://")):
            self.send_error(400)
            return

        digest = sha256(src.encode("utf-8")).hexdigest()[:32]
        cache_dir = get_cache_dir()  # 跟随存储目录，切换后自动指向新位置
        bin_path = cache_dir / f"{digest}.bin"
        ct_path = cache_dir / f"{digest}.ct"

        # 命中本地缓存：直接返回，不走网络
        if bin_path.is_file() and ct_path.is_file():
            self._send_img(bin_path.read_bytes(), ct_path.read_text(encoding="utf-8").strip())
            return

        # 负缓存：短时间内失败过的直接回，不让线程再干等一次超时
        # （每次 render 都会重建 <img>，没有这层的话每张挂掉的图都要再拖 5s）
        now = time.monotonic()
        failed = _IMG_FAIL.get(digest)
        if failed and failed[0] > now:
            self._send_img(failed[2], failed[3], failed[1])
            return

        proxy = self._effective_proxy(src, global_proxy)
        if proxy and _is_domestic(src):
            proxy = None

        status, body, ctype = 502, b"", "text/plain"
        if proxy:
            try:
                status, body, ctype = _img_try(src, proxy)
            except Exception:
                status = 502   # 代理挂起（实测会 hang 满超时）→ 下面试直连
            if not (200 <= status < 300):
                # 异常或代理吐了错误页，都再给直连一次机会；
                # 直连也失败就保留代理那次结果（不覆盖成更差的 502）
                try:
                    status, body, ctype = _img_try(src, None)
                except Exception:
                    pass
        else:
            try:
                status, body, ctype = _img_try(src, None)
            except Exception:
                pass

        # 只缓存成功的图片（4xx/5xx 不落盘，避免把错误页缓存住）
        if 200 <= status < 300 and body:
            cache_dir.mkdir(parents=True, exist_ok=True)
            bin_path.write_bytes(body)
            ct_path.write_text(ctype, encoding="utf-8")
            _IMG_FAIL.pop(digest, None)
        else:
            _remember_img_fail(digest, status, body, ctype)

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
            proxy = self._effective_proxy(url, global_proxy)
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

    def _fs_list(self, raw: str) -> dict:
        """目录浏览：空 path 返回顶层（Windows 列盘符，POSIX 列根目录）。"""
        if not raw:
            if os.name == "nt":
                roots = [f"{c}:\\" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if Path(f"{c}:\\").exists()]
            else:
                roots = ["/"]
            return {"ok": True, "path": "", "parent": "", "entries": roots}
        p = Path(raw).expanduser()
        try:
            p = p.resolve()
            if p.is_file():
                p = p.parent
            entries = sorted((d.name for d in p.iterdir() if d.is_dir()), key=str.lower)
        except OSError as exc:
            return {"ok": False, "error": f"无法读取目录：{exc}"}
        parent = str(p.parent) if p.parent != p else ""
        return {"ok": True, "path": str(p), "parent": parent, "entries": entries[:500]}

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
