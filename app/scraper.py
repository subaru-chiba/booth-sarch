"""
Booth product data fetcher.

Public endpoints:
  https://booth.pm/ja/items/{item_id}.json  — product detail
  https://booth.pm/ja/search.json           — search / category browse
"""

import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

ITEM_API   = "https://booth.pm/ja/items/{item_id}.json"
SEARCH_URL = "https://booth.pm/ja/search"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ja,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://booth.pm/",
}

# ---------------------------------------------------------------------------
# Category definitions
# IDs and slugs are taken from Booth's public browse URLs and item responses.
# ---------------------------------------------------------------------------
CATEGORIES: list[dict] = [
    {"id": None,      "name": "すべて",         "emoji": "🏠", "query": ""},
    {"id": "3d",      "name": "3Dモデル",        "emoji": "🎭", "query": "3Dモデル"},
    {"id": "vrchat",  "name": "VRChat",          "emoji": "🥽", "query": "VRChat"},
    {"id": "game",    "name": "ゲーム",          "emoji": "🎮", "query": "ゲーム"},
    {"id": "live2d",  "name": "Live2D",          "emoji": "✨", "query": "Live2D"},
    {"id": "music",   "name": "音楽・サウンド",  "emoji": "🎵", "query": "音楽 サウンド"},
    {"id": "illust",  "name": "イラスト・漫画",  "emoji": "🎨", "query": "イラスト"},
    {"id": "novel",   "name": "小説・シナリオ",  "emoji": "📖", "query": "小説"},
    {"id": "font",    "name": "フォント",        "emoji": "✍️", "query": "フォント"},
    {"id": "assets",  "name": "素材・ツール",    "emoji": "🔧", "query": "素材"},
    {"id": "handmade","name": "ハンドメイド",    "emoji": "🧵", "query": "ハンドメイド"},
]

SORT_OPTIONS = [
    ("new",        "新着順"),
    ("wish_count", "ウィッシュ数順"),
]


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
    category_id: Optional[int] = field(default=None)


def parse_item_id(url_or_id: str) -> int:
    """Extract the numeric item ID from a URL or bare ID string."""
    url_or_id = url_or_id.strip()
    if url_or_id.isdigit():
        return int(url_or_id)
    m = re.search(r"/items/(\d+)", url_or_id)
    if m:
        return int(m.group(1))
    raise ValueError(f"Cannot parse item ID from: {url_or_id!r}")


def _parse_item_dict(data: dict) -> ProductInfo:
    item_id = data.get("id") or 0
    name = data.get("name") or f"Item {item_id}"
    price = _parse_price(data)
    sold_count = data.get("sold_count")
    wish_count = data.get("wish_lists_count")

    shop = data.get("shop") or {}
    shop_name = shop.get("name")
    shop_url  = shop.get("url")

    images = data.get("images") or []
    image_url = images[0].get("original") if images else None

    raw_cat = data.get("category") or data.get("categories")
    if isinstance(raw_cat, list):
        cat_obj = raw_cat[0] if raw_cat else {}
    elif isinstance(raw_cat, dict):
        cat_obj = raw_cat
    else:
        cat_obj = {}
    category    = cat_obj.get("name")
    category_id = cat_obj.get("id")

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
        category_id=category_id,
    )


async def fetch_product(item_id: int) -> ProductInfo:
    url = ITEM_API.format(item_id=item_id)
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(url, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
    return _parse_item_dict(data)


async def search_category(
    category_id: Optional[str] = None,
    sort: str = "new",
    page: int = 1,
) -> tuple[list[ProductInfo], int]:
    """
    Fetch products from Booth's search API using keyword search per category.
    Returns (products, total_pages).
    """
    # Look up the query string for this category
    query = ""
    for cat in CATEGORIES:
        if cat["id"] == category_id:
            query = cat.get("query", "")
            break

    params: dict = {"q": query, "sort": sort, "page": page}

    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(SEARCH_URL, params=params, headers=HEADERS)
        resp.raise_for_status()
        # Booth returns JSON when X-Requested-With: XMLHttpRequest is set
        data = resp.json()

    items = data.get("items") or []
    total = data.get("total_pages") or data.get("pages") or 1
    products = [_parse_item_dict(item) for item in items]
    return products, int(total)


def _parse_price(data: dict) -> Optional[int]:
    price = data.get("price")
    if price is None:
        return None
    if isinstance(price, (int, float)):
        return int(price)
    cleaned = re.sub(r"[^\d]", "", str(price))
    return int(cleaned) if cleaned else None
