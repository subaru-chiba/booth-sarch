from contextlib import asynccontextmanager
from typing import Optional
import json
from urllib.parse import urlencode

import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import get_db, init_db
from .scraper import fetch_product, parse_item_id

scheduler = AsyncIOScheduler()
templates = Jinja2Templates(directory="templates")
templates.env.filters["urlencode"] = lambda v: urlencode({"q": v})[3:]  # strip "q="


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    scheduler.add_job(refresh_all_products, "interval", hours=6, id="auto_refresh")
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Booth Sales Trend Tracker", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

async def refresh_all_products():
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, booth_item_id FROM products")
        products = await cursor.fetchall()
        for row in products:
            try:
                info = await fetch_product(row["booth_item_id"])
                await db.execute(
                    """INSERT INTO snapshots (product_id, sold_count, wish_count, price)
                       VALUES (?, ?, ?, ?)""",
                    (row["id"], info.sold_count, info.wish_count, info.price),
                )
                await db.execute(
                    """UPDATE products SET name=?, shop_name=?, shop_url=?, image_url=?, category=?
                       WHERE id=?""",
                    (info.name, info.shop_name, info.shop_url, info.image_url, info.category, row["id"]),
                )
            except Exception:
                pass
        await db.commit()
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    category: Optional[str] = Query(default=None),
    sort: str = Query(default="sold_30d"),
):
    db = await get_db()
    try:
        # All distinct categories for filter tabs
        cursor = await db.execute(
            "SELECT DISTINCT category FROM products WHERE category IS NOT NULL ORDER BY category"
        )
        categories = [row[0] for row in await cursor.fetchall()]

        # Build main query:
        # sold_30d = latest sold_count - oldest sold_count within the past 30 days
        # (falls back to oldest available snapshot when no 30-day-old data exists)
        query = """
            SELECT
                p.id, p.booth_item_id, p.name, p.shop_name, p.image_url, p.category,
                s_latest.sold_count,
                s_latest.wish_count,
                s_latest.price,
                s_latest.fetched_at,
                (
                    s_latest.sold_count - COALESCE(
                        (SELECT sold_count FROM snapshots
                         WHERE product_id = p.id
                           AND fetched_at <= datetime('now', '-30 days')
                         ORDER BY fetched_at DESC LIMIT 1),
                        (SELECT sold_count FROM snapshots
                         WHERE product_id = p.id
                         ORDER BY fetched_at ASC LIMIT 1)
                    )
                ) AS sold_30d
            FROM products p
            LEFT JOIN snapshots s_latest ON s_latest.id = (
                SELECT id FROM snapshots WHERE product_id = p.id ORDER BY fetched_at DESC LIMIT 1
            )
        """

        params: list = []
        if category:
            query += " WHERE p.category = ?"
            params.append(category)

        order = {
            "sold_30d": "sold_30d DESC NULLS LAST",
            "sold_total": "s_latest.sold_count DESC NULLS LAST",
            "wish": "s_latest.wish_count DESC NULLS LAST",
            "added": "p.added_at DESC",
        }.get(sort, "sold_30d DESC NULLS LAST")
        query += f" ORDER BY {order}"

        cursor = await db.execute(query, params)
        products = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()

    return templates.TemplateResponse("index.html", {
        "request": request,
        "products": products,
        "categories": categories,
        "current_category": category,
        "current_sort": sort,
    })


@app.post("/products/add")
async def add_product(url_or_id: str = Form(...)):
    try:
        item_id = parse_item_id(url_or_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        info = await fetch_product(item_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Boothからデータを取得できませんでした: {e}")

    db = await get_db()
    try:
        await db.execute(
            """INSERT OR IGNORE INTO products (booth_item_id, name, shop_name, shop_url, image_url, category)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (info.booth_item_id, info.name, info.shop_name, info.shop_url, info.image_url, info.category),
        )
        cursor = await db.execute(
            "SELECT id FROM products WHERE booth_item_id = ?", (item_id,)
        )
        row = await cursor.fetchone()
        product_id = row["id"]

        await db.execute(
            """INSERT INTO snapshots (product_id, sold_count, wish_count, price)
               VALUES (?, ?, ?, ?)""",
            (product_id, info.sold_count, info.wish_count, info.price),
        )
        await db.commit()
    finally:
        await db.close()

    return RedirectResponse(url=f"/products/{item_id}", status_code=303)


@app.get("/products/{item_id}", response_class=HTMLResponse)
async def product_detail(request: Request, item_id: int):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM products WHERE booth_item_id = ?", (item_id,)
        )
        product = await cursor.fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="商品が見つかりません")
        product = dict(product)

        cursor = await db.execute(
            """SELECT sold_count, wish_count, price, fetched_at
               FROM snapshots WHERE product_id = ?
               ORDER BY fetched_at ASC""",
            (product["id"],),
        )
        snapshots = [dict(row) for row in await cursor.fetchall()]
    finally:
        await db.close()

    chart_labels = [s["fetched_at"] for s in snapshots]
    chart_sold = [s["sold_count"] for s in snapshots]
    chart_wish = [s["wish_count"] for s in snapshots]
    chart_price = [s["price"] for s in snapshots]

    return templates.TemplateResponse("product.html", {
        "request": request,
        "product": product,
        "snapshots": snapshots,
        "chart_labels": json.dumps(chart_labels),
        "chart_sold": json.dumps(chart_sold),
        "chart_wish": json.dumps(chart_wish),
        "chart_price": json.dumps(chart_price),
    })


@app.post("/products/{item_id}/refresh")
async def refresh_product(item_id: int):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id FROM products WHERE booth_item_id = ?", (item_id,)
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="商品が見つかりません")
        product_id = row["id"]

        try:
            info = await fetch_product(item_id)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Boothからデータを取得できませんでした: {e}")

        await db.execute(
            """INSERT INTO snapshots (product_id, sold_count, wish_count, price)
               VALUES (?, ?, ?, ?)""",
            (product_id, info.sold_count, info.wish_count, info.price),
        )
        await db.execute(
            """UPDATE products SET name=?, shop_name=?, shop_url=?, image_url=?, category=?
               WHERE id=?""",
            (info.name, info.shop_name, info.shop_url, info.image_url, info.category, product_id),
        )
        await db.commit()
    finally:
        await db.close()

    return RedirectResponse(url=f"/products/{item_id}", status_code=303)


@app.post("/products/{item_id}/delete")
async def delete_product(item_id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM products WHERE booth_item_id = ?", (item_id,))
        await db.commit()
    finally:
        await db.close()
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/products/{item_id}/snapshots")
async def api_snapshots(item_id: int):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id FROM products WHERE booth_item_id = ?", (item_id,)
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404)
        cursor = await db.execute(
            """SELECT sold_count, wish_count, price, fetched_at
               FROM snapshots WHERE product_id = ?
               ORDER BY fetched_at ASC""",
            (row["id"],),
        )
        return [dict(r) for r in await cursor.fetchall()]
    finally:
        await db.close()
