"""CLI 入口：提交 URL 抓取网页元数据，结果保存到 metadata.json。

用法：
    python main.py https://example.com https://github.com
"""

import argparse
from pathlib import Path

from metadata_fetcher import get_metadata
from records import STORE_PATH, load_records, merge_record, save_records


def main() -> None:
    parser = argparse.ArgumentParser(description="抓取网页元数据（标题、缩略图、favicon）")
    parser.add_argument("urls", nargs="+", help="要抓取的 URL，可传多个")
    parser.add_argument("-o", "--output", type=Path, default=STORE_PATH, help="结果 JSON 文件路径")
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

    records = load_records(args.output)
    for url in args.urls:
        print(f"抓取 {url} …")
        meta = get_metadata(url, timeout=args.timeout, proxy=proxy)
        records = merge_record(records, meta)
        if meta["success"]:
            print(f"  标题: {meta['title'] or '(无)'}")
            print(f"  缩略图: {meta['thumbnail'] or '(无)'}")
            print(f"  favicon: {meta['favicon'] or '(无)'}")
        else:
            print("  三级抓取全部失败，已仅保存 URL，请稍后手动补充标题")

    save_records(args.output, records)
    print(f"\n已保存 {len(records)} 条记录到 {args.output}")


if __name__ == "__main__":
    main()
