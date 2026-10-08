"""Scrapling 网页元数据抓取：抓取与解析合并为一步。

选择抓取器拉取页面后，直接在返回的 Response 对象上用 CSS 选择器提取数据。
"""

from urllib.parse import urljoin

from scrapling.fetchers import DynamicFetcher, Fetcher, StealthyFetcher

from xingren.core.fields import extract_fields

# 详情区块选择器：抓取成功后顺带解析「标签: 值」字段，无匹配时 details 为空列表
DETAIL_SELECTOR = ".space-y-2 > *"


def fetch_page(url: str, timeout: int = 20, proxy: str | None = None):
    """按 Fetcher → DynamicFetcher → StealthyFetcher 降级抓取，成功返回 Response，全部失败返回 None。

    配置代理时先用代理抓取，代理不可用则回退直连重试整条降级链。
    """
    engines = (
        lambda p: Fetcher.get(url, timeout=timeout, proxy=p),
        # 浏览器抓取器的 timeout 单位是毫秒，与 Fetcher（秒）不同
        lambda p: DynamicFetcher.fetch(url, timeout=timeout * 1000, proxy=p),
        lambda p: StealthyFetcher.fetch(url, timeout=timeout * 1000, proxy=p),
    )
    combos: list[tuple] = [(engine, proxy) for engine in engines] if proxy else []
    combos += [(engine, None) for engine in engines]
    for i, (engine, p) in enumerate(combos):
        if proxy and i == len(engines):
            print("  代理抓取失败，回退直连重试…")
        try:
            response = engine(p)
        except Exception as exc:  # 网络错误、代理/浏览器不可用、反爬拦截等都走降级
            via = f"（代理 {p}）" if p else ""
            print(f"  抓取失败{via}（{type(exc).__name__}: {exc}），尝试下一抓取器…")
            continue
        status = getattr(response, "status", None)
        if response is not None and (status is None or status < 400):
            return response
        print(f"  抓取返回 HTTP {status}，尝试下一抓取器…")
    return None


def extract_metadata(response, base_url: str) -> dict:
    """在 Response 上用 CSS 选择器按优先级提取元数据，相对路径用 urljoin 补全。"""

    def attr(selector: str) -> str:
        return response.css(selector).extract_first() or ""

    title = (
        attr('meta[property="og:title"]::attr(content)')
        or attr('meta[name="twitter:title"]::attr(content)')
    )
    if not title:
        title_elements = response.css("title")
        if title_elements:
            title = title_elements[0].text.strip()

    thumbnail = attr('meta[property="og:image"]::attr(content)')
    favicon = attr('link[rel*="icon"]::attr(href)')

    return {
        "title": title.strip() if title else "",
        "thumbnail": urljoin(base_url, thumbnail) if thumbnail else "",
        "favicon": urljoin(base_url, favicon) if favicon else "",
    }


def get_metadata(url: str, timeout: int = 20, proxy: str | None = None) -> dict:
    """抓取一个 URL 的元数据；全部抓取失败时仅保留 URL，标题留空供手动补充。"""
    meta = {"url": url, "title": "", "thumbnail": "", "favicon": "", "success": False, "details": []}
    response = fetch_page(url, timeout=timeout, proxy=proxy)
    if response is None:
        return meta
    base_url = getattr(response, "url", None) or url
    meta.update(extract_metadata(response, base_url))
    try:
        meta["details"] = extract_fields(response.css(DETAIL_SELECTOR))
    except Exception:  # 解析任意 HTML 时个别页面可能出错，不影响主元数据
        meta["details"] = []
    meta["success"] = True
    return meta
