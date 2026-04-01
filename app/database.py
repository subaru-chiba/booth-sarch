import aiosqlite
import os

DB_PATH = os.environ.get("DB_PATH", "booth_trends.db")


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                booth_item_id INTEGER UNIQUE NOT NULL,
                name TEXT NOT NULL,
                shop_name TEXT,
                shop_url TEXT,
                image_url TEXT,
                category TEXT,
                added_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                sold_count INTEGER,
                wish_count INTEGER,
                price INTEGER,
                fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_product_id ON snapshots(product_id);
            CREATE INDEX IF NOT EXISTS idx_snapshots_fetched_at ON snapshots(fetched_at);
        """)
        # migration: add category column if it doesn't exist yet
        try:
            await db.execute("ALTER TABLE products ADD COLUMN category TEXT")
            await db.commit()
        except Exception:
            pass
        await db.commit()
