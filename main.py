"""CLI 入口：提交 URL 抓取网页元数据，写入 SQLite（metadata.db）。

用法：
    python main.py https://example.com https://github.com
    python main.py https://example.com --export backup.json   # 另存 JSON 备份
"""

import argparse
from pathlib import Path

from metadata_fetcher import get_metadata
from records import DB_PATH, export_json, upsert_record


def main() -> None:
    parser = argparse.ArgumentParser(description="抓取网页元数据（标题、缩略图、favicon），存入 SQLite")
    parser.add_argument("urls", nargs="+", help="要抓取的 URL，可传多个")
    parser.add_argument("--export", type=Path, help="抓取后把库内全部记录导出到该 JSON 文件")
    parser.add_argument("-t", "--timeout", type=int, default=20, help="单个抓取器超时秒数（默认 20）")
    parser.add_argument(
        "--proxy",
        default="http://127.0.0.1:7892",
        help="代理地址，默认本机 7892（抓取外国网站用），代理不可用时自动回退直连",
    )
    parser.add_argument("--no-proxy", action="store_true", help="禁用代理，直连抓取")
    args = parser.parse_args()

    proxy = None if args.no_proxy else args.proxy
    print(f"代理: {proxy or '直连'}")

    for url in args.urls:
        print(f"抓取 {url} …")
        meta = get_metadata(url, timeout=args.timeout, proxy=proxy)
        merged = upsert_record(meta)
        if merged["success"]:
            print(f"  标题: {merged['title'] or '(无)'}")
            print(f"  缩略图: {merged['thumbnail'] or '(无)'}")
            print(f"  favicon: {merged['favicon'] or '(无)'}")
            if merged["details"]:
                print(f"  详情字段: {len(merged['details'])} 个")
        else:
            print("  三级抓取全部失败，已仅保存 URL，请稍后手动补充标题")

    print(f"\n已写入数据库 {DB_PATH}")
    if args.export:
        count = export_json(args.export)
        print(f"已导出 {count} 条记录到 {args.export}")


if __name__ == "__main__":
    main()
