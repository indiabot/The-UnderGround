import os
import datetime
import json
import asyncpg
from typing import Optional, Dict, Any, List

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ========= ENV =========
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = os.getenv("ADMIN_ID")  # numeric string

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing (Railway Variables -> BOT_TOKEN)")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL missing (Railway PostgreSQL plugin)")
if not ADMIN_ID or not ADMIN_ID.isdigit():
    raise RuntimeError("ADMIN_ID missing or not numeric (Railway Variables -> ADMIN_ID)")

ADMIN_ID_INT = int(ADMIN_ID)

CLAIM_IMAGE_PATH = "claim.png"
HOME_IMAGE_PATH = "home.png"
SHOP_IMAGE_PATH = "shop.png"


# ========= MONEY HELPERS =========
def eur_to_cents(x: float) -> int:
    return int(round(x * 100))


def cents_to_eur_str(c: int) -> str:
    return f"{c/100:.2f}€"


# ========= DB SCHEMA =========
CREATE_USERS_SQL = """
CREATE TABLE IF NOT EXISTS users (
  user_id BIGINT PRIMARY KEY,
  first_name TEXT,
  last_name TEXT,
  username TEXT,
  language TEXT DEFAULT 'et',
  status TEXT DEFAULT 'NEW',          -- NEW/PENDING/SAFE/DECLINED
  state TEXT DEFAULT NULL,            -- NULL/WAITING_REF/BUY_ADDRESS
  spent_cents BIGINT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
"""

CREATE_CLAIMS_SQL = """
CREATE TABLE IF NOT EXISTS claims (
  id SERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(user_id),
  ref_username TEXT NOT NULL,
  status TEXT DEFAULT 'PENDING',      -- PENDING/ACCEPTED/DECLINED
  created_at TIMESTAMPTZ DEFAULT now(),
  decided_at TIMESTAMPTZ NULL
);
"""

CREATE_ITEMS_SQL = """
CREATE TABLE IF NOT EXISTS items (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  short_text TEXT NOT NULL,
  price_cents INT NOT NULL DEFAULT 0,
  photo_file_id TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
"""

CREATE_SETTINGS_SQL = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

CREATE_ORDERS_SQL = """
CREATE TABLE IF NOT EXISTS orders (
  id SERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(user_id),
  cart_json JSONB NOT NULL,
  subtotal_cents INT NOT NULL,
  delivery BOOLEAN NOT NULL,
  address TEXT NULL,
  delivery_fee_cents INT NOT NULL DEFAULT 0,
  total_cents INT NOT NULL,
  status TEXT NOT NULL DEFAULT 'NEW',   -- NEW/SEEN/DONE
  created_at TIMESTAMPTZ DEFAULT now()
);
"""

ALTER_USERS_SQL = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS language TEXT DEFAULT 'et';",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'NEW';",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS state TEXT DEFAULT NULL;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name TEXT;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS spent_cents BIGINT NOT NULL DEFAULT 0;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();",
]

ALTER_ITEMS_SQL = [
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS price_cents INT NOT NULL DEFAULT 0;",
]

ALTER_ORDERS_SQL = [
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_fee_cents INT NOT NULL DEFAULT 0;",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS total_cents INT NOT NULL DEFAULT 0;",
]


