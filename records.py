"""书签记录存储：SQLite（metadata.db），替代原 metadata.json。

首次使用且数据库为空时，自动从同目录的 metadata.json 导入历史数据。
线程安全：每次操作独立短连接，读写走 WAL 模式。
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).with_name("metadata.db")
JSON_PATH = Path(__file__).with_name("metadata.json")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    url       TEXT PRIMARY KEY,
    title     TEXT NOT NULL DEFAULT '',
    thumbnail TEXT NOT NULL DEFAULT '',
    favicon   TEXT NOT NULL DEFAULT '',
    success   INTEGER NOT NULL DEFAULT 0,
    details   TEXT NOT NULL DEFAULT '[]'
)
"""

_initialized = False


def _row_to_record(row: sqlite3.Row) -> dict:
    return {
        "url": row["url"],
        "title": row["title"],
        "thumbnail": row["thumbnail"],
        "favicon": row["favicon"],
        "success": bool(row["success"]),
        "details": json.loads(row["details"] or "[]"),
    }


def _ensure(conn: sqlite3.Connection) -> None:
    global _initialized
    conn.execute(_SCHEMA)
    if _initialized:
        return
    conn.execute("PRAGMA journal_mode=WAL")
    # 数据库为空且存在历史 JSON 时，一次性导入
    count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    if count == 0 and JSON_PATH.exists():
        data = json.loads(JSON_PATH.read_text(encoding="utf-8-sig"))
        for rec in data:
            if not isinstance(rec, dict) or not rec.get("url"):
                continue
            conn.execute(
                "INSERT OR IGNORE INTO records (url, title, thumbnail, favicon, success, details) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    rec["url"],
                    rec.get("title", ""),
                    rec.get("thumbnail", ""),
                    rec.get("favicon", ""),
                    1 if rec.get("success") else 0,
                    json.dumps(rec.get("details") or [], ensure_ascii=False),
                ),
            )
        conn.commit()
    _initialized = True


@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        _ensure(conn)
        with conn:  # 事务：提交或回滚
            yield conn
    finally:
        conn.close()


def list_records() -> list[dict]:
    """全部记录，按插入顺序（与原 JSON 数组顺序一致）。"""
    with _db() as conn:
        rows = conn.execute("SELECT * FROM records ORDER BY rowid").fetchall()
    return [_row_to_record(row) for row in rows]


def get_record(url: str) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM records WHERE url = ?", (url,)).fetchone()
    return _row_to_record(row) if row else None


def upsert_record(new: dict) -> dict:
    """按 URL 合并写入；抓取失败或标题为空时保留已有数据（与原 merge 规则一致）。"""
    with _db() as conn:
        old = conn.execute("SELECT * FROM records WHERE url = ?", (new["url"],)).fetchone()
        if old is not None and not new.get("success"):
            return _row_to_record(old)
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
        row = conn.execute("SELECT * FROM records WHERE url = ?", (new["url"],)).fetchone()
    return _row_to_record(row)


def set_title(url: str, title: str) -> dict | None:
    with _db() as conn:
        cur = conn.execute("UPDATE records SET title = ? WHERE url = ?", (title.strip(), url))
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM records WHERE url = ?", (url,)).fetchone()
    return _row_to_record(row)


def delete_record(url: str) -> bool:
    with _db() as conn:
        cur = conn.execute("DELETE FROM records WHERE url = ?", (url,))
    return cur.rowcount > 0


def export_json(path: Path) -> int:
    """把库内记录导出为 JSON 文件（备份 / 人工查看用）。"""
    records = list_records()
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(records)
