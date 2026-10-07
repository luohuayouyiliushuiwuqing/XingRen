"""metadata.json 的读写与合并规则（CLI、Web 看板、Qt 看板共用）。"""

import json
from pathlib import Path

STORE_PATH = Path(__file__).with_name("metadata.json")


def load_records(path: Path) -> list[dict]:
    if path.exists():
        # utf-8-sig 兼容 PowerShell 等工具写入的 UTF-8 BOM
        return json.loads(path.read_text(encoding="utf-8-sig"))
    return []


def save_records(path: Path, records: list[dict]) -> None:
    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def merge_record(records: list[dict], new: dict) -> list[dict]:
    """按 URL 去重更新；抓取失败或标题为空时保留已有数据，避免覆盖手动补充的内容。"""
    for i, old in enumerate(records):
        if old["url"] == new["url"]:
            if not new["success"]:
                return records
            if not new["title"] and old.get("title"):
                new["title"] = old["title"]
            records[i] = new
            return records
    records.append(new)
    return records
