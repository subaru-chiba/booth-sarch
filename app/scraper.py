"""
Booth product data fetcher.

Booth provides a public JSON endpoint for each item:
  https://booth.pm/ja/items/{item_id}.json

This module fetches and normalizes that data.
"""

import re
from dataclasses import dataclass
from typing import Optional

import httpx

ITEM_API = "https://booth.pm/ja/items/{item_id}.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; booth-sarch/1.0)",
    "Accept": "application/json",
    "Accept-Language": "ja,en;q=0.9",
}


@dataclass
class ProductInfo:
    booth_item_id: int
    name: str
    shop_name: Optional[str]
    shop_url: Optional[str]
    image_url: Optional[str]
    sold_count: Optional[int]
    wish_count: Optional[int]
    price: Optional[int]
    category: Optional[str]


def parse_item_id(url_or_id: str) -> int:
    """Extract the numeric item ID from a URL or bare ID string."""
    url_or_id = url_or_id.strip()
    # bare integer
    if url_or_id.isdigit():
        return int(url_or_id)
    # https://booth.pm/ja/items/1234567
    # https://someshop.booth.pm/items/1234567
    m = re.search(r"/items/(\d+)", url_or_id)
    if m:
        return int(m.group(1))
    raise ValueError(f"Cannot parse item ID from: {url_or_id!r}")


async def fetch_product(item_id: int) -> ProductInfo:
    url = ITEM_API.format(item_id=item_id)
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(url, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()

    name = data.get("name") or f"Item {item_id}"
    price = _parse_price(data)
    sold_count = data.get("sold_count")
    wish_count = data.get("wish_lists_count")

    shop = data.get("shop") or {}
    shop_name = shop.get("name")
    shop_url = shop.get("url")

    images = data.get("images") or []
    image_url = images[0].get("original") if images else None

    # category: may be a single object or a list
    raw_cat = data.get("category") or data.get("categories")
    if isinstance(raw_cat, list):
        category = raw_cat[0].get("name") if raw_cat else None
    elif isinstance(raw_cat, dict):
        category = raw_cat.get("name")
    else:
        category = None

    return ProductInfo(
        booth_item_id=item_id,
        name=name,
        shop_name=shop_name,
        shop_url=shop_url,
        image_url=image_url,
        sold_count=sold_count,
        wish_count=wish_count,
        price=price,
        category=category,
    )


def _parse_price(data: dict) -> Optional[int]:
    price = data.get("price")
    if price is None:
        return None
    if isinstance(price, (int, float)):
        return int(price)
    # "1,500" or "¥1,500" format
    cleaned = re.sub(r"[^\d]", "", str(price))
    return int(cleaned) if cleaned else None
