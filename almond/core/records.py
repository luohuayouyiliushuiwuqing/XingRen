"""书签记录存储：SQLite（metadata.db）。

线程安全：每次操作独立短连接，读写走 WAL 模式。
"""

import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from fnmatch import fnmatchcase
from pathlib import Path
from urllib.parse import urlparse

# 仓库根：parents[0]=core, [1]=almond, [2]=仓库根（ALMOND_DATA_DIR 可兜底覆盖）。
# config.json 固定放这里——它只记录「本地数据放在哪个存储目录」，必须跟着代码走。
# 数据库、图片缓存等本地私有数据统一放在「存储目录」：默认仓库根，UI 里可切换（不迁移、旧数据留原地）。
_BASE_DIR = Path(os.environ.get("ALMOND_DATA_DIR") or Path(__file__).resolve().parents[2])
CONFIG_PATH = _BASE_DIR / "config.json"


def _load_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)  # 目录被删后重建，写配置不崩
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


_cfg = _load_config()
DATA_DIR = Path(_cfg["storage_dir"]).expanduser() if _cfg.get("storage_dir") else _BASE_DIR
DB_PATH = DATA_DIR / "metadata.db"

# 存储纪元：每次真实切换目录 +1。切换前发起的抓取，完成后比对纪元不一致 → 结果作废
# （防止旧库的 URL 被写进新库、旧经验写进新缓存）。同一目录重复保存不递增。
_STORAGE_EPOCH = 0
# 切库互斥锁：set_storage_dir 的目录/纪元改写 与 upsert_record_if_epoch 的
# 「比对纪元 + 写入」必须原子——否则存在「比对通过 → 切库 → 写进新库」的窗口
_SWITCH_LOCK = threading.RLock()
# 切库后的回调（fetch_hints 在 import 时注册自己的内存缓存重置）
_STORAGE_LISTENERS: list = []


def storage_epoch() -> int:
    return _STORAGE_EPOCH


def on_storage_changed(callback) -> None:
    """注册切库回调；切换成功后按注册顺序执行（异常单个吞掉，不阻断切换）。"""
    _STORAGE_LISTENERS.append(callback)

# domains 表的老库回填只按库跑一次（见 _ensure 里的用法）。
# 记**路径集合**：切到新库会自动跑一次，但切回曾经打开过的库不再重复 O(N) 回填
# ——在两个目录之间来回切换时，每次白付一遍全表扫正是「切换慢」的一份来源。
_DOMAINS_BACKFILLED_FOR: set[str] = set()

_SCHEMA_RECORDS = """
CREATE TABLE IF NOT EXISTS records (
    url        TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    thumbnail  TEXT NOT NULL DEFAULT '',
    favicon    TEXT NOT NULL DEFAULT '',
    success    INTEGER NOT NULL DEFAULT 0,
    details    TEXT NOT NULL DEFAULT '[]',
    fetched    INTEGER NOT NULL DEFAULT 1
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


# 多段公共后缀：co.uk / com.cn / com.au … —— 这类要连前一段一起取，
# 否则 bar.co.uk 会被截成 co.uk，导致分组错乱、域名代理规则对不上。
# 必须与前端 rootDomain() 保持同一份列表，否则两边分组口径不一致。
_MULTI_TLDS = (
    ".co.uk", ".org.uk", ".ac.uk", ".gov.uk", ".me.uk",
    ".com.cn", ".net.cn", ".org.cn", ".gov.cn", ".edu.cn",
    ".co.jp", ".ne.jp", ".or.jp", ".ac.jp",
    ".com.au", ".net.au", ".org.au",
    ".co.nz", ".com.hk", ".com.tw", ".com.sg",
    ".com.br", ".com.mx", ".co.kr", ".co.in", ".com.ar",
)


def _root_domain(url: str) -> str:
    """从 URL 提取可注册域名（处理多段公共后缀）。"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    for tld in _MULTI_TLDS:
        if host.endswith(tld):
            head = host[: -len(tld)]
            return f"{head.rsplit('.', 1)[-1]}{tld}" if head else host
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _tags_for(conn: sqlite3.Connection, url: str) -> list[dict]:
    """单条记录的标签（各调用方自己查，避免 list_records 出现 N+1）。"""
    rows = conn.execute(
        "SELECT t.id, t.name FROM tags t "
        "JOIN record_tags rt ON rt.tag_id = t.id "
        "WHERE rt.record_url = ? ORDER BY t.name",
        (url,),
    ).fetchall()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