# ========= TEXTS =========
TEXTS: Dict[str, Dict[str, str]] = {
    "et": {
        "welcome": "Tere! Vajuta Verify",
        "verify": "✅ Verify",
        "waiting_ref": "Kirjuta oma sõbra @username, kelle käest sa selle boti said (näiteks: @mart).",
        "invalid_ref": "❌ Palun kirjuta korrektne @username (peab algama @-ga). Proovi uuesti.",
        "wait_admin": "⏳ Aitäh! Oota palun admini vastust.",
        "already_pending": "⏳ Su verifitseerimine on juba ootel. Oota admini vastust.",
        "accepted": "✅ Admin kinnitas su verifitseerimise. Sa oled nüüd SAFE. Tee /start",
        "declined": "❌ Admin lükkas su verifitseerimise tagasi.",
        "removed_safe": "❌ Admin eemaldas sind SAFE listist. Tee /start ja verifitseeri uuesti.",
        "added_safe": "✅ Admin lisas sind SAFE listi. Tee /start",
        "do_start": "Tee /start",
        "safe_welcome": (
            "*The UnderGround Market*\n\n"
            "Siin saad vaadata pakkumisi ja teha oste.\n"
            "Vali alt menüüst üks valik."
        ),
        "shop_title": "*Shop*\nVali toode.",
        "shop_empty": "Shop on hetkel tühi.",
        "help_text": "Help: kirjuta adminile.",
        "account_text": "Account",
        "buy_offline": "❌ Praegu on operator OFFLINE.",
        "buy_intro": "*Buy*\nVali toode ja kogus. Kui valmis, vajuta Next.",
        "buy_cart": "Cart",
        "buy_next": "✅ Next",
        "buy_clear": "❌ Clear",
        "buy_choose_qty": "Vali kogus:",
        "buy_delivery_q": "Kas on vaja delivery?",
        "buy_yes": "✅ Jah",
        "buy_no": "❌ Ei",
        "buy_send_address": "Kirjuta oma aadress.\n\nDelivery fee ja kell kirjutab admin pärast DM.",
        "buy_order_sent": "✅ Order saadetud. Admin kirjutab sulle.",
        "buy_need_items": "❌ Lisa vähemalt 1 item carti.",
        "admin_add_name": "/additem\nSaada itemi nimi:",
        "admin_add_text": "Saada lühike tekst (kirjeldus):",
        "admin_add_price": "Saada hind EUR (näiteks: 25 või 25.50):",
        "admin_add_photo": "Saada nüüd pilt (foto) selle itemi jaoks:",
        "admin_add_done": "✅ Item lisatud Shopi!",
        "admin_remove_pick": "Vali item, mida eemaldada:",
        "admin_removed": "✅ Item eemaldatud.",
        "admin_remove_empty": "Pole midagi eemaldada.",
        "admin_bad": "❌ Midagi läks valesti.",
        "back": "⬅️ Tagasi",
        "home": "⬅️ Home",
        "order_pickup_msg": "✅ Sinu order on valmis.\nAsukoht ja kellaaeg:",
        "order_completed_user": "✅ Order completed.",
        "admin_fee_prompt": "Kirjuta delivery fee EUR (näiteks: 5 või 7.50):",
    },
    "ru": {
        "welcome": "Привет! Нажми Verify",
        "verify": "✅ Verify",
        "waiting_ref": "Напиши @username друга, от которого ты получил бота (например: @mart).",
        "invalid_ref": "❌ Напиши корректный @username (должен начинаться с @). Попробуй ещё раз.",
        "wait_admin": "⏳ Спасибо! Дождись решения админа.",
        "already_pending": "⏳ Проверка уже в ожидании.",
        "accepted": "✅ Админ подтвердил проверку. Ты теперь SAFE. Напиши /start",
        "declined": "❌ Админ отклонил проверку.",
        "removed_safe": "❌ Админ удалил тебя из SAFE. Сделай /start и пройди проверку снова.",
        "added_safe": "✅ Админ добавил тебя в SAFE. Напиши /start",
        "do_start": "Напиши /start",
        "safe_welcome": (
            "*The UnderGround Market*\n\n"
            "Здесь ты можешь смотреть товары и делать заказы.\n"
            "Выбери пункт меню ниже."
        ),
        "shop_title": "*Shop*\nВыбери товар.",
        "shop_empty": "Shop сейчас пуст.",
        "help_text": "Help: напиши админу.",
        "account_text": "Account",
        "buy_offline": "❌ Сейчас оператор OFFLINE.",
        "buy_intro": "*Buy*\nВыбери товар и количество. Когда готов, нажми Next.",
        "buy_cart": "Cart",
        "buy_next": "✅ Next",
        "buy_clear": "❌ Clear",
        "buy_choose_qty": "Выбери количество:",
        "buy_delivery_q": "Нужна доставка?",
        "buy_yes": "✅ Да",
        "buy_no": "❌ Нет",
        "buy_send_address": "Напиши адрес.\n\nDelivery fee и время админ напишет позже в DM.",
        "buy_order_sent": "✅ Заказ отправлен. Админ напишет тебе.",
        "buy_need_items": "❌ Добавь хотя бы 1 товар в cart.",
        "admin_add_name": "/additem\nОтправь название товара:",
        "admin_add_text": "Отправь короткий текст (описание):",
        "admin_add_price": "Отправь цену EUR (пример: 25 или 25.50):",
        "admin_add_photo": "Теперь отправь фото товара:",
        "admin_add_done": "✅ Товар добавлен в Shop!",
        "admin_remove_pick": "Выбери товар для удаления:",
        "admin_removed": "✅ Удалено.",
        "admin_remove_empty": "Нечего удалять.",
        "admin_bad": "❌ Что-то пошло не так.",
        "back": "⬅️ Назад",
        "home": "⬅️ Home",
        "order_pickup_msg": "✅ Твой заказ готов.\nМесто и время:",
        "order_completed_user": "✅ Заказ выполнен.",
        "admin_fee_prompt": "Отправь delivery fee EUR (пример: 5 или 7.50):",
    },
    "en": {
        "welcome": "Hi! Press Verify",
        "verify": "✅ Verify",
        "waiting_ref": "Send your friend's @username who gave you this bot (example: @mart).",
        "invalid_ref": "❌ Please send a valid @username (must start with @). Try again.",
        "wait_admin": "⏳ Thanks. Wait for admin approval.",
        "already_pending": "⏳ Verification is pending.",
        "accepted": "✅ Admin approved you. You are SAFE now. Send /start",
        "declined": "❌ Admin declined your verification.",
        "removed_safe": "❌ Admin removed you from SAFE. Do /start and verify again.",
        "added_safe": "✅ Admin added you to SAFE. Send /start",
        "do_start": "Send /start",
        "safe_welcome": (
            "*The UnderGround Market*\n\n"
            "Browse items and place orders.\n"
            "Choose an option below."
        ),
        "shop_title": "*Shop*\nChoose an item.",
        "shop_empty": "Shop is empty.",
        "help_text": "Help: contact admin.",
        "account_text": "Account",
        "buy_offline": "❌ Operator is OFFLINE right now.",
        "buy_intro": "*Buy*\nPick items and quantities. When ready, press Next.",
        "buy_cart": "Cart",
        "buy_next": "✅ Next",
        "buy_clear": "❌ Clear",
        "buy_choose_qty": "Choose quantity:",
        "buy_delivery_q": "Need delivery?",
        "buy_yes": "✅ Yes",
        "buy_no": "❌ No",
        "buy_send_address": "Send your address.\n\nDelivery fee and time will be sent by admin in DM.",
        "buy_order_sent": "✅ Order sent. Admin will DM you.",
        "buy_need_items": "❌ Add at least 1 item to cart.",
        "admin_add_name": "/additem\nSend item name:",
        "admin_add_text": "Send short text (description):",
        "admin_add_price": "Send price EUR (example: 25 or 25.50):",
        "admin_add_photo": "Now send item photo:",
        "admin_add_done": "✅ Item added to Shop!",
        "admin_remove_pick": "Pick an item to remove:",
        "admin_removed": "✅ Removed.",
        "admin_remove_empty": "Nothing to remove.",
        "admin_bad": "❌ Something went wrong.",
        "back": "⬅️ Back",
        "home": "⬅️ Home",
        "order_pickup_msg": "✅ Your order is ready.\nLocation and time:",
        "order_completed_user": "✅ Order completed.",
        "admin_fee_prompt": "Send delivery fee EUR (example: 5 or 7.50):",
    },
}


def t(lang: str, key: str) -> str:
    if lang not in TEXTS:
        lang = "et"
    return TEXTS[lang].get(key, TEXTS["et"].get(key, key))


# ========= KEYBOARDS =========
def kb_languages() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🇪🇪 ET", callback_data="lang:et"),
        InlineKeyboardButton("🇷🇺 RU", callback_data="lang:ru"),
        InlineKeyboardButton("🇬🇧 EN", callback_data="lang:en"),
    ]])


def kb_languages_and_verify(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇪🇪 ET", callback_data="lang:et"),
            InlineKeyboardButton("🇷🇺 RU", callback_data="lang:ru"),
            InlineKeyboardButton("🇬🇧 EN", callback_data="lang:en"),
        ],
        [InlineKeyboardButton(t(lang, "verify"), callback_data="verify")],
    ])


def kb_safe_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Shop", callback_data="safe:shop"),
            InlineKeyboardButton("Buy", callback_data="safe:buy"),
        ],
        [
            InlineKeyboardButton("Help", callback_data="safe:help"),
            InlineKeyboardButton("Account", callback_data="safe:account"),
        ],
        [
            InlineKeyboardButton("🇪🇪 ET", callback_data="lang:et"),
            InlineKeyboardButton("🇷🇺 RU", callback_data="lang:ru"),
            InlineKeyboardButton("🇬🇧 EN", callback_data="lang:en"),
        ],
    ])


def kb_shop_items(lang: str, items: List[asyncpg.Record]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for it in items:
        price = cents_to_eur_str(int(it["price_cents"]))
        rows.append([InlineKeyboardButton(f"{it['name']} — {price}", callback_data=f"item:{it['id']}")])
    rows.append([InlineKeyboardButton(t(lang, "home"), callback_data="safe:home")])
    rows.append([
        InlineKeyboardButton("🇪🇪 ET", callback_data="lang:et"),
        InlineKeyboardButton("🇷🇺 RU", callback_data="lang:ru"),
        InlineKeyboardButton("🇬🇧 EN", callback_data="lang:en"),
    ])
    return InlineKeyboardMarkup(rows)


def kb_item_detail(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "back"), callback_data="safe:shop")],
        [InlineKeyboardButton(t(lang, "home"), callback_data="safe:home")],
        [
            InlineKeyboardButton("🇪🇪 ET", callback_data="lang:et"),
            InlineKeyboardButton("🇷🇺 RU", callback_data="lang:ru"),
            InlineKeyboardButton("🇬🇧 EN", callback_data="lang:en"),
        ],
    ])


