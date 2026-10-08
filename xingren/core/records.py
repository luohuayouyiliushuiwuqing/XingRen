"""书签记录存储：SQLite（metadata.db）。

线程安全：每次操作独立短连接，读写走 WAL 模式。
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# 数据文件始终放在仓库根：parents[0]=core, [1]=xingren, [2]=仓库根。
# editable 安装下 __file__ 指向源码树，结果即仓库根；
# 非 editable（wheel）安装下会落到 site-packages（静默写错地方），
# 故留 XINGREN_DATA_DIR 环境变量兜底。
_DATA_DIR = Path(os.environ.get("XINGREN_DATA_DIR") or Path(__file__).resolve().parents[2])
DB_PATH = _DATA_DIR / "metadata.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    url        TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    thumbnail  TEXT NOT NULL DEFAULT '',
    favicon    TEXT NOT NULL DEFAULT '',
    success    INTEGER NOT NULL DEFAULT 0,
    details    TEXT NOT NULL DEFAULT '[]',
    group_name TEXT NOT NULL DEFAULT ''
)
"""


def _row_to_record(row: sqlite3.Row) -> dict:
    return {
        "url": row["url"],
        "title": row["title"],
        "thumbnail": row["thumbnail"],
        "favicon": row["favicon"],
        "success": bool(row["success"]),
        "details": json.loads(row["details"] or "[]"),
        "group_name": row["group_name"],
    }


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)
    # 已有数据库迁移：group_name 列（v0.2 新增）
    try:
        conn.execute("SELECT group_name FROM records LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE records ADD COLUMN group_name TEXT NOT NULL DEFAULT ''")
    # WAL 让多线程（ThreadingHTTPServer）并发读写安全；幂等，重复执行无副作用
    conn.execute("PRAGMA journal_mode=WAL")


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
    """全部记录，按插入顺序。"""
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
        group_name = new.get("group_name", "") or (old["group_name"] if old is not None else "")
        conn.execute(
            "INSERT INTO records (url, title, thumbnail, favicon, success, details, group_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(url) DO UPDATE SET title = excluded.title, "
            "thumbnail = excluded.thumbnail, favicon = excluded.favicon, "
            "success = excluded.success, details = excluded.details, "
            "group_name = CASE WHEN excluded.group_name = '' THEN records.group_name ELSE excluded.group_name END",
            (
                new["url"],
                title,
                new.get("thumbnail", ""),
                new.get("favicon", ""),
                1 if new.get("success") else 0,
                json.dumps(new.get("details") or [], ensure_ascii=False),
                group_name,
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


def set_group_name(url: str, group_name: str) -> dict | None:
    with _db() as conn:
        cur = conn.execute("UPDATE records SET group_name = ? WHERE url = ?", (group_name.strip(), url))
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM records WHERE url = ?", (url,)).fetchone()
    return _row_to_record(row)


def delete_record(url: str) -> bool:
    with _db() as conn:
        cur = conn.execute("DELETE FROM records WHERE url = ?", (url,))
    return cur.rowcount > 0
