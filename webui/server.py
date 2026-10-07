"""主项目 Web 看板服务：Raindrop 风格书签卡片，端口默认 4509。

数据与 CLI / Qt 版共用 metadata.json；抓取复用 metadata_fetcher 的三级降级链。

用法：python webui/server.py [--port 4509]
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from curl_cffi.requests import get as http_get

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT.parent))  # 主项目目录，复用 records / metadata_fetcher

from metadata_fetcher import get_metadata  # noqa: E402
from records import STORE_PATH, load_records, merge_record, save_records  # noqa: E402

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
STATIC_FILES = {"/": "index.html", "/index.html": "index.html", "/style.css": "style.css", "/app.js": "app.js"}


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

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/records":
            self._send_json({"records": load_records(STORE_PATH)})
            return
        if parsed.path == "/api/img":
            self.handle_img(parse_qs(parsed.query))
            return
        name = STATIC_FILES.get(parsed.path)
        if name is None:
            self.send_error(404)
            return
        file_path = ROOT / name
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES[file_path.suffix])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_img(self, qs: dict) -> None:
        """服务端代抓图片（走本地代理），避免浏览器直连外站被重置。"""
        src = (qs.get("src") or [""])[0]
        proxy = (qs.get("proxy") or [""])[0].strip() or None
        if not src.startswith(("http://", "https://")):
            self.send_error(400)
            return
        try:
            resp = http_get(src, proxy=proxy, timeout=10)
        except Exception:
            self.send_error(502)
            return
        body = resp.content
        ctype = resp.headers.get("content-type", "application/octet-stream").split(";")[0]
        self.send_response(resp.status_code if 100 <= (resp.status_code or 0) < 600 else 502)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    # ---------- 业务 ----------

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/fetch":
            self.send_error(404)
            return
        data = self._read_json()
        url = (data.get("url") or "").strip()
        proxy = (data.get("proxy") or "").strip() or None
        if not url.startswith(("http://", "https://")):
            self._send_json({"ok": False, "error": "网址必须以 http:// 或 https:// 开头"}, 400)
            return

        meta = get_metadata(url, proxy=proxy)
        records = merge_record(load_records(STORE_PATH), meta)
        save_records(STORE_PATH, records)
        merged = next(r for r in records if r["url"] == url)
        self._send_json({"ok": True, "record": merged})

    def do_PATCH(self) -> None:
        if urlparse(self.path).path != "/api/record":
            self.send_error(404)
            return
        data = self._read_json()
        url = (data.get("url") or "").strip()
        if "title" not in data:
            self._send_json({"ok": False, "error": "缺少 title"}, 400)
            return
        records = load_records(STORE_PATH)
        for record in records:
            if record["url"] == url:
                record["title"] = (data.get("title") or "").strip()
                save_records(STORE_PATH, records)
                self._send_json({"ok": True, "record": record})
                return
        self._send_json({"ok": False, "error": "记录不存在"}, 404)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/record":
            self.send_error(404)
            return
        url = (parse_qs(parsed.query).get("url") or [""])[0]
        records = load_records(STORE_PATH)
        remaining = [r for r in records if r["url"] != url]
        if len(remaining) == len(records):
            self._send_json({"ok": False, "error": "记录不存在"}, 404)
            return
        save_records(STORE_PATH, remaining)
        self._send_json({"ok": True})

    def log_message(self, fmt: str, *args) -> None:
        print("[webui]", self.address_string(), fmt % args)


def main() -> None:
    port = 4509
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Web 看板已启动：http://127.0.0.1:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