def kb_buy_menu(lang: str, items: List[asyncpg.Record], cart: Dict[int, int], subtotal_cents: int) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for it in items:
        item_id = int(it["id"])
        qty = cart.get(item_id, 0)
        price = cents_to_eur_str(int(it["price_cents"]))
        label = f"{it['name']} — {price}"
        if qty > 0:
            label += f" (x{qty})"
        rows.append([InlineKeyboardButton(label, callback_data=f"buy:item:{item_id}")])

    rows.append([
        InlineKeyboardButton(t(lang, "buy_clear"), callback_data="buy:clear"),
        InlineKeyboardButton(t(lang, "buy_next"), callback_data="buy:next"),
    ])
    rows.append([InlineKeyboardButton(t(lang, "home"), callback_data="safe:home")])
    rows.append([
        InlineKeyboardButton("🇪🇪 ET", callback_data="lang:et"),
        InlineKeyboardButton("🇷🇺 RU", callback_data="lang:ru"),
        InlineKeyboardButton("🇬🇧 EN", callback_data="lang:en"),
    ])
    return InlineKeyboardMarkup(rows)


def kb_qty(lang: str, item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1", callback_data=f"buy:qty:{item_id}:1"),
            InlineKeyboardButton("2", callback_data=f"buy:qty:{item_id}:2"),
            InlineKeyboardButton("3", callback_data=f"buy:qty:{item_id}:3"),
        ],
        [
            InlineKeyboardButton("4", callback_data=f"buy:qty:{item_id}:4"),
            InlineKeyboardButton("5", callback_data=f"buy:qty:{item_id}:5"),
            InlineKeyboardButton("0", callback_data=f"buy:qty:{item_id}:0"),
        ],
        [InlineKeyboardButton(t(lang, "back"), callback_data="buy:back")],
    ])


def kb_delivery(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "buy_yes"), callback_data="buy:delivery:yes")],
        [InlineKeyboardButton(t(lang, "buy_no"), callback_data="buy:delivery:no")],
        [InlineKeyboardButton(t(lang, "back"), callback_data="buy:back")],
    ])


def kb_admin_decision(claim_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept", callback_data=f"adm:acc:{claim_id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"adm:dec:{claim_id}"),
    ]])


def kb_admin_remove(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Remove SAFE", callback_data=f"adm:rem:{user_id}")]])


def kb_admin_removeitem(items: List[asyncpg.Record]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for it in items:
        rows.append([InlineKeyboardButton(f"❌ {it['name']}", callback_data=f"adm:rmitem:{it['id']}")])
    return InlineKeyboardMarkup(rows) if rows else InlineKeyboardMarkup([])


def kb_admin_order(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Complete", callback_data=f"ord:complete:{order_id}"),
        InlineKeyboardButton("🚚 Delivery fee", callback_data=f"ord:fee:{order_id}"),
    ]])


# ========= DB HELPERS =========
async def upsert_user(pool: asyncpg.Pool, user) -> None:
    await pool.execute(
        """
        INSERT INTO users (user_id, first_name, last_name, username, updated_at)
        VALUES ($1, $2, $3, $4, now())
        ON CONFLICT (user_id) DO UPDATE
          SET first_name = EXCLUDED.first_name,
              last_name  = EXCLUDED.last_name,
              username   = EXCLUDED.username,
              updated_at = now()
        """,
        user.id, user.first_name, user.last_name, user.username
    )


async def ensure_user_exists(pool: asyncpg.Pool, user_id: int) -> None:
    await pool.execute(
        "INSERT INTO users (user_id, updated_at) VALUES ($1, now()) ON CONFLICT (user_id) DO NOTHING",
        user_id
    )


async def get_user(pool: asyncpg.Pool, user_id: int) -> Optional[asyncpg.Record]:
    return await pool.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)


async def set_language(pool: asyncpg.Pool, user_id: int, lang: str) -> None:
    await pool.execute("UPDATE users SET language=$1, updated_at=now() WHERE user_id=$2", lang, user_id)


async def set_state(pool: asyncpg.Pool, user_id: int, state: Optional[str]) -> None:
    await pool.execute("UPDATE users SET state=$1, updated_at=now() WHERE user_id=$2", state, user_id)


async def set_status(pool: asyncpg.Pool, user_id: int, status: str) -> None:
    await pool.execute("UPDATE users SET status=$1, updated_at=now() WHERE user_id=$2", status, user_id)


async def add_spent(pool: asyncpg.Pool, user_id: int, add_cents: int) -> None:
    await pool.execute(
        "UPDATE users SET spent_cents = spent_cents + $1, updated_at=now() WHERE user_id=$2",
        int(add_cents), user_id
    )


async def create_claim(pool: asyncpg.Pool, user_id: int, ref_username: str) -> int:
    row = await pool.fetchrow(
        "INSERT INTO claims (user_id, ref_username, status) VALUES ($1, $2, 'PENDING') RETURNING id",
        user_id, ref_username
    )
    return int(row["id"])


async def get_claim(pool: asyncpg.Pool, claim_id: int) -> Optional[asyncpg.Record]:
    return await pool.fetchrow("SELECT * FROM claims WHERE id=$1", claim_id)


async def decide_claim(pool: asyncpg.Pool, claim_id: int, decision: str) -> None:
    await pool.execute("UPDATE claims SET status=$1, decided_at=now() WHERE id=$2", decision, claim_id)


async def list_items(pool: asyncpg.Pool) -> List[asyncpg.Record]:
    return await pool.fetch("SELECT id, name, short_text, price_cents, photo_file_id FROM items ORDER BY id ASC")


async def get_item(pool: asyncpg.Pool, item_id: int) -> Optional[asyncpg.Record]:
    return await pool.fetchrow("SELECT id, name, short_text, price_cents, photo_file_id FROM items WHERE id=$1", item_id)


async def add_item(pool: asyncpg.Pool, name: str, short_text: str, price_cents: int, photo_file_id: str) -> None:
    await pool.execute(
        "INSERT INTO items (name, short_text, price_cents, photo_file_id) VALUES ($1, $2, $3, $4)",
        name, short_text, price_cents, photo_file_id
    )


async def remove_item(pool: asyncpg.Pool, item_id: int) -> None:
    await pool.execute("DELETE FROM items WHERE id=$1", item_id)


async def get_setting(pool: asyncpg.Pool, key: str, default: str) -> str:
    row = await pool.fetchrow("SELECT value FROM settings WHERE key=$1", key)
    return row["value"] if row else default


async def set_setting(pool: asyncpg.Pool, key: str, value: str) -> None:
    await pool.execute(
        "INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        key, value
    )


async def create_order(
    pool: asyncpg.Pool,
    user_id: int,
    cart: Dict[int, int],
    subtotal_cents: int,
    delivery: bool,
    address: Optional[str],
) -> int:
    delivery_fee_cents = 0
    total_cents = subtotal_cents + delivery_fee_cents
    row = await pool.fetchrow(
        """
        INSERT INTO orders (user_id, cart_json, subtotal_cents, delivery, address, delivery_fee_cents, total_cents, status)
        VALUES ($1, $2::jsonb, $3, $4, $5, $6, $7, 'NEW')
        RETURNING id
        """,
        user_id, json.dumps(cart), subtotal_cents, delivery, address, delivery_fee_cents, total_cents
    )
    return int(row["id"])


async def get_order(pool: asyncpg.Pool, order_id: int) -> Optional[asyncpg.Record]:
    return await pool.fetchrow("SELECT * FROM orders WHERE id=$1", order_id)


async def set_order_fee(pool: asyncpg.Pool, order_id: int, fee_cents: int) -> None:
    await pool.execute(
        """
        UPDATE orders
        SET delivery_fee_cents=$1,
            total_cents = subtotal_cents + $1
        WHERE id=$2
        """,
        int(fee_cents), int(order_id)
    )