def _row_to_record(row: sqlite3.Row, tags: list[dict]) -> dict:
    return {
        "url": row["url"],
        "title": row["title"],
        "thumbnail": row["thumbnail"],
        "favicon": row["favicon"],
        "success": bool(row["success"]),
        "details": json.loads(row["details"] or "[]"),
        "domain": _root_domain(row["url"]),
        "tags": tags,
        "fetched": bool(row["fetched"]),
    }


def _all_tags_map(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """一次 JOIN 取全部记录的标签，返回 {record_url: [tag]} —— 消除 N+1。"""
    result: dict[str, list[dict]] = {}
    rows = conn.execute(
        "SELECT rt.record_url, t.id, t.name FROM record_tags rt "
        "JOIN tags t ON t.id = rt.tag_id ORDER BY t.name"
    ).fetchall()
    for r in rows:
        result.setdefault(r["record_url"], []).append({"id": r["id"], "name": r["name"]})
    return result


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA_RECORDS)
    conn.execute(_SCHEMA_DOMAINS)
    conn.execute(_SCHEMA_TAGS)
    conn.execute(_SCHEMA_RECORD_TAGS)
    conn.execute(_SCHEMA_PROXY_RULES)
    conn.execute("PRAGMA journal_mode=WAL")
    _migrate_to_bool_proxy(conn)

    # 迁移：fetched 列（0=快照导入未抓取，1=已抓取/尝试过）。
    # ADD COLUMN 带 NOT NULL DEFAULT 合法，现有行自动得 1（都尝试过抓取），无需回填。
    try:
        conn.execute("SELECT fetched FROM records LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE records ADD COLUMN fetched INTEGER NOT NULL DEFAULT 1")

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

    # 迁移：域名级配置（每域名的显示偏好 + 抓取规则），都挂在 domains 表上。
    # auto_fetch / cover 用**可空**整型——NULL 就是「跟随全局 / 默认」，
    # 与 need_proxy 的三态口径一致（NOT NULL 会存不进 NULL）。
    cols = [r[1] for r in conn.execute("PRAGMA table_info(domains)")]
    for col, ddl in (
        ("prefs", "TEXT NOT NULL DEFAULT '{}'"),          # JSON：{sort, fields[], tag}
        ("auto_fetch", "INTEGER"),                        # NULL=跟随全局, 1=开, 0=关（补抓不碰）
        ("detail_selector", "TEXT NOT NULL DEFAULT ''"),  # '' = 用默认 .space-y-2 > *
        ("cover", "INTEGER"),                             # NULL=默认(og), 0=不要封面, 1=只要图标
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE domains ADD COLUMN {col} {ddl}")

    # 自动填充 domains 表（老库升级用，**按库跑一次**）
    # 这段必须门控：_ensure 每开一条连接都会走一遍，全表扫 + 逐行 urlparse +
    # 写语句就是 O(N)×每条连接——一次 5000 条的自动补抓会放大成 O(N²)，
    # 且让每次连接都去抢写锁，跟真正的 upsert 抢 timeout=10。
    # 之后新增记录的写入路径（upsert_record / insert_quick_records / update_domain）
    # 都会同步补自己那条域名，这里的全表回填只服务「domains 表刚建起来的老库」。
    global _DOMAINS_BACKFILLED_FOR
    if str(DB_PATH) not in _DOMAINS_BACKFILLED_FOR:
        for row in conn.execute("SELECT DISTINCT url FROM records").fetchall():
            domain = _root_domain(row["url"])
            if domain:
                conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (domain,))
        # 多线程同时首开也安全：INSERT OR IGNORE 幂等，最多重复跑一次
        _DOMAINS_BACKFILLED_FOR.add(str(DB_PATH))


