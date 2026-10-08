"""书签记录存储：SQLite（metadata.db）。

线程安全：每次操作独立短连接，读写走 WAL 模式。
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

# 数据文件始终放在仓库根：parents[0]=core, [1]=xingren, [2]=仓库根。
_DATA_DIR = Path(os.environ.get("XINGREN_DATA_DIR") or Path(__file__).resolve().parents[2])
DATA_DIR = _DATA_DIR
DB_PATH = _DATA_DIR / "metadata.db"

_SCHEMA_RECORDS = """
CREATE TABLE IF NOT EXISTS records (
    url        TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    thumbnail  TEXT NOT NULL DEFAULT '',
    favicon    TEXT NOT NULL DEFAULT '',
    success    INTEGER NOT NULL DEFAULT 0,
    details    TEXT NOT NULL DEFAULT '[]'
)
"""

_SCHEMA_DOMAINS = """
CREATE TABLE IF NOT EXISTS domains (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    proxy        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_SCHEMA_TAGS = """
CREATE TABLE IF NOT EXISTS tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_SCHEMA_RECORD_TAGS = """
CREATE TABLE IF NOT EXISTS record_tags (
    record_url TEXT NOT NULL,
    tag_id     INTEGER NOT NULL,
    PRIMARY KEY (record_url, tag_id),
    FOREIGN KEY (record_url) REFERENCES records(url) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
)
"""


def _root_domain(url: str) -> str:
    """从 URL 提取可注册域名（简化版：最后两段）。"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _row_to_record(row: sqlite3.Row, conn: sqlite3.Connection = None) -> dict:
    record = {
        "url": row["url"],
        "title": row["title"],
        "thumbnail": row["thumbnail"],
        "favicon": row["favicon"],
        "success": bool(row["success"]),
        "details": json.loads(row["details"] or "[]"),
        "domain": _root_domain(row["url"]),
        "tags": [],
    }
    if conn:
        tag_rows = conn.execute(
            "SELECT t.id, t.name FROM tags t "
            "JOIN record_tags rt ON rt.tag_id = t.id "
            "WHERE rt.record_url = ? ORDER BY t.name",
            (row["url"],),
        ).fetchall()
        record["tags"] = [{"id": r["id"], "name": r["name"]} for r in tag_rows]
    return record


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA_RECORDS)
    conn.execute(_SCHEMA_DOMAINS)
    conn.execute(_SCHEMA_TAGS)
    conn.execute(_SCHEMA_RECORD_TAGS)
    conn.execute("PRAGMA journal_mode=WAL")

    # 迁移：group_name 列 → tags 表（一次性）
    try:
        rows = conn.execute("SELECT url, group_name FROM records WHERE group_name != ''").fetchall()
        for row in rows:
            tag_name = row["group_name"]
            conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
            tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO record_tags (record_url, tag_id) VALUES (?, ?)",
                         (row["url"], tag_id))
        conn.execute("ALTER TABLE records DROP COLUMN group_name")
    except sqlite3.OperationalError:
        pass  # group_name 列已不存在

    # 自动填充 domains 表（从现有 records 提取域名）
    urls = conn.execute("SELECT DISTINCT url FROM records").fetchall()
    for row in urls:
        domain = _root_domain(row["url"])
        if domain:
            conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (domain,))


@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        _ensure(conn)
        with conn:
            yield conn
    finally:
        conn.close()


# ──────────────────────── Records ────────────────────────

def list_records() -> list[dict]:
    """全部记录，按插入顺序。"""
    with _db() as conn:
        rows = conn.execute("SELECT * FROM records ORDER BY rowid").fetchall()
        return [_row_to_record(row, conn) for row in rows]


def get_record(url: str) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM records WHERE url = ?", (url,)).fetchone()
        return _row_to_record(row, conn) if row else None