async def mark_order_done(pool: asyncpg.Pool, order_id: int) -> None:
    await pool.execute("UPDATE orders SET status='DONE' WHERE id=$1", int(order_id))


# ========= LIFECYCLE =========
async def on_startup(app: Application) -> None:
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    app.bot_data["db_pool"] = pool
    async with pool.acquire() as conn:
        await conn.execute(CREATE_USERS_SQL)
        for q in ALTER_USERS_SQL:
            await conn.execute(q)
        await conn.execute(CREATE_CLAIMS_SQL)
        await conn.execute(CREATE_SETTINGS_SQL)
        await conn.execute(CREATE_ITEMS_SQL)
        for q in ALTER_ITEMS_SQL:
            await conn.execute(q)
        await conn.execute(CREATE_ORDERS_SQL)
        for q in ALTER_ORDERS_SQL:
            await conn.execute(q)

        cur = await conn.fetchrow("SELECT value FROM settings WHERE key='operator_online'")
        if not cur:
            await conn.execute("INSERT INTO settings (key, value) VALUES ('operator_online', 'true')")


async def on_shutdown(app: Application) -> None:
    pool = app.bot_data.get("db_pool")
    if pool:
        await pool.close()


# ========= UTIL =========
def is_admin(uid: Optional[int]) -> bool:
    return uid == ADMIN_ID_INT