@contextmanager
def _db():
    if not DB_PATH.parent.is_dir():
        # 目录不存在就创建（文件夹被删 / 外置盘重新挂载后自动重建，直接可用）
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        _ensure(conn)
        with conn:
            yield conn
    finally:
        conn.close()


# ──────────────────────── 存储目录（本地私有数据统一存放处） ────────────────────────

# 历史上限：够回看几处常用位置即可，多了反而难找（新用过的顶到最前）
_STORAGE_HISTORY_MAX = 10


def _push_storage_history(cfg: dict, path: str) -> None:
    """把目录记到历史头部（去重、截断）。cfg 由调用方负责落盘。"""
    hist = cfg.get("storage_history") or []
    kept = [p for p in hist if isinstance(p, str) and p != path]
    cfg["storage_history"] = ([path] + kept)[:_STORAGE_HISTORY_MAX]


def _storage_history(current: str) -> list[str]:
    """历史列表：当前目录永远置顶（老配置没有历史时也能看到现在在哪）。"""
    hist = [p for p in (_load_config().get("storage_history") or []) if isinstance(p, str)]
    return [current] + [p for p in hist if p != current]


def get_storage_paths() -> dict:
    """当前存储目录及其中的数据库、图片缓存位置，外加用过的历史目录。

    `epoch` 随处返回：前端开抓前要把它带上（/api/fetch 的请求级纪元校验），
    不能只在切库响应里出现——页面加载时也得知道当前是第几纪元。
    """
    return {
        "path": str(DATA_DIR),
        "db_path": str(DB_PATH),
        "cache_dir": str(DATA_DIR / "cache" / "img"),
        "exists": DATA_DIR.exists(),
        "history": _storage_history(str(DATA_DIR)),
        "epoch": _STORAGE_EPOCH,
    }


def remove_storage_history(path: str) -> list[str]:
    """从历史里移除一条（当前目录移不掉——置顶展示由 _storage_history 保证）。"""
    path = (path or "").strip()
    cfg = _load_config()
    hist = cfg.get("storage_history") or []
    cfg["storage_history"] = [p for p in hist if isinstance(p, str) and p != path]
    _save_config(cfg)
    return _storage_history(str(DATA_DIR))


def get_cache_dir() -> Path:
    """图片缓存目录（跟随存储目录，切换后自动指向新位置）。"""
    return DATA_DIR / "cache" / "img"


