"""
Booth product data fetcher.

Public endpoints:
  https://booth.pm/ja/items/{item_id}.json  — product detail
  https://booth.pm/ja/search.json           — search / category browse
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx
from bs4 import BeautifulSoup

ITEM_API   = "https://booth.pm/ja/items/{item_id}.json"
SEARCH_URL = "https://booth.pm/ja/search"

HEADERS_JSON = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, */*; q=0.01",
    "Accept-Language": "ja,en;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://booth.pm/",
}

HEADERS_HTML = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.9",
    "Referer": "https://booth.pm/",
}

# Keep backward compat
HEADERS = HEADERS_JSON

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
    Fetch products from Booth search page.
    Tries JSON API first, falls back to HTML scraping.
    Returns (products, total_pages).
    """
    query = ""
    for cat in CATEGORIES:
        if cat["id"] == category_id:
            query = cat.get("query", "")
            break

    params: dict = {"q": query, "sort": sort, "page": page}

    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        # 1) Try JSON API
        try:
            resp = await client.get(SEARCH_URL, params=params, headers=HEADERS_JSON)
            if resp.status_code == 200:
                ct = resp.headers.get("content-type", "")
                if "json" in ct:
                    data = resp.json()
                    items = data.get("items") or []
                    total = int(data.get("total_pages") or data.get("pages") or 1)
                    return [_parse_item_dict(i) for i in items], total
        except Exception:
            pass

        # 2) Fall back: fetch HTML page and scrape
        resp = await client.get(SEARCH_URL, params=params, headers=HEADERS_HTML)
        resp.raise_for_status()

    return _parse_search_html(resp.text)


def _parse_search_html(html: str) -> tuple[list[ProductInfo], int]:
    """Parse product cards from Booth's search result HTML."""
    soup = BeautifulSoup(html, "html.parser")

    # Try embedded JSON first (Rails / React initial state)
    for script in soup.find_all("script"):
        text = script.string or ""
        for pattern in [
            r'window\.__(?:STORE|STATE|INITIAL_STATE|INITIAL_DATA)__\s*=\s*(\{.+?\});',
            r'"items"\s*:\s*(\[.+?\])',
        ]:
            m = re.search(pattern, text, re.DOTALL)
            if m:
                try:
                    raw = json.loads(m.group(1))
                    items_raw = raw if isinstance(raw, list) else (
                        raw.get("items") or raw.get("search", {}).get("items") or []
                    )
                    if items_raw:
                        products = [_parse_item_dict(i) for i in items_raw]
                        return products, 1
                except Exception:
                    pass

    # HTML structure scraping
    products: list[ProductInfo] = []

    # Booth item cards — try multiple selector patterns
    selectors = [
        "li[data-product]",
        "div[data-product]",
        ".item-card",
        ".market-item",
        "li.item",
        "[data-item-id]",
    ]
    cards = []
    for sel in selectors:
        cards = soup.select(sel)
        if cards:
            break

    for card in cards:
        try:
            # Item ID from data attributes or href
            item_id = (
                card.get("data-product")
                or card.get("data-item-id")
                or card.get("data-id")
            )
            if not item_id:
                link = card.find("a", href=re.compile(r"/items/(\d+)"))
                if link:
                    m = re.search(r"/items/(\d+)", link["href"])
                    item_id = m.group(1) if m else None
            if not item_id:
                continue

            name_el = card.select_one(".item-name, .name, h2, h3, [class*='name']")
            name = name_el.get_text(strip=True) if name_el else f"Item {item_id}"

            price_el = card.select_one(".price, [class*='price']")
            price_text = price_el.get_text(strip=True) if price_el else ""
            price_num = int(re.sub(r"[^\d]", "", price_text)) if re.search(r"\d", price_text) else None

            wish_el = card.select_one("[class*='wish'], [class*='heart']")
            wish_text = wish_el.get_text(strip=True) if wish_el else ""
            wish_num = int(re.sub(r"[^\d]", "", wish_text)) if re.search(r"\d", wish_text) else None

            img_el = card.find("img")
            image_url = img_el.get("data-src") or img_el.get("src") if img_el else None

            shop_el = card.select_one(".shop-name, [class*='shop']")
            shop_name = shop_el.get_text(strip=True) if shop_el else None

            products.append(ProductInfo(
                booth_item_id=int(item_id),
                name=name,
                shop_name=shop_name,
                shop_url=None,
                image_url=image_url,
                sold_count=None,
                wish_count=wish_num,
                price=price_num,
                category=None,
            ))
        except Exception:
            continue

    # Pagination
    total = 1
    pager = soup.select_one(".pagination, [class*='pager']")
    if pager:
        page_links = pager.find_all("a")
        nums = [int(a.get_text(strip=True)) for a in page_links if a.get_text(strip=True).isdigit()]
        total = max(nums) if nums else 1

    return products, total


def _parse_price(data: dict) -> Optional[int]:
    price = data.get("price")
    if price is None:
        return None
    if isinstance(price, (int, float)):
        return int(price)
    cleaned = re.sub(r"[^\d]", "", str(price))
    return int(cleaned) if cleaned else None