def reset_additem(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("additem", None)


def get_cart(context: ContextTypes.DEFAULT_TYPE) -> Dict[int, int]:
    buy = context.user_data.get("buy")
    if not buy or not isinstance(buy, dict):
        buy = {"cart": {}, "subtotal_cents": 0}
        context.user_data["buy"] = buy
    cart = buy.get("cart")
    if not cart or not isinstance(cart, dict):
        buy["cart"] = {}
    cart2: Dict[int, int] = {}
    for k, v in buy["cart"].items():
        try:
            cart2[int(k)] = int(v)
        except Exception:
            pass
    buy["cart"] = cart2
    return cart2


async def recompute_subtotal(pool: asyncpg.Pool, cart: Dict[int, int]) -> int:
    if not cart:
        return 0
    item_ids = list(cart.keys())
    rows = await pool.fetch("SELECT id, price_cents FROM items WHERE id = ANY($1::int[])", item_ids)
    price_map = {int(r["id"]): int(r["price_cents"]) for r in rows}
    subtotal = 0
    for iid, qty in cart.items():
        subtotal += price_map.get(iid, 0) * max(qty, 0)
    return subtotal


# ========= HOME / SCREENS =========
async def send_home(chat_id: int, lang: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        with open(HOME_IMAGE_PATH, "rb") as f:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(f, filename="home.png"),
                caption=t(lang, "safe_welcome"),
                reply_markup=kb_safe_menu(lang),
                parse_mode="Markdown",
            )
    except FileNotFoundError:
        await context.bot.send_message(
            chat_id=chat_id,
            text=t(lang, "safe_welcome"),
            reply_markup=kb_safe_menu(lang),
            parse_mode="Markdown",
        )


# ========= ORDER -> ADMIN =========
async def notify_admin_order(pool: asyncpg.Pool, context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    order = await get_order(pool, order_id)
    if not order:
        return

    user_id = int(order["user_id"])
    u = await get_user(pool, user_id)
    uname = f"@{u['username']}" if u and u.get("username") else "(no username)"
    name = ((u.get("first_name") or "") + " " + (u.get("last_name") or "")).strip() if u else "(no name)"

    cart = order["cart_json"]
    if isinstance(cart, str):
        try:
            cart = json.loads(cart)
        except Exception:
            cart = {}

    items = await list_items(pool)
    item_map = {int(it["id"]): it for it in items}

    lines = []
    for k, v in (cart or {}).items():
        try:
            iid = int(k)
            qty = int(v)
        except Exception:
            continue
        it = item_map.get(iid)
        if not it:
            continue
        price = cents_to_eur_str(int(it["price_cents"]))
        lines.append(f"- {it['name']} x{qty} ({price})")

    delivery = bool(order["delivery"])
    addr = order["address"] or "-"
    subtotal = cents_to_eur_str(int(order["subtotal_cents"]))
    fee = cents_to_eur_str(int(order["delivery_fee_cents"]))
    total = cents_to_eur_str(int(order["total_cents"]))

    msg = (
        "NEW ORDER\n\n"
        f"Order ID: {order_id}\n"
        f"User ID: {user_id}\n"
        f"Name: {name}\n"
        f"Username: {uname}\n\n"
        "Items:\n" + ("\n".join(lines) if lines else "- (empty)") + "\n\n"
        f"Subtotal: {subtotal}\n"
        f"Delivery: {'YES' if delivery else 'NO'}\n"
        f"Address: {addr}\n"
        f"Delivery fee: {fee}\n"
        f"TOTAL: {total}\n"
    )

    sent = await context.bot.send_message(
        chat_id=ADMIN_ID_INT,
        text=msg,
        reply_markup=kb_admin_order(order_id),
    )

    # store last admin order message id for this order (optional)
    context.application.bot_data.setdefault("admin_order_msg", {})
    context.application.bot_data["admin_order_msg"][order_id] = sent.message_id


async def refresh_admin_order_message(pool: asyncpg.Pool, context: ContextTypes.DEFAULT_TYPE, order_id: int) -> None:
    order = await get_order(pool, order_id)
    if not order:
        return

    user_id = int(order["user_id"])
    u = await get_user(pool, user_id)
    uname = f"@{u['username']}" if u and u.get("username") else "(no username)"
    name = ((u.get("first_name") or "") + " " + (u.get("last_name") or "")).strip() if u else "(no name)"

    cart = order["cart_json"]
    if isinstance(cart, str):
        try:
            cart = json.loads(cart)
        except Exception:
            cart = {}

    items = await list_items(pool)
    item_map = {int(it["id"]): it for it in items}

    lines = []
    for k, v in (cart or {}).items():
        try:
            iid = int(k)
            qty = int(v)
        except Exception:
            continue
        it = item_map.get(iid)
        if not it:
            continue
        price = cents_to_eur_str(int(it["price_cents"]))
        lines.append(f"- {it['name']} x{qty} ({price})")

    delivery = bool(order["delivery"])
    addr = order["address"] or "-"
    subtotal = cents_to_eur_str(int(order["subtotal_cents"]))
    fee = cents_to_eur_str(int(order["delivery_fee_cents"]))
    total = cents_to_eur_str(int(order["total_cents"]))
    status = str(order["status"])

    msg = (
        "ORDER\n\n"
        f"Order ID: {order_id}\n"
        f"Status: {status}\n"
        f"User ID: {user_id}\n"
        f"Name: {name}\n"
        f"Username: {uname}\n\n"
        "Items:\n" + ("\n".join(lines) if lines else "- (empty)") + "\n\n"
        f"Subtotal: {subtotal}\n"
        f"Delivery: {'YES' if delivery else 'NO'}\n"
        f"Address: {addr}\n"
        f"Delivery fee: {fee}\n"
        f"TOTAL: {total}\n"
    )

    msg_map = context.application.bot_data.get("admin_order_msg", {})
    mid = msg_map.get(order_id)
    if not mid:
        return

    try:
        await context.bot.edit_message_text(
            chat_id=ADMIN_ID_INT,
            message_id=mid,
            text=msg,
            reply_markup=kb_admin_order(order_id),
        )
    except Exception:
        # if can't edit (too old etc), just send new
        sent = await context.bot.send_message(chat_id=ADMIN_ID_INT, text=msg, reply_markup=kb_admin_order(order_id))
        context.application.bot_data.setdefault("admin_order_msg", {})
        context.application.bot_data["admin_order_msg"][order_id] = sent.message_id


# ========= USER HANDLERS =========
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not update.message:
        return

    await upsert_user(pool, user)
    db_user = await get_user(pool, user.id)
    lang = (db_user["language"] if db_user and db_user["language"] else "et")
    status = (db_user["status"] if db_user and db_user["status"] else "NEW")

    if status == "SAFE":
        await send_home(chat.id, lang, context)
        return

    if status == "PENDING":
        await update.message.reply_text(t(lang, "already_pending"), reply_markup=kb_languages())
        return

    try:
        with open(CLAIM_IMAGE_PATH, "rb") as f:
            await context.bot.send_photo(
                chat_id=chat.id,
                photo=InputFile(f, filename="claim.png"),
                caption=t(lang, "welcome"),
                reply_markup=kb_languages_and_verify(lang),
            )
    except FileNotFoundError:
        await update.message.reply_text(t(lang, "welcome"), reply_markup=kb_languages_and_verify(lang))


async def on_lang_or_verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    user = update.effective_user
    if not user:
        return

    await upsert_user(pool, user)
    db_user = await get_user(pool, user.id)
    lang = (db_user["language"] if db_user and db_user["language"] else "et")
    status = (db_user["status"] if db_user and db_user["status"] else "NEW")
    state = db_user["state"] if db_user else None

    data = query.data or ""
    is_photo = bool(query.message and getattr(query.message, "photo", None))

    if data.startswith("lang:"):
        new_lang = data.split(":", 1)[1]
        if new_lang not in ("et", "ru", "en"):
            new_lang = "et"
        await set_language(pool, user.id, new_lang)

        db_user2 = await get_user(pool, user.id)
        status2 = (db_user2["status"] if db_user2 else "NEW")
        state2 = (db_user2["state"] if db_user2 else None)

        def is_buy_screen() -> bool:
            rm = getattr(query.message, "reply_markup", None)
            if not rm or not rm.inline_keyboard:
                return False
            for row in rm.inline_keyboard:
                for btn in row:
                    cd = getattr(btn, "callback_data", "") or ""
                    if cd.startswith("buy:"):
                        return True
            return False

        def is_shop_list_screen() -> bool:
            rm = getattr(query.message, "reply_markup", None)
            if not rm or not rm.inline_keyboard:
                return False
            for row in rm.inline_keyboard:
                for btn in row:
                    cd = getattr(btn, "callback_data", "") or ""
                    if cd.startswith("item:"):
                        return True
            return False

        if status2 == "SAFE":
            if is_buy_screen():
                items = await list_items(pool)
                cart = get_cart(context)
                subtotal = await recompute_subtotal(pool, cart)
                context.user_data.setdefault("buy", {})["subtotal_cents"] = subtotal
                text = f"{t(new_lang,'buy_intro')}\n\n{t(new_lang,'buy_cart')}: {cents_to_eur_str(subtotal)}"
                kb = kb_buy_menu(new_lang, items, cart, subtotal)
                if is_photo:
                    await query.edit_message_caption(caption=text, reply_markup=kb, parse_mode="Markdown")
                else:
                    await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
                return

            if is_shop_list_screen():
                items = await list_items(pool)
                if items:
                    text = t(new_lang, "shop_title")
                    kb = kb_shop_items(new_lang, items)
                else:
                    text = t(new_lang, "shop_empty")
                    kb = kb_safe_menu(new_lang)
                if is_photo:
                    await query.edit_message_caption(caption=text, reply_markup=kb, parse_mode="Markdown")
                else:
                    await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
                return

            if is_photo:
                await query.edit_message_caption(caption=t(new_lang, "safe_welcome"), reply_markup=kb_safe_menu(new_lang), parse_mode="Markdown")
            else:
                await query.edit_message_text(t(new_lang, "safe_welcome"), reply_markup=kb_safe_menu(new_lang), parse_mode="Markdown")
            return

        if status2 == "PENDING":
            if is_photo:
                await query.edit_message_caption(caption=t(new_lang, "already_pending"), reply_markup=kb_languages())
            else:
                await query.edit_message_text(t(new_lang, "already_pending"), reply_markup=kb_languages())
            return

        if state2 == "WAITING_REF":
            if is_photo:
                await query.edit_message_caption(caption=t(new_lang, "waiting_ref"), reply_markup=kb_languages())
            else:
                await query.edit_message_text(t(new_lang, "waiting_ref"), reply_markup=kb_languages())
            return

        if state2 == "BUY_ADDRESS":
            if is_photo:
                await query.edit_message_caption(caption=t(new_lang, "buy_send_address"), reply_markup=kb_languages())
            else:
                await query.edit_message_text(t(new_lang, "buy_send_address"), reply_markup=kb_languages())
            return

        if is_photo:
            await query.edit_message_caption(caption=t(new_lang, "welcome"), reply_markup=kb_languages_and_verify(new_lang))
        else:
            await query.edit_message_text(t(new_lang, "welcome"), reply_markup=kb_languages_and_verify(new_lang))
        return

    if data == "verify":
        if status == "PENDING":
            if is_photo:
                await query.edit_message_caption(caption=t(lang, "already_pending"), reply_markup=kb_languages())
            else:
                await query.edit_message_text(t(lang, "already_pending"), reply_markup=kb_languages())
            return

        if status == "SAFE":
            if query.message:
                await send_home(query.message.chat_id, lang, context)
            return

        await set_state(pool, user.id, "WAITING_REF")
        if is_photo:
            await query.edit_message_caption(caption=t(lang, "waiting_ref"), reply_markup=kb_languages())
        else:
            await query.edit_message_text(t(lang, "waiting_ref"), reply_markup=kb_languages())
        return


async def safe_menu_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    user = update.effective_user
    if not user:
        return

    db_user = await get_user(pool, user.id)
    lang = (db_user["language"] if db_user and db_user["language"] else "et")
    status = (db_user["status"] if db_user and db_user["status"] else "NEW")
    chat_id = query.message.chat_id

    if status != "SAFE":
        await query.edit_message_text(t(lang, "do_start"), reply_markup=kb_languages())
        return

    data = query.data or ""

    if data == "safe:help":
        await query.edit_message_text(t(lang, "help_text"), reply_markup=kb_safe_menu(lang))
        return

    if data == "safe:account":
        spent = int(db_user["spent_cents"] or 0)
        await query.edit_message_text(
            f"{t(lang, 'account_text')}\n\nUser ID: `{user.id}`\nSpent: `{cents_to_eur_str(spent)}`",
            reply_markup=kb_safe_menu(lang),
            parse_mode="Markdown",
        )
        return

    if data == "safe:home":
        await send_home(chat_id, lang, context)
        return

    if data == "safe:shop":
        items = await list_items(pool)
        if not items:
            try:
                with open(SHOP_IMAGE_PATH, "rb") as f:
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=InputFile(f, filename="shop.png"),
                        caption=t(lang, "shop_empty"),
                        reply_markup=kb_safe_menu(lang),
                        parse_mode="Markdown",
                    )
            except FileNotFoundError:
                await query.edit_message_text(t(lang, "shop_empty"), reply_markup=kb_safe_menu(lang))
            return

        try:
            with open(SHOP_IMAGE_PATH, "rb") as f:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=InputFile(f, filename="shop.png"),
                    caption=t(lang, "shop_title"),
                    reply_markup=kb_shop_items(lang, items),
                    parse_mode="Markdown",
                )
        except FileNotFoundError:
            await query.edit_message_text(t(lang, "shop_title"), reply_markup=kb_shop_items(lang, items), parse_mode="Markdown")
        return

    if data == "safe:buy":
        online = await get_setting(pool, "operator_online", "true")
        if online != "true":
            await query.edit_message_text(t(lang, "buy_offline"), reply_markup=kb_safe_menu(lang))
            return

        items = await list_items(pool)
        cart = get_cart(context)
        subtotal = await recompute_subtotal(pool, cart)
        context.user_data.setdefault("buy", {})["subtotal_cents"] = subtotal

        text = f"{t(lang,'buy_intro')}\n\n{t(lang,'buy_cart')}: {cents_to_eur_str(subtotal)}"
        kb = kb_buy_menu(lang, items, cart, subtotal)

        is_photo = bool(query.message and getattr(query.message, "photo", None))
        if is_photo:
            await query.edit_message_caption(caption=text, reply_markup=kb, parse_mode="Markdown")
        else:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        return