def set_storage_dir(new_dir: str) -> dict:
    """切换存储目录：**不迁移，新目录从零开始**（用户定的语义）。

    - 旧数据库与缓存（图片、经验缓存）**原样留在原目录**——想用回旧数据随时切回去；
    - 目标已有 `metadata.db` 则直接使用它（只校验合法，内容不动）；没有则由下次连接
      自动创建**空库**；目录不存在会创建；相对路径按仓库根解析，传入文件则拒绝；
    - 同路径重复保存是 no-op（不递增纪元）；
    - 切换成功：存储纪元 +1（**切换前发起的抓取结果从此作废**）、依次执行
      `on_storage_changed` 注册的回调（fetch_hints 借此丢弃内存里的旧经验）、
      历史位置记录新旧两处；结果写 config.json，下次启动沿用，调用后立即生效。
    """
    global DATA_DIR, DB_PATH, _STORAGE_EPOCH
    raw = (new_dir or "").strip()
    if not raw:
        raise ValueError("目录不能为空")
    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = _BASE_DIR / target
    if target.is_file():
        raise ValueError(f"请指定目录而不是文件：{target}")
    try:
        target = target.resolve()
    except OSError as exc:
        raise ValueError(f"路径无效：{exc}") from exc

    old_data_dir = DATA_DIR
    try:
        same = target == old_data_dir.resolve()
    except OSError:
        same = target == old_data_dir
    # 同路径但目录已不存在：不视为 no-op，往下走 mkdir 重建（修复场景）
    if same and target.is_dir():
        cfg = _load_config()
        _push_storage_history(cfg, str(target))   # 升级后的老配置首条历史从这里补上
        _save_config(cfg)
        return {**get_storage_paths(), "db_used_existing": DB_PATH.exists()}

    target.mkdir(parents=True, exist_ok=True)
    dst_db = target / "metadata.db"
    db_used_existing = dst_db.exists()
    if db_used_existing:
        # 目标已有数据库：直接使用——只校验是合法 SQLite，内容一概不动
        try:
            probe = sqlite3.connect(dst_db)
            probe.execute("SELECT count(*) FROM sqlite_master")
            probe.close()
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"目标目录的 metadata.db 不是有效的 SQLite 数据库：{dst_db}") from exc

    # 目录/纪元的改写与 upsert_record_if_epoch 的「比对+写入」共用 _SWITCH_LOCK：
    # 保证不会出现「写方比对纪元通过 → 这里切库 → 写方落进新库」的窗口
    with _SWITCH_LOCK:
        DATA_DIR = target
        DB_PATH = dst_db
        _STORAGE_EPOCH += 1                  # 从此刻起，旧纪元的抓取结果作废
    # 监听器放在锁**外**跑：fetchpool 的硬杀子进程要 join 几个进程，
    # 挂在锁里会把并发的写入/效应落地全都堵住（纪元已 +1，语义上已无需持锁）
    for _cb in list(_STORAGE_LISTENERS):
        try:
            _cb()
        except Exception:  # noqa: BLE001 —— 回调失败不阻断切换
            pass
    cfg = _load_config()
    if target == _BASE_DIR:
        cfg.pop("storage_dir", None)  # 切回默认目录，配置无需冗余记录
    else:
        cfg["storage_dir"] = str(target)
    cfg.pop("db_path", None)  # 清掉旧版「数据库单独指定位置」的配置键
    # 旧位置也留痕：从默认目录第一次切走时，老位置才不会从历史里消失
    _push_storage_history(cfg, str(old_data_dir))
    _push_storage_history(cfg, str(target))
    _save_config(cfg)
    return {**get_storage_paths(), "db_used_existing": db_used_existing, "epoch": _STORAGE_EPOCH}


# ──────────────────────── Records ────────────────────────

def count_records() -> int:
    """记录总数。切库响应只需要个数——list_records() 会拉全量行再 JOIN 标签，纯浪费。"""
    with _db() as conn:
        return conn.execute("SELECT count(*) FROM records").fetchone()[0]


def list_records() -> list[dict]:
    """全部记录，按插入顺序。标签用一次 JOIN 取，避免 N+1。"""
    with _db() as conn:
        rows = conn.execute("SELECT * FROM records ORDER BY rowid").fetchall()
        tag_map = _all_tags_map(conn)
        return [_row_to_record(row, tag_map.get(row["url"], [])) for row in rows]