def upsert_record(new: dict) -> dict:
    """按 URL 合并写入；抓取失败或标题为空时保留已有数据。"""
    with _db() as conn:
        old = conn.execute("SELECT * FROM records WHERE url = ?", (new["url"],)).fetchone()
        if old is not None and not new.get("success"):
            return _row_to_record(old, conn)
        title = new.get("title", "") or (old["title"] if old is not None else "")
        conn.execute(
            "INSERT INTO records (url, title, thumbnail, favicon, success, details) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(url) DO UPDATE SET title = excluded.title, "
            "thumbnail = excluded.thumbnail, favicon = excluded.favicon, "
            "success = excluded.success, details = excluded.details",
            (
                new["url"],
                title,
                new.get("thumbnail", ""),
                new.get("favicon", ""),
                1 if new.get("success") else 0,
                json.dumps(new.get("details") or [], ensure_ascii=False),
            ),
        )
        # 自动填充 domains 表
        domain = _root_domain(new["url"])
        if domain:
            conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (domain,))
        row = conn.execute("SELECT * FROM records WHERE url = ?", (new["url"],)).fetchone()
        return _row_to_record(row, conn)


def set_title(url: str, title: str) -> dict | None:
    with _db() as conn:
        cur = conn.execute("UPDATE records SET title = ? WHERE url = ?", (title.strip(), url))
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM records WHERE url = ?", (url,)).fetchone()
        return _row_to_record(row, conn)


def delete_record(url: str) -> bool:
    with _db() as conn:
        cur = conn.execute("DELETE FROM records WHERE url = ?", (url,))
        return cur.rowcount > 0


# ──────────────────────── Domains ────────────────────────

def list_domains() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM domains ORDER BY name").fetchall()
        return [{"id": r["id"], "name": r["name"], "display_name": r["display_name"],
                 "proxy": r["proxy"]} for r in rows]


def get_domain(name: str) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM domains WHERE name = ?", (name,)).fetchone()
        if not row:
            return None
        return {"id": row["id"], "name": row["name"], "display_name": row["display_name"],
                "proxy": row["proxy"]}


def update_domain(name: str, display_name: str = None, proxy: str = None) -> dict | None:
    with _db() as conn:
        # 确保域名存在
        conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (name,))
        if display_name is not None:
            conn.execute("UPDATE domains SET display_name = ? WHERE name = ?", (display_name, name))
        if proxy is not None:
            conn.execute("UPDATE domains SET proxy = ? WHERE name = ?", (proxy, name))
        row = conn.execute("SELECT * FROM domains WHERE name = ?", (name,)).fetchone()
        return {"id": row["id"], "name": row["name"], "display_name": row["display_name"],
                "proxy": row["proxy"]}


def get_domain_proxy(url: str) -> str:
    """返回该域名的代理地址，空字符串表示用全局代理。"""
    domain = _root_domain(url)
    if not domain:
        return ""
    with _db() as conn:
        row = conn.execute("SELECT proxy FROM domains WHERE name = ?", (domain,)).fetchone()
        return row["proxy"] if row else ""


# ──────────────────────── Tags ────────────────────────

def list_tags() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM tags ORDER BY name").fetchall()
        return [{"id": r["id"], "name": r["name"]} for r in rows]


def create_tag(name: str) -> dict:
    with _db() as conn:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name.strip(),))
        row = conn.execute("SELECT * FROM tags WHERE name = ?", (name.strip(),)).fetchone()
        return {"id": row["id"], "name": row["name"]}


def delete_tag(tag_id: int) -> bool:
    with _db() as conn:
        cur = conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        return cur.rowcount > 0


def add_tag_to_record(url: str, tag_id: int) -> bool:
    with _db() as conn:
        try:
            conn.execute("INSERT OR IGNORE INTO record_tags (record_url, tag_id) VALUES (?, ?)", (url, tag_id))
            return True
        except sqlite3.IntegrityError:
            return False


def remove_tag_from_record(url: str, tag_id: int) -> bool:
    with _db() as conn:
        cur = conn.execute("DELETE FROM record_tags WHERE record_url = ? AND tag_id = ?", (url, tag_id))
        return cur.rowcount > 0


def get_record_tags(url: str) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT t.id, t.name FROM tags t "
            "JOIN record_tags rt ON rt.tag_id = t.id "
            "WHERE rt.record_url = ? ORDER BY t.name",
            (url,),
        ).fetchall()
        return [{"id": r["id"], "name": r["name"]} for r in rows]