async def item_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    user = update.effective_user
    if not user:
        return

    db_user = await get_user(pool, user.id)
    lang = (db_user["language"] if db_user and db_user["language"] else "et")
    status = (db_user["status"] if db_user and db_user["status"] else "NEW")

    if status != "SAFE":
        await query.edit_message_text(t(lang, "do_start"), reply_markup=kb_languages())
        return

    try:
        item_id = int((query.data or "").split(":", 1)[1])
    except Exception:
        await query.edit_message_text(t(lang, "admin_bad"))
        return

    item = await get_item(context.application.bot_data["db_pool"], item_id)
    if not item:
        await query.edit_message_text(t(lang, "admin_bad"), reply_markup=kb_safe_menu(lang))
        return

    price = cents_to_eur_str(int(item["price_cents"]))
    caption = f"*{item['name']}*\n{price}\n\n{item['short_text']}"
    await context.bot.send_photo(
        chat_id=query.message.chat_id,
        photo=item["photo_file_id"],
        caption=caption,
        reply_markup=kb_item_detail(lang),
        parse_mode="Markdown",
    )


# ========= BUY CALLBACKS =========
async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    user = update.effective_user
    if not user:
        return

    db_user = await get_user(pool, user.id)
    lang = (db_user["language"] if db_user and db_user["language"] else "et")
    status = (db_user["status"] if db_user and db_user["status"] else "NEW")
    if status != "SAFE":
        await query.edit_message_text(t(lang, "do_start"), reply_markup=kb_languages())
        return

    online = await get_setting(pool, "operator_online", "true")
    if online != "true":
        await query.edit_message_text(t(lang, "buy_offline"), reply_markup=kb_safe_menu(lang))
        return

    data = query.data or ""
    parts = data.split(":")
    cart = get_cart(context)

    if data == "buy:clear":
        context.user_data["buy"] = {"cart": {}, "subtotal_cents": 0}
        items = await list_items(pool)
        text = f"{t(lang,'buy_intro')}\n\n{t(lang,'buy_cart')}: {cents_to_eur_str(0)}"
        kb = kb_buy_menu(lang, items, {}, 0)
        is_photo = bool(query.message and getattr(query.message, "photo", None))
        if is_photo:
            await query.edit_message_caption(caption=text, reply_markup=kb, parse_mode="Markdown")
        else:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "buy:back":
        items = await list_items(pool)
        subtotal = await recompute_subtotal(pool, cart)
        context.user_data.setdefault("buy", {})["subtotal_cents"] = subtotal
        text = f"{t(lang,'buy_intro')}\n\n{t(lang,'buy_cart')}: {cents_to_eur_str(subtotal)}"
        kb = kb_buy_menu(lang, items, cart, subtotal)
        is_photo = bool(query.message and getattr(query.message, "photo", None))
        if is_photo:
            await query.edit_message_caption(caption=text, reply_markup=kb, parse_mode="Markdown")
        else:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        return

    if len(parts) == 3 and parts[1] == "item":
        item_id = int(parts[2])
        item = await get_item(pool, item_id)
        if not item:
            return
        price = cents_to_eur_str(int(item["price_cents"]))
        text = f"*{item['name']}*\n{price}\n\n{t(lang,'buy_choose_qty')}"
        is_photo = bool(query.message and getattr(query.message, "photo", None))
        if is_photo:
            await query.edit_message_caption(caption=text, reply_markup=kb_qty(lang, item_id), parse_mode="Markdown")
        else:
            await query.edit_message_text(text, reply_markup=kb_qty(lang, item_id), parse_mode="Markdown")
        return

    if len(parts) == 4 and parts[1] == "qty":
        item_id = int(parts[2])
        qty = int(parts[3])
        if qty <= 0:
            cart.pop(item_id, None)
        else:
            cart[item_id] = qty
        context.user_data.setdefault("buy", {})["cart"] = cart

        items = await list_items(pool)
        subtotal = await recompute_subtotal(pool, cart)
        context.user_data.setdefault("buy", {})["subtotal_cents"] = subtotal

        text = f"{t(lang,'buy_intro')}\n\n{t(lang,'buy_cart')}: {cents_to_eur_str(subtotal)}"
        kb = kb_buy_menu(lang, items, cart, subtotal)
        is_photo = bool(query.message and getattr(query.message, "photo", None))
        if is_photo:
            await query.edit_message_caption(caption=text, reply_markup=kb, parse_mode="Markdown")
        else:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        return

    if data == "buy:next":
        subtotal = await recompute_subtotal(pool, cart)
        context.user_data.setdefault("buy", {})["subtotal_cents"] = subtotal
        if subtotal <= 0 or not cart:
            is_photo = bool(query.message and getattr(query.message, "photo", None))
            if is_photo:
                await query.edit_message_caption(caption=t(lang, "buy_need_items"), reply_markup=kb_delivery(lang))
            else:
                await query.edit_message_text(t(lang, "buy_need_items"), reply_markup=kb_delivery(lang))
            return

        text = t(lang, "buy_delivery_q") + f"\n\n{t(lang,'buy_cart')}: {cents_to_eur_str(subtotal)}"
        is_photo = bool(query.message and getattr(query.message, "photo", None))
        if is_photo:
            await query.edit_message_caption(caption=text, reply_markup=kb_delivery(lang), parse_mode="Markdown")
        else:
            await query.edit_message_text(text, reply_markup=kb_delivery(lang), parse_mode="Markdown")
        return

    if len(parts) == 3 and parts[1] == "delivery":
        choice = parts[2]
        subtotal = await recompute_subtotal(pool, cart)
        context.user_data.setdefault("buy", {})["subtotal_cents"] = subtotal

        if choice == "yes":
            context.user_data.setdefault("buy", {})["delivery"] = True
            await set_state(pool, user.id, "BUY_ADDRESS")
            await context.bot.send_message(chat_id=query.message.chat_id, text=t(lang, "buy_send_address"), reply_markup=kb_languages())
            return

        if choice == "no":
            context.user_data.setdefault("buy", {})["delivery"] = False
            order_id = await create_order(pool, user.id, cart, subtotal, False, None)
            context.user_data.pop("buy", None)
            await context.bot.send_message(chat_id=user.id, text=t(lang, "buy_order_sent"))
            await notify_admin_order(pool, context, order_id)
            return