def list_pending_urls(limit: int = 5000) -> list[str]:
    """从未抓取过的记录（fetched=0），按插入顺序，只回 URL。

    自动补抓的轮询数据源：页面加载时只扫一次 state.records，而别的标签页、
    浏览器插件、导入接口随后写进库的链接不会自己冒出来，靠这个小接口发现。
    只回 url 不回整条记录——1378 条全量 /api/records 每 10 秒拉一次太重。
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT url FROM records WHERE fetched = 0 ORDER BY rowid LIMIT ?",
            (limit,),
        ).fetchall()
        return [r["url"] for r in rows]


def get_record(url: str) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM records WHERE url = ?", (url,)).fetchone()
        return _row_to_record(row, _tags_for(conn, url)) if row else None


def upsert_record(new: dict) -> dict:
    """按 URL 合并写入；抓取失败或标题为空时保留已有数据。置 fetched=1（真实抓取过）。"""
    with _db() as conn:
        old = conn.execute("SELECT * FROM records WHERE url = ?", (new["url"],)).fetchone()
        if old is not None and not new.get("success"):
            # 抓取失败：内容一个字都不覆盖（合并规则），但要把 fetched 置 1——
            # 这一列的语义是「已抓取/**尝试过**」。不记的话，快照导入的链接
            # 提取失败后 fetched 仍是 0，状态点永远灰着，看着像还在排队。
            conn.execute("UPDATE records SET fetched = 1 WHERE url = ?", (new["url"],))
            row = conn.execute("SELECT * FROM records WHERE url = ?", (new["url"],)).fetchone()
            return _row_to_record(row, _tags_for(conn, new["url"]))
        title = new.get("title", "") or (old["title"] if old is not None else "")
        conn.execute(
            "INSERT INTO records (url, title, thumbnail, favicon, success, details, fetched) "
            "VALUES (?, ?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(url) DO UPDATE SET title = excluded.title, "
            "thumbnail = excluded.thumbnail, favicon = excluded.favicon, "
            "success = excluded.success, details = excluded.details, "
            "fetched = 1",
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
        return _row_to_record(row, _tags_for(conn, new["url"]))


def upsert_record_if_epoch(new: dict, epoch: int) -> dict | None:
    """带存储纪元的合并写入：纪元一致才写，不一致返回 None（本次结果作废）。

    比对与写入在 `_SWITCH_LOCK` 下一体执行、与 `set_storage_dir` 互斥——
    杜绝「调用方比对纪元通过 → 切库 → upsert 落进**新库**」的 TOCTOU 窗口。
    `/api/fetch` 的抓取结果必须走这个入口（直接调 `upsert_record` 的脚本
    路径没有这层保护，但它们也不经过切库流程）。
    """
    with _SWITCH_LOCK:
        if epoch != _STORAGE_EPOCH:
            return None
        return upsert_record(new)


def insert_quick_records(items: list[dict]) -> dict:
    """快照批量入库：**不联网**，直接写入已有元数据，`fetched=0`。

    items: [{url, title?, thumbnail?, favicon?, tags?: [str]}]
    单事务处理，已存在的 URL 跳过。返回 {inserted, skipped}。
    """
    inserted = skipped = 0
    with _db() as conn:
        for item in items:
            url = (item.get("url") or "").strip()
            if not url:
                continue
            if conn.execute("SELECT 1 FROM records WHERE url = ?", (url,)).fetchone():
                skipped += 1
                continue
            # success=1：HTML 已提供标题/封面，元数据有效（details 留待按需补抓）
            conn.execute(
                "INSERT INTO records (url, title, thumbnail, favicon, success, details, fetched) "
                "VALUES (?, ?, ?, ?, 1, '[]', 0)",
                (
                    url,
                    (item.get("title") or "").strip(),
                    (item.get("thumbnail") or "").strip(),
                    (item.get("favicon") or "").strip(),
                ),
            )
            domain = _root_domain(url)
            if domain:
                conn.execute("INSERT OR IGNORE INTO domains (name) VALUES (?)", (domain,))
            for name in item.get("tags") or []:
                name = (name or "").strip()
                if not name:
                    continue
                conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
                tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()[0]
                conn.execute(
                    "INSERT OR IGNORE INTO record_tags (record_url, tag_id) VALUES (?, ?)",
                    (url, tag_id),
                )
            inserted += 1
    return {"inserted": inserted, "skipped": skipped}


def set_title(url: str, title: str) -> dict | None:
    with _db() as conn:
        cur = conn.execute("UPDATE records SET title = ? WHERE url = ?", (title.strip(), url))
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM records WHERE url = ?", (url,)).fetchone()
        return _row_to_record(row, _tags_for(conn, url))


def delete_record(url: str) -> bool:
    with _db() as conn:
        cur = conn.execute("DELETE FROM records WHERE url = ?", (url,))
        return cur.rowcount > 0


# ──────────────────────── Domains ────────────────────────

_UNSET = object()  # 与「值为 None」区分开：None = 设为无规则，_UNSET = 本次不改


def _domain_row(row: sqlite3.Row) -> dict:
    # prefs 落库是 JSON 字符串，出库还原成对象（坏数据一律当没有偏好）
    try:
        prefs = json.loads(row["prefs"] or "{}")
    except (json.JSONDecodeError, KeyError, IndexError):
        prefs = {}
    if not isinstance(prefs, dict):
        prefs = {}
    return {"id": row["id"], "name": row["name"],
            "display_name": row["display_name"],
            # None = 无规则（跟随全局），True = 用代理，False = 强制直连
            "need_proxy": None if row["need_proxy"] is None else bool(row["need_proxy"]),
            # 每域名显示偏好（{sort, fields[], tag}）与抓取规则
            "prefs": prefs,
            "auto_fetch": None if row["auto_fetch"] is None else bool(row["auto_fetch"]),
            "detail_selector": row["detail_selector"] or "",
            "cover": None if row["cover"] is None else bool(row["cover"])}


def list_domains() -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM domains ORDER BY name").fetchall()
        return [_domain_row(r) for r in rows]


def get_domain(name: str) -> dict | None:
    with _db() as conn:
        row = conn.execute("SELECT * FROM domains WHERE name = ?", (name,)).fetchone()
        return _domain_row(row) if row else None


def update_domain(name: str, display_name=_UNSET, need_proxy=_UNSET,
                  prefs=_UNSET, auto_fetch=_UNSET, detail_selector=_UNSET,
                  cover=_UNSET) -> dict:
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
        if prefs is not _UNSET:
            if prefs is None:
                prefs = {}
            if not isinstance(prefs, dict):
                raise ValueError("prefs 必须是对象")
            conn.execute("UPDATE domains SET prefs = ? WHERE name = ?",
                         (json.dumps(prefs, ensure_ascii=False), name))
        if auto_fetch is not _UNSET:
            value = None if auto_fetch is None else (1 if auto_fetch else 0)
            conn.execute("UPDATE domains SET auto_fetch = ? WHERE name = ?", (value, name))
        if detail_selector is not _UNSET:
            conn.execute("UPDATE domains SET detail_selector = ? WHERE name = ?",
                         (detail_selector or "", name))
        if cover is not _UNSET:
            value = None if cover is None else (1 if cover else 0)
            conn.execute("UPDATE domains SET cover = ? WHERE name = ?", (value, name))
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


def get_domain_fetch_config(url: str) -> dict:
    """该域名的抓取规则（`/api/fetch` 用）：详情选择器 + 封面开关。
    **故意不返回代理结论**——代理优先级由 server.effective_proxy 统一算
    （URL 模式规则 > 域名规则 > 全局），在这里拿 need_proxy 去覆盖它会打乱优先级。"""
    out = {"detail_selector": "", "cover": None}
    domain = _root_domain(url)
    if not domain:
        return out
    with _db() as conn:
        row = conn.execute(
            "SELECT detail_selector, cover FROM domains WHERE name = ?", (domain,)
        ).fetchone()
    if row is None:
        return out
    return {"detail_selector": row["detail_selector"] or "",
            "cover": None if row["cover"] is None else bool(row["cover"])}


# ──────────────────────── 域名重置 ────────────────────────

# 至少两段（允许 .com.cn 这类整体作 TLD 判断不在此处，格式校验只看形状）
_DOMAIN_SHAPE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$",
    re.I,
)


def _norm_domain(raw: str) -> str:
    """归一化用户输入：小写、剥掉 scheme/路径（粘贴完整 URL 也能用）。"""
    d = (raw or "").strip().lower()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].strip()


def _swap_url_domain(url: str, old: str, new: str) -> str:
    """改写 URL 的主机名：old 及其子域名（cdn.old → cdn.new）换掉，其余原样。"""
    try:
        p = urlparse(url)
        host = p.hostname or ""
    except Exception:
        return url
    low = host.lower()
    if low == old:
        new_host = new
    elif low.endswith("." + old):
        new_host = host[: -len(old)] + new   # 保留子域名前缀及其原始大小写
    else:
        return url
    netloc = new_host
    try:
        if p.port:
            netloc += f":{p.port}"
    except ValueError:
        pass  # 非法端口：原样拼回，不因它中断整次重置
    if "@" in (p.netloc or ""):
        netloc = p.netloc.rsplit("@", 1)[0] + "@" + netloc
    return p._replace(netloc=netloc).geturl()


def _swap_text_domain(text: str, old: str, new: str) -> str:
    """文本（缩略图/favicon/详情 JSON/规则模式）中的域名按边界整体替换。

    边界：前面不是域名标签字符（否则 myexample.live 不会误中），
    后面不能延伸出更长的域名（example.live.backup.com 不是本域，不替换）。
    """
    if not text or old not in text.lower():
        return text
    pattern = re.compile(
        r"(?<![a-z0-9-])" + re.escape(old) + r"(?![a-z0-9.-]*[a-z0-9])",
        re.I,
    )
    return pattern.sub(new, text)


def replace_domain(old: str, new: str) -> dict:
    """域名重置：原域名失效时，把它（含子域名）在库里的所有引用换成新域名。

    覆盖：records 的 url / thumbnail / favicon / details、record_tags 关联、
    domains 表（别名与域名代理规则）、proxy_rules 中的匹配模式。
    新旧 URL 撞车（两个域名版本都导入过）时合并到新记录：标签迁过去，删旧行。
    单事务，整体出错回滚；单条记录出错只回滚那一条、计为 failed，其余照改。
    返回 {total, records, merged, failed, error, rules}，恒有
    total == records + merged + failed：
      total   = 命中（主机名是 old 或其子域名）的记录数，即本次重置对象；
      records = 改写成功；
      merged  = 重复（新域名版本已存在，并进那一条，旧行删除）；
      failed  = 命中却没改成的（单条出错被跳过），error 给出最近一条原因。
    """
    old = _norm_domain(old)
    new = _norm_domain(new)
    if not old or not new:
        raise ValueError("域名不能为空")
    if not _DOMAIN_SHAPE.match(old):
        raise ValueError(f"原域名格式无效：{old}")
    if not _DOMAIN_SHAPE.match(new):
        raise ValueError(f"新域名格式无效：{new}")
    if old == new:
        raise ValueError("新域名与原域名相同")

    with _db() as conn:
        # 显式开外层事务：行级 SAVEPOINT 必须挂在它下面——否则最外层 savepoint 一 RELEASE
        # 就把已改的行提交了，「整体出错回滚」会退化成逐条落库；挂上去之后单条 RELEASE
        # 只释放该条，整体仍由 with conn: 一次性收口（出错回滚、正常提交）
        if not conn.in_transaction:
            conn.execute("BEGIN")
        total = updated = merged = rules_updated = 0
        last_error = None

        for row in conn.execute(
            "SELECT url, thumbnail, favicon, details FROM records"
        ).fetchall():
            new_url = _swap_url_domain(row["url"], old, new)
            if new_url == row["url"]:
                # 链接没命中（只有封面/详情里出现过原域名）：照改字段，但不计入重置对象
                thumb = _swap_text_domain(row["thumbnail"], old, new)
                favicon = _swap_text_domain(row["favicon"], old, new)
                details = _swap_text_domain(row["details"], old, new)
                if (thumb, favicon, details) != (row["thumbnail"], row["favicon"], row["details"]):
                    conn.execute(
                        "UPDATE records SET thumbnail = ?, favicon = ?, details = ? WHERE url = ?",
                        (thumb, favicon, details, new_url),
                    )
                continue

            total += 1
            conn.execute("SAVEPOINT xr_row")
            try:
                if conn.execute(
                    "SELECT 1 FROM records WHERE url = ?", (new_url,)
                ).fetchone():
                    # 新域名版本已存在：标签并过去，删掉旧记录（它的封面等随行作废）
                    conn.execute(
                        "UPDATE OR IGNORE record_tags SET record_url = ? WHERE record_url = ?",
                        (new_url, row["url"]),
                    )
                    conn.execute("DELETE FROM record_tags WHERE record_url = ?", (row["url"],))
                    conn.execute("DELETE FROM records WHERE url = ?", (row["url"],))
                    merged += 1
                else:
                    conn.execute(
                        "UPDATE records SET url = ? WHERE url = ?", (new_url, row["url"])
                    )
                    # OR IGNORE：新旧记录共有标签时主键不冲突，独有标签照常迁走
                    conn.execute(
                        "UPDATE OR IGNORE record_tags SET record_url = ? WHERE record_url = ?",
                        (new_url, row["url"]),
                    )
                    thumb = _swap_text_domain(row["thumbnail"], old, new)
                    favicon = _swap_text_domain(row["favicon"], old, new)
                    details = _swap_text_domain(row["details"], old, new)
                    if (thumb, favicon, details) != (
                        row["thumbnail"], row["favicon"], row["details"]
                    ):
                        conn.execute(
                            "UPDATE records SET thumbnail = ?, favicon = ?, details = ? "
                            "WHERE url = ?",
                            (thumb, favicon, details, new_url),
                        )
                    updated += 1
            except sqlite3.Error as exc:
                # 这一条出错只丢这一条：退回行级 savepoint，其余记录照常改
                conn.execute("ROLLBACK TO xr_row")
                conn.execute("RELEASE xr_row")
                last_error = f"{row['url']}：{exc}"
            else:
                conn.execute("RELEASE xr_row")

        # domains 表：改名保住别名/代理规则；new 已有行则把 old 的设置（仅在 new 缺时）并过去
        old_row = conn.execute("SELECT * FROM domains WHERE name = ?", (old,)).fetchone()
        if old_row is not None:
            new_row = conn.execute("SELECT * FROM domains WHERE name = ?", (new,)).fetchone()
            if new_row is None:
                conn.execute("UPDATE domains SET name = ? WHERE name = ?", (new, old))
            else:
                if new_row["need_proxy"] is None and old_row["need_proxy"] is not None:
                    conn.execute(
                        "UPDATE domains SET need_proxy = ? WHERE name = ?",
                        (old_row["need_proxy"], new),
                    )
                if not new_row["display_name"] and old_row["display_name"]:
                    conn.execute(
                        "UPDATE domains SET display_name = ? WHERE name = ?",
                        (old_row["display_name"], new),
                    )
                conn.execute("DELETE FROM domains WHERE name = ?", (old,))

        for row in conn.execute("SELECT id, pattern FROM proxy_rules").fetchall():
            new_pattern = _swap_text_domain(row["pattern"], old, new)
            if new_pattern == row["pattern"]:
                continue
            if conn.execute(
                "SELECT 1 FROM proxy_rules WHERE pattern = ?", (new_pattern,)
            ).fetchone():
                conn.execute("DELETE FROM proxy_rules WHERE id = ?", (row["id"],))
            else:
                conn.execute(
                    "UPDATE proxy_rules SET pattern = ? WHERE id = ?", (new_pattern, row["id"])
                )
            rules_updated += 1

        return {"old": old, "new": new, "total": total,
                "records": updated, "merged": merged,
                "failed": total - updated - merged, "error": last_error,
                "rules": rules_updated}


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


def list_proxy_domains() -> list[str]:
    """最终会走代理的域名（供侧边栏标记）。

    判定优先级与服务端 _effective_proxy 一致：
    URL 模式规则命中 → 规则说了算；否则看域名规则是否为「用代理」。
    候选 = domains 表 ∪ 全部记录按根域名去重。
    单连接取数、内存判定——避免逐域名开库触发 _ensure 全表扫描。
    """
    with _db() as conn:
        names = {row["name"] for row in conn.execute("SELECT name FROM domains")}
        for row in conn.execute("SELECT DISTINCT url FROM records"):
            d = _root_domain(row["url"])
            if d:
                names.add(d)
        # 与 match_proxy_rule 同序：模式更长（更具体）的先命中
        rules = conn.execute(
            "SELECT pattern, need_proxy FROM proxy_rules ORDER BY length(pattern) DESC, pattern"
        ).fetchall()
        domain_need = {
            row["name"]: row["need_proxy"]
            for row in conn.execute("SELECT name, need_proxy FROM domains")
        }

    proxied = []
    for name in sorted(names):
        # 侧边栏是根域名，而 *.example.com 不匹配顶级域名本身：
        # 根域名与 www. 子域名各探测一次，任一命中即代表该域名下的记录
        hit, need = False, False
        for host in (name, f"www.{name}"):
            for rule in rules:
                if _pattern_matches(rule["pattern"], host, "/"):
                    hit, need = True, bool(rule["need_proxy"])
                    break
            if hit:
                break
        if hit:
            if need:
                proxied.append(name)
        elif domain_need.get(name):
            # SQLite 返回整数 1/0/NULL：真值判断即可（1=用代理，0/NULL 不标）
            proxied.append(name)
    return proxied
