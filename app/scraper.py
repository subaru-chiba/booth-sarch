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
SEARCH_API = "https://booth.pm/ja/search.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; booth-sarch/1.0)",
    "Accept": "application/json",
    "Accept-Language": "ja,en;q=0.9",
}

# ---------------------------------------------------------------------------
# Category definitions
# IDs and slugs are taken from Booth's public browse URLs and item responses.
# ---------------------------------------------------------------------------
CATEGORIES: list[dict] = [
    {"id": None,  "name": "すべて",         "emoji": "🏠"},
    {"id": 208,   "name": "3Dモデル",        "emoji": "🎭"},
    {"id": 4,     "name": "VRChat",          "emoji": "🥽"},
    {"id": 7,     "name": "ゲーム",          "emoji": "🎮"},
    {"id": 18,    "name": "Live2D",          "emoji": "✨"},
    {"id": 43,    "name": "音楽・サウンド",  "emoji": "🎵"},
    {"id": 6,     "name": "イラスト・漫画",  "emoji": "🎨"},
    {"id": 28,    "name": "小説・シナリオ",  "emoji": "📖"},
    {"id": 71,    "name": "フォント",        "emoji": "✍️"},
    {"id": 53,    "name": "素材・ツール",    "emoji": "🔧"},
    {"id": 9,     "name": "ハンドメイド",    "emoji": "🧵"},
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
    category_id: Optional[int] = None,
    sort: str = "new",
    page: int = 1,
) -> tuple[list[ProductInfo], int]:
    """
    Fetch products from Booth's search API filtered by category.
    Returns (products, total_pages).
    """
    params: dict = {"q": "", "sort": sort, "page": page}
    if category_id is not None:
        params["category_id"] = category_id

    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(SEARCH_API, params=params, headers=HEADERS)
        resp.raise_for_status()
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