# ========= ADMIN ORDER CALLBACKS =========
async def admin_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    if not update.effective_user or not is_admin(update.effective_user.id):
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 3:
        return

    action = parts[1]
    order_id = int(parts[2])

    order = await get_order(pool, order_id)
    if not order:
        await query.edit_message_text("Order not found.")
        return

    if action == "fee":
        # start fee input mode for admin
        context.user_data["fee_input"] = {"order_id": order_id}
        await context.bot.send_message(chat_id=ADMIN_ID_INT, text=t("et", "admin_fee_prompt"))
        return

    if action == "complete":
        # mark done + add spent to user + notify user
        await mark_order_done(pool, order_id)

        order2 = await get_order(pool, order_id)
        user_id = int(order2["user_id"])
        total_cents = int(order2["total_cents"])
        await add_spent(pool, user_id, total_cents)

        # notify user
        u = await get_user(pool, user_id)
        lang = (u["language"] if u and u.get("language") else "et")
        try:
            await context.bot.send_message(chat_id=user_id, text=f"{t(lang, 'order_completed_user')}\nTOTAL: {cents_to_eur_str(total_cents)}")
        except Exception:
            pass

        await refresh_admin_order_message(pool, context, order_id)
        return


# ========= TEXT HANDLER =========
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    await upsert_user(pool, user)
    db_user = await get_user(pool, user.id)
    lang = (db_user["language"] if db_user and db_user["language"] else "et")
    status = (db_user["status"] if db_user and db_user["status"] else "NEW")
    state = db_user["state"] if db_user else None
    text = update.message.text.strip()

    # --- ADMIN fee input ---
    if is_admin(user.id) and context.user_data.get("fee_input"):
        finfo = context.user_data.get("fee_input", {})
        order_id = int(finfo.get("order_id", 0))
        try:
            fee = float(text.replace(",", "."))
            if fee < 0:
                raise ValueError()
            fee_cents = eur_to_cents(fee)
        except Exception:
            await update.message.reply_text(t("et", "admin_fee_prompt"))
            return

        await set_order_fee(pool, order_id, fee_cents)
        context.user_data.pop("fee_input", None)

        await update.message.reply_text(f"✅ Delivery fee set: {cents_to_eur_str(fee_cents)}")
        await refresh_admin_order_message(pool, context, order_id)
        return

    # ---- ADMIN additem flow ----
    addflow: Optional[Dict[str, Any]] = context.user_data.get("additem")
    if addflow and is_admin(user.id):
        step = addflow.get("step")
        if step == "NAME":
            addflow["name"] = text
            addflow["step"] = "TEXT"
            context.user_data["additem"] = addflow
            await update.message.reply_text(t(lang, "admin_add_text"))
            return
        if step == "TEXT":
            addflow["short_text"] = text
            addflow["step"] = "PRICE"
            context.user_data["additem"] = addflow
            await update.message.reply_text(t(lang, "admin_add_price"))
            return
        if step == "PRICE":
            try:
                price = float(text.replace(",", "."))
                if price < 0:
                    raise ValueError()
                addflow["price_cents"] = eur_to_cents(price)
            except Exception:
                await update.message.reply_text(t(lang, "admin_add_price"))
                return
            addflow["step"] = "PHOTO"
            context.user_data["additem"] = addflow
            await update.message.reply_text(t(lang, "admin_add_photo"))
            return

    # ---- BUY ADDRESS ----
    if state == "BUY_ADDRESS" and status == "SAFE":
        buy = context.user_data.get("buy") or {}
        cart = buy.get("cart") if isinstance(buy, dict) else {}
        if not isinstance(cart, dict) or not cart:
            await set_state(pool, user.id, None)
            context.user_data.pop("buy", None)
            await update.message.reply_text(t(lang, "buy_need_items"))
            return

        cart2: Dict[int, int] = {}
        for k, v in cart.items():
            try:
                cart2[int(k)] = int(v)
            except Exception:
                pass

        subtotal = await recompute_subtotal(pool, cart2)
        address = text

        order_id = await create_order(pool, user.id, cart2, subtotal, True, address)
        await set_state(pool, user.id, None)
        context.user_data.pop("buy", None)

        await update.message.reply_text(t(lang, "buy_order_sent"))
        await notify_admin_order(pool, context, order_id)
        return

    # ---- CLAIM referral ----
    if status == "PENDING":
        await update.message.reply_text(t(lang, "already_pending"), reply_markup=kb_languages())
        return

    if state == "WAITING_REF":
        if not text.startswith("@") or len(text) < 2 or " " in text:
            await update.message.reply_text(t(lang, "invalid_ref"), reply_markup=kb_languages())
            return

        ref_username = text
        claim_id = await create_claim(pool, user.id, ref_username)
        await set_state(pool, user.id, None)
        await set_status(pool, user.id, "PENDING")

        await update.message.reply_text(t(lang, "wait_admin"), reply_markup=kb_languages())

        now_utc = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        uname = f"@{user.username}" if user.username else "(no username)"

        admin_text = (
            "NEW CLAIM\n\n"
            f"User ID: {user.id}\n"
            f"Name: {full_name or '(no name)'}\n"
            f"Username: {uname}\n"
            f"Referral: {ref_username}\n"
            f"Date: {now_utc}\n"
            f"Claim ID: {claim_id}\n"
        )

        await context.bot.send_message(
            chat_id=ADMIN_ID_INT,
            text=admin_text,
            reply_markup=kb_admin_decision(claim_id),
        )
        return

    if status == "SAFE":
        await send_home(chat.id, lang, context)
        return

    await update.message.reply_text(t(lang, "do_start"), reply_markup=kb_languages())


# ========= PHOTO HANDLER (admin additem photo step) =========
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.photo:
        return

    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    db_user = await get_user(pool, user.id)
    lang = (db_user["language"] if db_user and db_user["language"] else "et")

    addflow: Optional[Dict[str, Any]] = context.user_data.get("additem")
    if not addflow or addflow.get("step") != "PHOTO":
        return

    name = (addflow.get("name") or "").strip()
    short_text = (addflow.get("short_text") or "").strip()
    price_cents = int(addflow.get("price_cents") or 0)

    if not name or not short_text:
        reset_additem(context)
        await update.message.reply_text(t(lang, "admin_bad"))
        return

    file_id = update.message.photo[-1].file_id

    try:
        await add_item(pool, name=name, short_text=short_text, price_cents=price_cents, photo_file_id=file_id)
    except Exception:
        reset_additem(context)
        await update.message.reply_text(t(lang, "admin_bad"))
        return

    reset_additem(context)
    await update.message.reply_text(t(lang, "admin_add_done"))


