import aiosqlite
from datetime import datetime

DB_NAME = "park_service.db"

PARKS_FIXED = ["Территория Такси", "Инслига", "Свобода", "Другой таксопарк / Улица"]


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS parks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                park_id INTEGER,
                model TEXT,
                plate_number TEXT,
                works TEXT,
                norms_hours REAL DEFAULT 0,
                status TEXT DEFAULT 'На парковке',
                status_since TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                note TEXT DEFAULT '',
                urgent INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS parts_waiting (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                car_id INTEGER,
                name TEXT NOT NULL,
                arrived INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS car_works (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                car_id INTEGER,
                name TEXT NOT NULL,
                hours REAL DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS norms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                hours REAL NOT NULL,
                category TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.commit()
        # миграция на случай старой базы без urgent
        try:
            await db.execute("ALTER TABLE cars ADD COLUMN urgent INTEGER DEFAULT 0")
            await db.commit()
        except Exception:
            pass

    # фиксированные парки — только если таблица пуста
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM parks") as cur:
            count = (await cur.fetchone())[0]
    if count == 0:
        for p in PARKS_FIXED:
            await add_park(p)

    # стартовый справочник нормо-часов — только если пусто
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM norms") as cur:
            count = (await cur.fetchone())[0]
    if count == 0:
        try:
            from norms_seed import SEED_NORMS
            for key, (display, hours, cat) in SEED_NORMS.items():
                await add_norm(key, display, hours, cat)
        except ImportError:
            pass


# ============================================================
# ПАРКИ
# ============================================================

async def add_park(name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute("INSERT INTO parks (name) VALUES (?)", (name,))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def get_parks():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, name FROM parks ORDER BY id") as cur:
            return await cur.fetchall()


async def get_park_by_name(name: str):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, name FROM parks WHERE LOWER(name) = LOWER(?)", (name,)
        ) as cur:
            return await cur.fetchone()


# ============================================================
# МАШИНЫ
# ============================================================

async def add_car(park_id, model, plate, works, norms_hours):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO cars (park_id, model, plate_number, works, norms_hours) VALUES (?, ?, ?, ?, ?)",
            (park_id, model, plate, works, norms_hours)
        )
        await db.commit()
        async with db.execute("SELECT last_insert_rowid()") as cur:
            return (await cur.fetchone())[0]


async def get_cars_by_park(park_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, model, plate_number, works, norms_hours, status, status_since, note, urgent "
            "FROM cars WHERE park_id = ? ORDER BY id DESC", (park_id,)
        ) as cur:
            return await cur.fetchall()


async def get_all_cars():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, park_id, model, plate_number, works, norms_hours, status, status_since, note, urgent "
            "FROM cars ORDER BY id DESC"
        ) as cur:
            return await cur.fetchall()


async def get_car(car_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, park_id, model, plate_number, works, norms_hours, status, status_since, note, urgent "
            "FROM cars WHERE id = ?", (car_id,)
        ) as cur:
            return await cur.fetchone()


async def find_car_by_plate(query: str):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, park_id, model, plate_number, works, norms_hours, status, status_since, note, urgent "
            "FROM cars WHERE LOWER(plate_number) LIKE LOWER(?) ORDER BY id DESC",
            (f"%{query}%",)
        ) as cur:
            return await cur.fetchall()


async def update_car_status(car_id, status):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE cars SET status = ?, status_since = CURRENT_TIMESTAMP WHERE id = ?",
            (status, car_id)
        )
        await db.commit()


async def update_car_note(car_id, note):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE cars SET note = ? WHERE id = ?", (note, car_id))
        await db.commit()


async def set_urgent(car_id, urgent):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE cars SET urgent = ? WHERE id = ?", (1 if urgent else 0, car_id))
        await db.commit()


async def delete_car(car_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM parts_waiting WHERE car_id = ?", (car_id,))
        await db.execute("DELETE FROM car_works WHERE car_id = ?", (car_id,))
        await db.execute("DELETE FROM cars WHERE id = ?", (car_id,))
        await db.commit()


# ============================================================
# ЗАПЧАСТИ
# ============================================================

async def add_part_waiting(car_id, name):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO parts_waiting (car_id, name) VALUES (?, ?)", (car_id, name)
        )
        await db.commit()


