from __future__ import annotations

import urllib.parse


PRODUCT_HOSTS = {"item.taobao.com", "detail.tmall.com"}
SHORT_LINK_HOST = "m.tb.cn"


def _query_value(url: str, *names: str) -> str:
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    for name in names:
        values = query.get(name, [])
        if values and values[0].strip():
            return values[0].strip()
    return ""


def product_id_from_url(url: str) -> str:
    return _query_value(url, "id")


def sku_id_from_url(url: str) -> str:
    return _query_value(url, "skuId", "skuid")


def with_sku_id(url: str, sku_id: str) -> str:
    normalized = normalize_product_url(url)
    sku_id = str(sku_id or "").strip()
    if not sku_id:
        return normalized
    if not sku_id.isdigit():
        raise ValueError("SKU ID 必须是数字")
    parsed = urllib.parse.urlparse(normalized)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
    query = [(key, value) for key, value in query if key.lower() != "skuid"]
    query.append(("skuId", sku_id))
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def resolve_product_selection(base_url: str, sku_or_url: str) -> str:
    """Build a stable task URL from a SKU ID or a URL for the same product."""
    normalized_base = normalize_product_url(base_url)
    selection = str(sku_or_url or "").strip()
    if not selection:
        return normalized_base
    if selection.lower().startswith(("https://", "http://")):
        selected_url = normalize_product_url(selection)
        if product_id_from_url(selected_url) != product_id_from_url(normalized_base):
            raise ValueError("款式链接必须属于当前商品")
        return selected_url
    return with_sku_id(normalized_base, selection)


def normalize_product_url(url: str) -> str:
    """Validate a product URL and retain only stable product/SKU identifiers."""
    url = url.strip()
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()

    if parsed.scheme != "https" or host not in PRODUCT_HOSTS | {SHORT_LINK_HOST}:
        raise ValueError("首版只接受淘宝、天猫或 m.tb.cn 商品链接")

    if host == SHORT_LINK_HOST:
        if not parsed.path or parsed.path == "/":
            raise ValueError("m.tb.cn 商品短链不完整")
        return urllib.parse.urlunparse(("https", host, parsed.path, "", parsed.query, ""))

    query = urllib.parse.parse_qs(parsed.query)
    product_ids = query.get("id", [])
    product_id = product_ids[0].strip() if product_ids else ""
    if not product_id or not product_id.isdigit():
        raise ValueError("商品链接缺少有效的商品 ID")

    stable_query = [("id", product_id)]
    sku_id = sku_id_from_url(url)
    if sku_id:
        if not sku_id.isdigit():
            raise ValueError("商品链接包含无效的 SKU ID")
        stable_query.append(("skuId", sku_id))
    return urllib.parse.urlunparse(
        ("https", host, "/item.htm", "", urllib.parse.urlencode(stable_query), "")
    )