# ========= ADMIN: CLAIM DECISIONS =========
async def admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    if not update.effective_user or update.effective_user.id != ADMIN_ID_INT:
        await query.edit_message_text("Not allowed.")
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 3:
        await query.edit_message_text("Bad callback.")
        return

    action = parts[1]
    claim_id = int(parts[2])

    claim = await get_claim(pool, claim_id)
    if not claim:
        await query.edit_message_text("Claim not found.")
        return

    target_user_id = int(claim["user_id"])
    if claim["status"] in ("ACCEPTED", "DECLINED"):
        await query.edit_message_text(f"Already decided: {claim['status']}")
        return

    target_user = await get_user(pool, target_user_id)
    target_lang = (target_user["language"] if target_user and target_user["language"] else "et")
    base_text = query.message.text or ""

    if action == "acc":
        await decide_claim(pool, claim_id, "ACCEPTED")
        await set_status(pool, target_user_id, "SAFE")
        await set_state(pool, target_user_id, None)

        await context.bot.send_message(chat_id=target_user_id, text=t(target_lang, "accepted"))
        await query.edit_message_text(base_text + "\n✅ ACCEPTED", reply_markup=kb_admin_remove(target_user_id))
        return

    if action == "dec":
        await decide_claim(pool, claim_id, "DECLINED")
        await set_status(pool, target_user_id, "DECLINED")
        await set_state(pool, target_user_id, None)

        await context.bot.send_message(chat_id=target_user_id, text=t(target_lang, "declined"))
        await query.edit_message_text(base_text + "\n❌ DECLINED")
        return


async def admin_remove_safe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    if not update.effective_user or update.effective_user.id != ADMIN_ID_INT:
        await query.edit_message_text("Not allowed.")
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 3:
        await query.edit_message_text("Bad callback.")
        return

    user_id = int(parts[2])
    await ensure_user_exists(pool, user_id)
    await set_status(pool, user_id, "NEW")
    await set_state(pool, user_id, None)

    target_user = await get_user(pool, user_id)
    target_lang = (target_user["language"] if target_user and target_user["language"] else "et")

    try:
        await context.bot.send_message(chat_id=user_id, text=t(target_lang, "removed_safe"))
    except Exception:
        pass

    await query.edit_message_text((query.message.text or "") + "\n✅ Removed from SAFE.")


# ========= ADMIN COMMANDS =========
async def admin_add_safe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not is_admin(user.id) or not update.message:
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    args = context.args or []
    if len(args) != 1 or not args[0].isdigit():
        await update.message.reply_text("Usage: /add <user_id>")
        return

    user_id = int(args[0])
    await ensure_user_exists(pool, user_id)
    await set_status(pool, user_id, "SAFE")
    await set_state(pool, user_id, None)

    target_user = await get_user(pool, user_id)
    target_lang = (target_user["language"] if target_user and target_user["language"] else "et")
    try:
        await context.bot.send_message(chat_id=user_id, text=t(target_lang, "added_safe"))
    except Exception:
        pass

    await update.message.reply_text("✅ Added to SAFE list.")


async def admin_remove_safe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not is_admin(user.id) or not update.message:
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    args = context.args or []
    if len(args) != 1 or not args[0].isdigit():
        await update.message.reply_text("Usage: /remove <user_id>")
        return

    user_id = int(args[0])
    await ensure_user_exists(pool, user_id)
    await set_status(pool, user_id, "NEW")
    await set_state(pool, user_id, None)

    target_user = await get_user(pool, user_id)
    target_lang = (target_user["language"] if target_user and target_user["language"] else "et")
    try:
        await context.bot.send_message(chat_id=user_id, text=t(target_lang, "removed_safe"))
    except Exception:
        pass

    await update.message.reply_text("✅ Removed from SAFE list.")


async def admin_online(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not is_admin(update.effective_user.id) or not update.message:
        return
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    await set_setting(pool, "operator_online", "true")
    await update.message.reply_text("✅ ONLINE")


async def admin_offline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not is_admin(update.effective_user.id) or not update.message:
        return
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    await set_setting(pool, "operator_online", "false")
    await update.message.reply_text("❌ OFFLINE")


async def admin_loc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not is_admin(update.effective_user.id) or not update.message:
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    args = context.args or []
    if len(args) < 2 or not args[0].isdigit():
        await update.message.reply_text("Usage: /loc <order_id> <asukoht ja kellaaeg>")
        return

    order_id = int(args[0])
    info = " ".join(args[1:]).strip()
    order = await get_order(pool, order_id)
    if not order:
        await update.message.reply_text("Order not found.")
        return

    user_id = int(order["user_id"])
    u = await get_user(pool, user_id)
    lang = (u["language"] if u and u.get("language") else "et")

    await context.bot.send_message(chat_id=user_id, text=f"{t(lang,'order_pickup_msg')}\n{info}")
    await update.message.reply_text("✅ Sent.")


async def admin_additem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not is_admin(user.id) or not update.message:
        return
    context.user_data["additem"] = {"step": "NAME"}
    await update.message.reply_text(t("et", "admin_add_name"))


async def admin_removeitem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not is_admin(user.id) or not update.message:
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    db_user = await get_user(pool, user.id)
    lang = (db_user["language"] if db_user and db_user["language"] else "et")

    items = await list_items(pool)
    if not items:
        await update.message.reply_text(t(lang, "admin_remove_empty"))
        return

    await update.message.reply_text(t(lang, "admin_remove_pick"), reply_markup=kb_admin_removeitem(items))


async def admin_removeitem_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    if not update.effective_user or not is_admin(update.effective_user.id):
        await query.edit_message_text("Not allowed.")
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    try:
        item_id = int((query.data or "").split(":", 2)[2])
    except Exception:
        await query.edit_message_text("Bad callback.")
        return

    await remove_item(pool, item_id)
    await query.edit_message_text("✅ Removed.")


# ========= MAIN =========
def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    # user
    app.add_handler(CommandHandler("start", start_cmd))

    # admin commands
    app.add_handler(CommandHandler("add", admin_add_safe))
    app.add_handler(CommandHandler("remove", admin_remove_safe))
    app.add_handler(CommandHandler("online", admin_online))
    app.add_handler(CommandHandler("offline", admin_offline))
    app.add_handler(CommandHandler("loc", admin_loc))
    app.add_handler(CommandHandler("additem", admin_additem))
    app.add_handler(CommandHandler("removeitem", admin_removeitem))

    # callbacks
    app.add_handler(CallbackQueryHandler(on_lang_or_verify, pattern=r"^(lang:(et|ru|en)|verify)$"))
    app.add_handler(CallbackQueryHandler(safe_menu_click, pattern=r"^safe:(shop|buy|help|account|home)$"))
    app.add_handler(CallbackQueryHandler(item_open, pattern=r"^item:\d+$"))
    app.add_handler(CallbackQueryHandler(buy_callback, pattern=r"^buy:"))

    # claim callbacks
    app.add_handler(CallbackQueryHandler(admin_decision, pattern=r"^adm:(acc|dec):\d+$"))
    app.add_handler(CallbackQueryHandler(admin_remove_safe_callback, pattern=r"^adm:rem:\d+$"))

    # shop remove callback
    app.add_handler(CallbackQueryHandler(admin_removeitem_callback, pattern=r"^adm:rmitem:\d+$"))

    # order callbacks (complete / fee)
    app.add_handler(CallbackQueryHandler(admin_order_callback, pattern=r"^ord:(complete|fee):\d+$"))

    # messages
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