async def get_parts_waiting(car_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, name, arrived FROM parts_waiting WHERE car_id = ? ORDER BY id",
            (car_id,)
        ) as cur:
            return await cur.fetchall()


async def mark_part_arrived(part_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE parts_waiting SET arrived = 1 WHERE id = ?", (part_id,))
        await db.commit()


async def delete_part(part_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM parts_waiting WHERE id = ?", (part_id,))
        await db.commit()


async def all_parts_arrived(car_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM parts_waiting WHERE car_id = ? AND arrived = 0",
            (car_id,)
        ) as cur:
            return (await cur.fetchone())[0] == 0


# ============================================================
# РАБОТЫ МАШИНЫ
# ============================================================

async def get_car_works(car_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, name, hours FROM car_works WHERE car_id = ? ORDER BY id",
            (car_id,)
        ) as cur:
            return await cur.fetchall()


async def add_car_work(car_id, name, hours):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO car_works (car_id, name, hours) VALUES (?, ?, ?)",
            (car_id, name, hours)
        )
        await db.commit()
    await recalc_car_hours(car_id)


async def delete_car_work(work_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT car_id FROM car_works WHERE id = ?", (work_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        car_id = row[0]
        await db.execute("DELETE FROM car_works WHERE id = ?", (work_id,))
        await db.commit()
    await recalc_car_hours(car_id)
    return car_id


async def recalc_car_hours(car_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COALESCE(SUM(hours), 0) FROM car_works WHERE car_id = ?", (car_id,)) as cur:
            total = (await cur.fetchone())[0]
        async with db.execute("SELECT name FROM car_works WHERE car_id = ? ORDER BY id", (car_id,)) as cur:
            names = [r[0] for r in await cur.fetchall()]
        works_text = "; ".join(names) if names else ""
        await db.execute(
            "UPDATE cars SET norms_hours = ?, works = ? WHERE id = ?",
            (total, works_text, car_id)
        )
        await db.commit()


# ============================================================
# НОРМО-ЧАСЫ
# ============================================================

async def get_all_norms():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT id, key, display_name, hours, category FROM norms ORDER BY category, display_name"
        ) as cur:
            return await cur.fetchall()


async def add_norm(key, display_name, hours, category="Прочее"):
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute(
                "INSERT INTO norms (key, display_name, hours, category) VALUES (?, ?, ?, ?)",
                (key.lower(), display_name, hours, category)
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def delete_norm(norm_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM norms WHERE id = ?", (norm_id,))
        await db.commit()


# ============================================================
# ПОДПИСЧИКИ УТРЕННЕЙ СВОДКИ
# ============================================================

async def add_subscriber(chat_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO subscribers (chat_id) VALUES (?)", (chat_id,))
        await db.commit()


async def get_subscribers():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT chat_id FROM subscribers") as cur:
            return [r[0] for r in await cur.fetchall()]


# ============================================================
# НАСТРОЙКИ (например, час утренней сводки)
# ============================================================

async def get_setting(key):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_setting(key, value):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value))
        )
        await db.commit()


# ============================================================
# ОБЩИЕ СВОДКИ И УТИЛИТЫ
# ============================================================

async def get_stats_by_park():
    parks = await get_parks()
    result = []
    for p in parks:
        cars = await get_cars_by_park(p[0])
        result.append({"park_id": p[0], "park_name": p[1], "cars": cars})
    return result


def days_in_status(status_since_str) -> int:
    if not status_since_str:
        return 0
    try:
        dt = datetime.fromisoformat(str(status_since_str).replace("Z", "").split(".")[0])
        return max(0, (datetime.now() - dt).days)
    except Exception:
        return 0


def days_circle(days: int) -> str:
    if days <= 2:
        return "🟢"
    elif days <= 4:
        return "🟡"
    else:
        return "🔴"