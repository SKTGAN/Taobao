from __future__ import annotations

import urllib.parse


PRODUCT_HOSTS = {"item.taobao.com", "detail.tmall.com"}
SHORT_LINK_HOST = "m.tb.cn"


def normalize_product_url(url: str) -> str:
    """Validate a supported product URL and remove disposable tracking parameters."""
    url = url.strip()
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()

    if parsed.scheme != "https" or host not in PRODUCT_HOSTS | {SHORT_LINK_HOST}:
        raise ValueError("首版只接受淘宝、天猫或 m.tb.cn 商品链接")

    if host == SHORT_LINK_HOST:
        if not parsed.path or parsed.path == "/":
            raise ValueError("m.tb.cn 商品短链不完整")
        return urllib.parse.urlunparse(("https", host, parsed.path, "", parsed.query, ""))

    product_ids = urllib.parse.parse_qs(parsed.query).get("id", [])
    product_id = product_ids[0].strip() if product_ids else ""
    if not product_id or not product_id.isdigit():
        raise ValueError("商品链接缺少有效的商品 ID")

    return f"https://{host}/item.htm?id={product_id}"
