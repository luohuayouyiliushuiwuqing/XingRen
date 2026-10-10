"""Scrapling 网页元数据抓取：抓取与解析合并为一步。

选择抓取器拉取页面后，直接在返回的 Response 对象上用 CSS 选择器提取数据。
"""

from urllib.parse import urljoin, urlparse

from scrapling.fetchers import DynamicFetcher, Fetcher, StealthyFetcher

from almond.core.fields import extract_fields

# 详情区块选择器：抓取成功后顺带解析「标签: 值」字段，无匹配时 details 为空列表
DETAIL_SELECTOR = ".space-y-2 > *"

# 国内域名后缀（.cn 及其二级后缀）
_DOMESTIC_SUFFIXES = (".cn", ".com.cn", ".net.cn", ".org.cn")
# 常见国内域名（不含 .cn 后缀，需显式列出）
_DOMESTIC_DOMAINS: set[str] = {
    "baidu.com", "bilibili.com", "zhihu.com", "weibo.com", "douyin.com",
    "xiaohongshu.com", "taobao.com", "tmall.com", "jd.com", "pinduoduo.com",
    "qq.com", "weixin.qq.com", "163.com", "126.com", "sina.com", "sohu.com",
    "csdn.net", "cnblogs.com", "jianshu.com", "juejin.cn",
    "aliyun.com", "tencent.com", "huawei.com", "xiaomi.com", "mi.com",
    "xiaomimimo.com", "deepseek.com",
    "apple.com", "microsoft.com", "google.com.hk",
    "feishu.cn", "dingtalk.com", "yuque.com",
    "hdslb.com", "127.net", "netease.com", "kimi.com",
}


def _is_domestic(url: str) -> bool:
    """判断 URL 是否为国内网站（不走代理）。"""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if any(host.endswith(s) for s in _DOMESTIC_SUFFIXES):
        return True
    # 子域名匹配：mimo.mi.com → 检查 mi.com
    parts = host.split(".")
    for i in range(len(parts) - 1):
        if ".".join(parts[i:]) in _DOMESTIC_DOMAINS:
            return True
    return False


def fetch_page(url: str, timeout: int = 20, proxy: str | None = None):
    """按 Fetcher → DynamicFetcher → StealthyFetcher 降级抓取，成功返回 Response，全部失败返回 None。

    国内网站自动跳过代理直连；外国站配置代理时先用代理抓取，代理不可用则回退直连重试整条降级链。
    """
    if proxy and _is_domestic(url):
        print(f"  国内站点，跳过代理直连：{urlparse(url).hostname}")
        proxy = None
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


def get_metadata(url: str, timeout: int = 20, proxy: str | None = None,
                 selector: str | None = None, cover: bool | None = None) -> dict:
    """抓取一个 URL 的元数据；全部抓取失败时仅保留 URL，标题留空供手动补充。

    selector：按站点覆盖详情选择器（空/None 用默认 DETAIL_SELECTOR）——
      域名级配置（domains.detail_selector）由调用方传进来，选择器写错只会让详情为空。
    cover：按域名的封面开关——False=不要封面，True=只要图标（拿 favicon 当封面），
      None=默认走 og:image。
    """
    meta = {"url": url, "title": "", "thumbnail": "", "favicon": "", "success": False, "details": []}
    response = fetch_page(url, timeout=timeout, proxy=proxy)
    if response is None:
        return meta
    base_url = getattr(response, "url", None) or url
    meta.update(extract_metadata(response, base_url))
    try:
        meta["details"] = extract_fields(response.css(selector or DETAIL_SELECTOR))
    except Exception:  # 解析任意 HTML 时个别页面可能出错（含非法选择器），不影响主元数据
        meta["details"] = []
    if cover is False:
        meta["thumbnail"] = ""
    elif cover is True and meta.get("favicon"):
        meta["thumbnail"] = meta["favicon"]
    meta["success"] = True
    return meta
