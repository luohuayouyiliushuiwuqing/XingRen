"""书签记录存储：SQLite（metadata.db）。

线程安全：每次操作独立短连接，读写走 WAL 模式。
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from fnmatch import fnmatchcase
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
    need_proxy   INTEGER,  -- NULL=无规则(跟随全局), 1=用代理, 0=强制直连
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

_SCHEMA_PROXY_RULES = """
CREATE TABLE IF NOT EXISTS proxy_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern    TEXT NOT NULL UNIQUE,        -- "*.google.com" / "github.com/*"
    need_proxy INTEGER NOT NULL DEFAULT 1,  -- 1=用全局代理, 0=强制直连
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

# 代理只表达「要不要走」，具体地址仅存于全局配置一处。
# 旧表存的是代理地址（proxy TEXT）：
#   proxy_rules 旧语义 proxy='' = 强制直连 → need_proxy=0
#   domains     旧语义 proxy='' = 无规则跟随全局 → need_proxy=NULL（不能迁成 0，否则全站被强制直连）
def _migrate_to_bool_proxy(conn: sqlite3.Connection) -> None:
    for table, empty_value in (("proxy_rules", "0"), ("domains", "NULL")):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if "proxy" not in cols:
            continue
        if "need_proxy" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN need_proxy INTEGER")
        conn.execute(
            f"UPDATE {table} SET need_proxy = CASE WHEN proxy != '' THEN 1 ELSE {empty_value} END"
        )
        conn.execute(f"ALTER TABLE {table} DROP COLUMN proxy")


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
    conn.execute(_SCHEMA_PROXY_RULES)
    conn.execute("PRAGMA journal_mode=WAL")
    _migrate_to_bool_proxy(conn)

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

_UNSET = object()  # 与「值为 None」区分开：None = 设为无规则，_UNSET = 本次不改


def _domain_row(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "name": row["name"],
            "display_name": row["display_name"],
            # None = 无规则（跟随全局），True = 用代理，False = 强制直连
            "need_proxy": None if row["need_proxy"] is None else bool(row["need_proxy"])}


def list_domains() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM domains ORDER BY name").fetchall()
        return [_domain_row(r) for r in rows]


def get_domain(name: str) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM domains WHERE name = ?", (name,)).fetchone()
        return _domain_row(row) if row else None


def update_domain(name: str, display_name=_UNSET, need_proxy=_UNSET) -> dict:
    with _db() as conn:
        # 确保域名存在
        conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (name,))
        if display_name is not _UNSET:
            conn.execute("UPDATE domains SET display_name = ? WHERE name = ?",
                         (display_name or "", name))
        if need_proxy is not _UNSET:
            # None → NULL（无规则），True/False → 1/0
            value = None if need_proxy is None else (1 if need_proxy else 0)
            conn.execute("UPDATE domains SET need_proxy = ? WHERE name = ?", (value, name))
        row = conn.execute("SELECT * FROM domains WHERE name = ?", (name,)).fetchone()
        return _domain_row(row)


def get_domain_need_proxy(url: str) -> bool | None:
    """返回该域名是否要走代理；无域名规则时返回 None（由全局代理兜底）。"""
    domain = _root_domain(url)
    if not domain:
        return None
    with _db() as conn:
        row = conn.execute("SELECT need_proxy FROM domains WHERE name = ?", (domain,)).fetchone()
        if row is None or row["need_proxy"] is None:
            return None
        return bool(row["need_proxy"])


# ──────────────────────── Proxy rules ────────────────────────

def _rule_row(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "pattern": row["pattern"],
            "need_proxy": bool(row["need_proxy"])}


def list_proxy_rules() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM proxy_rules ORDER BY length(pattern) DESC, pattern").fetchall()
        return [_rule_row(r) for r in rows]


def upsert_proxy_rule(pattern: str, need_proxy: bool) -> dict:
    with _db() as conn:
        conn.execute(
            "INSERT INTO proxy_rules (pattern, need_proxy) VALUES (?, ?) "
            "ON CONFLICT(pattern) DO UPDATE SET need_proxy = excluded.need_proxy",
            (pattern.strip(), 1 if need_proxy else 0),
        )
        row = conn.execute("SELECT * FROM proxy_rules WHERE pattern = ?", (pattern.strip(),)).fetchone()
        return _rule_row(row)


def delete_proxy_rule(rule_id: int) -> bool:
    with _db() as conn:
        cur = conn.execute("DELETE FROM proxy_rules WHERE id = ?", (rule_id,))
        return cur.rowcount > 0


def _pattern_matches(pattern: str, host: str, path: str) -> bool:
    """通配符匹配：含 / 时连路径一起匹配，否则只匹配主机名。"""
    target = f"{host}{path}" if "/" in pattern else host
    return fnmatchcase(target.lower(), pattern.lower())


def match_proxy_rule(url: str) -> tuple[bool, bool]:
    """按 URL 模式匹配代理规则。

    返回 (是否命中, 是否要走代理)。未命中返回 (False, False)，
    交由域名规则 / 全局代理兜底。
    规则按模式长度降序排列，更长（更具体）的先匹配。
    """
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        path = p.path or "/"
    except Exception:
        return False, False
    if not host:
        return False, False
    with _db() as conn:
        rows = conn.execute(
            "SELECT pattern, need_proxy FROM proxy_rules ORDER BY length(pattern) DESC, pattern"
        ).fetchall()
    for row in rows:
        if _pattern_matches(row["pattern"], host, path):
            return True, bool(row["need_proxy"])
    return False, False


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
