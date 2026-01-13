import os
import datetime
import html
import asyncpg
from typing import Optional, Dict, List

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile,
    InputMediaPhoto,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================== ENV ==================
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


# ================== MONEY ==================
def eur_to_cents(x: float) -> int:
    return int(round(x * 100))


def cents_to_eur_str(c: int) -> str:
    return f"{c/100:.2f}€"


# ================== DB ==================
CREATE_USERS_SQL = """
CREATE TABLE IF NOT EXISTS users (
  user_id BIGINT PRIMARY KEY,
  first_name TEXT,
  last_name TEXT,
  username TEXT,
  language TEXT DEFAULT 'et',
  status TEXT DEFAULT 'NEW',          -- NEW/PENDING/SAFE/DECLINED
  state TEXT DEFAULT NULL,            -- NULL/WAITING_REF
  spent_cents BIGINT NOT NULL DEFAULT 0,
  discount_cents BIGINT NOT NULL DEFAULT 0,
  banned BOOLEAN NOT NULL DEFAULT false,
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

CREATE_SETTINGS_SQL = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# Requests = generic "requests"
CREATE_REQUESTS_SQL = """
CREATE TABLE IF NOT EXISTS requests (
  id SERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(user_id),
  title TEXT NOT NULL,
  details TEXT NOT NULL,
  fee_cents INT NOT NULL DEFAULT 0,
  total_cents INT NOT NULL DEFAULT 0,
  use_discount BOOLEAN NOT NULL DEFAULT false,
  discount_used_cents INT NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'NEW',     -- NEW/IN_REVIEW/DONE/CANCELLED
  admin_message_id BIGINT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
"""

# Stock items = products catalog
CREATE_STOCK_SQL = """
CREATE TABLE IF NOT EXISTS stock_items (
  id SERIAL PRIMARY KEY,
  title TEXT NOT NULL,
  price_cents BIGINT NOT NULL DEFAULT 0,
  description TEXT NOT NULL DEFAULT '',
  photo_file_id TEXT NULL,
  active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_stock_active ON stock_items(active);
"""

ALTER_USERS_SQL = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS language TEXT DEFAULT 'et';",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'NEW';",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS state TEXT DEFAULT NULL;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name TEXT;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS spent_cents BIGINT NOT NULL DEFAULT 0;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS discount_cents BIGINT NOT NULL DEFAULT 0;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS banned BOOLEAN NOT NULL DEFAULT false;",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();",
]

ALTER_REQUESTS_SQL = [
    "ALTER TABLE requests ADD COLUMN IF NOT EXISTS fee_cents INT NOT NULL DEFAULT 0;",
    "ALTER TABLE requests ADD COLUMN IF NOT EXISTS total_cents INT NOT NULL DEFAULT 0;",
    "ALTER TABLE requests ADD COLUMN IF NOT EXISTS use_discount BOOLEAN NOT NULL DEFAULT false;",
    "ALTER TABLE requests ADD COLUMN IF NOT EXISTS discount_used_cents INT NOT NULL DEFAULT 0;",
    "ALTER TABLE requests ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'NEW';",
    "ALTER TABLE requests ADD COLUMN IF NOT EXISTS admin_message_id BIGINT NULL;",
]


# ================== TEXTS ==================
TEXTS: Dict[str, Dict[str, str]] = {
    "et": {
        "welcome": "Tere! Vajuta Verify",
        "verify": "✅ Verify",
        "waiting_ref": "Kirjuta oma sõbra @username, kelle käest sa said boti (näiteks: @mart).",
        "invalid_ref": "❌ Palun kirjuta korrektne @username (peab algama @-ga).",
        "wait_admin": "⏳ Aitäh! Oota admini vastust.",
        "already_pending": "⏳ Su verifitseerimine on ootel.",
        "accepted": "✅ Admin kinnitas su verifitseerimise. Sa oled nüüd SAFE. Tee /start",
        "declined": "❌ Admin lükkas su verifitseerimise tagasi.",
        "removed_safe": "❌ Admin eemaldas sind SAFE listist. Tee /start ja verifitseeri uuesti.",
        "added_safe": "✅ Admin lisas sind SAFE listi. Tee /start",
        "do_start": "Tee /start",
        "banned": "⛔ Sul puudub ligipääs botile.",
        "safe_welcome": (
            "<b>Private Hub</b>\n\n"
            "Siin saad esitada päringuid ja hallata oma infot.\n"
            "Vali alt menüüst üks valik."
        ),
        "help_text": "Help: kirjuta adminile.",
        "account_text": "<b>Account</b>",
        "requests_title": "<b>Requests</b>\nVali päring.",
        "requests_empty": "Sul pole aktiivseid päringuid.",
        "request_new": "➕ New request",
        "request_enter_title": "Kirjuta päringu pealkiri (1 rida).",
        "request_enter_details": "Kirjuta päringu detailid.",
        "request_sent": "✅ Päring saadetud. Admin vaatab üle.",
        "request_cancel": "❌ Cancel",
        "request_cancel_confirm": "✅ Confirm cancel",
        "request_cancelled_user": "✅ Päring cancelled.",
        "request_cancelled_admin": "❌ USER CANCELLED",
        "admin_fee_prompt": "Kirjuta fee EUR (näiteks: 5 või 7.50):",
        "search_usage": "Usage: /search @username",
        "search_not_found": "❌ User not found in database.",
        "back": "⬅️ Tagasi",
        "home": "⬅️ Home",
        "op_online": "🟢 Operator: ONLINE",
        "op_offline": "🔴 Operator: OFFLINE",
        "op_offline_block": "🔴 Operator on hetkel OFFLINE. Uus päring on ajutiselt kinni.",
        "admin_online_set": "✅ Operator ONLINE.",
        "admin_offline_set": "✅ Operator OFFLINE.",
        "menu_requests": "Requests",
        "menu_account": "Account",
        "menu_help": "Help",
        "menu_list": "📄 List",
        "menu_shop": "Shop",
        "shop_title": "<b>Shop</b>\nVali toode.",
        "shop_empty": "Shop on tühi.",
        "shop_prev": "⬅️ Prev",
        "shop_next": "Next ➡️",
        "stock_usage": "Usage: /stock add | /stock remove <id> | /stock list",
        "stock_title": "Sisesta pealkiri:",
        "stock_price": "Sisesta hind EUR (nt 19.99):",
        "stock_desc": "Sisesta kirjeldus:",
        "stock_photo": "Saada pilt (photo). Või kirjuta /skip, et jätta pilt vahele.",
        "stock_invalid_price": "❌ Vale hind. Näide: 19.99",
        "stock_added": "✅ Lisatud (ID: {id}).",
        "stock_removed": "✅ Eemaldatud.",
        "stock_not_found": "❌ Ei leitud.",
        "discount_use_prompt": "Sul on discount: {amount}. Kas tahad seda kasutada?",
        "discount_yes": "✅ Kasuta discounti",
        "discount_no": "❌ Ära kasuta",
        "discount_added": "🎁 Su kontole lisandus uus discount: {amount}.\nKui tahad seda kasutada, vali päringu ajal \"Kasuta discounti\".",
        "discount_applied": "🎁 Discount kasutatud: {amount}.\nUus TOTAL: {total}",
    },
    "ru": {
        "welcome": "Привет! Нажми Verify",
        "verify": "✅ Verify",
        "waiting_ref": "Напиши @username друга (например: @mart).",
        "invalid_ref": "❌ Напиши корректный @username (должен начинаться с @).",
        "wait_admin": "⏳ Спасибо! Дождись решения админа.",
        "already_pending": "⏳ Проверка уже в ожидании.",
        "accepted": "✅ Админ подтвердил. Ты SAFE. Напиши /start",
        "declined": "❌ Админ отклонил проверку.",
        "removed_safe": "❌ Админ удалил тебя из SAFE. Сделай /start и проверься снова.",
        "added_safe": "✅ Админ добавил тебя в SAFE. Напиши /start",
        "do_start": "Напиши /start",
        "banned": "⛔ У тебя нет доступа к боту.",
        "safe_welcome": (
            "<b>Private Hub</b>\n\n"
            "Здесь ты можешь отправлять запросы и управлять информацией.\n"
            "Выбери пункт меню ниже."
        ),
        "help_text": "Help: напиши админу.",
        "account_text": "<b>Account</b>",
        "requests_title": "<b>Requests</b>\nВыбери запрос.",
        "requests_empty": "У тебя нет активных запросов.",
        "request_new": "➕ New request",
        "request_enter_title": "Отправь заголовок запроса (1 строка).",
        "request_enter_details": "Отправь детали запроса.",
        "request_sent": "✅ Запрос отправлен. Админ посмотрит.",
        "request_cancel": "❌ Cancel",
        "request_cancel_confirm": "✅ Confirm cancel",
        "request_cancelled_user": "✅ Запрос отменён.",
        "request_cancelled_admin": "❌ USER CANCELLED",
        "admin_fee_prompt": "Отправь fee EUR (пример: 5 или 7.50):",
        "search_usage": "Usage: /search @username",
        "search_not_found": "❌ User not found in database.",
        "back": "⬅️ Назад",
        "home": "⬅️ Home",
        "op_online": "🟢 Operator: ONLINE",
        "op_offline": "🔴 Operator: OFFLINE",
        "op_offline_block": "🔴 Operator сейчас OFFLINE. Новый запрос временно закрыт.",
        "admin_online_set": "✅ Operator ONLINE.",
        "admin_offline_set": "✅ Operator OFFLINE.",
        "menu_requests": "Requests",
        "menu_account": "Account",
        "menu_help": "Help",
        "menu_list": "📄 List",
        "menu_shop": "Shop",
        "shop_title": "<b>Shop</b>\nВыбери товар.",
        "shop_empty": "Shop пуст.",
        "shop_prev": "⬅️ Prev",
        "shop_next": "Next ➡️",
        "stock_usage": "Usage: /stock add | /stock remove <id> | /stock list",
        "stock_title": "Отправь заголовок:",
        "stock_price": "Отправь цену EUR (например 19.99):",
        "stock_desc": "Отправь описание:",
        "stock_photo": "Отправь фото. Или напиши /skip чтобы пропустить.",
        "stock_invalid_price": "❌ Неверная цена. Пример: 19.99",
        "stock_added": "✅ Добавлено (ID: {id}).",
        "stock_removed": "✅ Удалено.",
        "stock_not_found": "❌ Не найдено.",
        "discount_use_prompt": "У тебя есть discount: {amount}. Использовать его?",
        "discount_yes": "✅ Использовать discount",
        "discount_no": "❌ Не использовать",
        "discount_added": "🎁 На твой аккаунт добавлен новый discount: {amount}.\nЧтобы использовать его, во время запроса выбери \"Использовать discount\".",
        "discount_applied": "🎁 Discount применён: {amount}.\nНовый TOTAL: {total}",
    },
    "en": {
        "welcome": "Hi! Press Verify",
        "verify": "✅ Verify",
        "waiting_ref": "Send your friend's @username (example: @mart).",
        "invalid_ref": "❌ Please send a valid @username (must start with @).",
        "wait_admin": "⏳ Thanks. Wait for admin approval.",
        "already_pending": "⏳ Verification is pending.",
        "accepted": "✅ Admin approved you. You are SAFE now. Send /start",
        "declined": "❌ Admin declined your verification.",
        "removed_safe": "❌ Admin removed you from SAFE. Do /start and verify again.",
        "added_safe": "✅ Admin added you to SAFE. Send /start",
        "do_start": "Send /start",
        "banned": "⛔ You do not have access to this bot.",
        "safe_welcome": (
            "<b>Private Hub</b>\n\n"
            "Submit requests and manage your info.\n"
            "Choose an option below."
        ),
        "help_text": "Help: contact admin.",
        "account_text": "<b>Account</b>",
        "requests_title": "<b>Requests</b>\nPick a request.",
        "requests_empty": "You have no active requests.",
        "request_new": "➕ New request",
        "request_enter_title": "Send request title (1 line).",
        "request_enter_details": "Send request details.",
        "request_sent": "✅ Request sent. Admin will review.",
        "request_cancel": "❌ Cancel",
        "request_cancel_confirm": "✅ Confirm cancel",
        "request_cancelled_user": "✅ Request cancelled.",
        "request_cancelled_admin": "❌ USER CANCELLED",
        "admin_fee_prompt": "Send fee EUR (example: 5 or 7.50):",
        "search_usage": "Usage: /search @username",
        "search_not_found": "❌ User not found in database.",
        "back": "⬅️ Back",
        "home": "⬅️ Home",
        "op_online": "🟢 Operator: ONLINE",
        "op_offline": "🔴 Operator: OFFLINE",
        "op_offline_block": "🔴 Operator is OFFLINE. New request is temporarily disabled.",
        "admin_online_set": "✅ Operator ONLINE.",
        "admin_offline_set": "✅ Operator OFFLINE.",
        "menu_requests": "Requests",
        "menu_account": "Account",
        "menu_help": "Help",
        "menu_list": "📄 List",
        "menu_shop": "Shop",
        "shop_title": "<b>Shop</b>\nPick an item.",
        "shop_empty": "Shop is empty.",
        "shop_prev": "⬅️ Prev",
        "shop_next": "Next ➡️",
        "stock_usage": "Usage: /stock add | /stock remove <id> | /stock list",
        "stock_title": "Send title:",
        "stock_price": "Send price EUR (e.g. 19.99):",
        "stock_desc": "Send description:",
        "stock_photo": "Send a photo. Or type /skip to skip photo.",
        "stock_invalid_price": "❌ Invalid price. Example: 19.99",
        "stock_added": "✅ Added (ID: {id}).",
        "stock_removed": "✅ Removed.",
        "stock_not_found": "❌ Not found.",
        "discount_use_prompt": "You have a discount: {amount}. Use it?",
        "discount_yes": "✅ Use discount",
        "discount_no": "❌ Don't use",
        "discount_added": "🎁 A new discount was added to your account: {amount}.\nTo use it, choose \"Use discount\" during request.",
        "discount_applied": "🎁 Discount applied: {amount}.\nNew TOTAL: {total}",
    },
}


def t(lang: str, key: str) -> str:
    if lang not in TEXTS:
        lang = "et"
    return TEXTS[lang].get(key, TEXTS["et"].get(key, key))


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


# ================== UI EDIT HELPER ==================
async def edit_ui(query, text: str, reply_markup=None, parse_mode: str = "HTML") -> None:
    """Edit the same message (caption if photo, otherwise text)."""
    msg = query.message
    is_photo = bool(msg and getattr(msg, "photo", None))
    if is_photo:
        await query.edit_message_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    else:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)


# ================== ACCESS CONTROL ==================
async def is_banned(pool: asyncpg.Pool, user_id: int) -> bool:
    row = await pool.fetchrow("SELECT banned FROM users WHERE user_id=$1", int(user_id))
    return bool(row and row["banned"])


async def set_banned(pool: asyncpg.Pool, user_id: int, banned: bool) -> None:
    await ensure_user_exists(pool, int(user_id))
    await pool.execute("UPDATE users SET banned=$1, updated_at=now() WHERE user_id=$2", bool(banned), int(user_id))


async def resolve_user_id(pool: asyncpg.Pool, token: str) -> Optional[int]:
    s = (token or "").strip()
    if not s:
        return None
    if s.startswith("@"):
        u = await get_user_by_username(pool, s)
        return int(u["user_id"]) if u else None
    if s.isdigit():
        return int(s)
    return None


async def count_accepted_referrals(pool: asyncpg.Pool, ref_username: str) -> int:
    ref = (ref_username or "").strip()
    if not ref:
        return 0
    if not ref.startswith("@"):
        ref = "@" + ref
    row = await pool.fetchrow(
        "SELECT COUNT(*) AS c FROM claims WHERE status='ACCEPTED' AND lower(ref_username)=lower($1)",
        ref,
    )
    return int(row["c"] if row else 0)


# ================== KEYBOARDS ==================
def kb_languages() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🇪🇪 ET", callback_data="lang:et"),
                InlineKeyboardButton("🇷🇺 RU", callback_data="lang:ru"),
                InlineKeyboardButton("🇬🇧 EN", callback_data="lang:en"),
            ]
        ]
    )


def kb_languages_and_verify(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🇪🇪 ET", callback_data="lang:et"),
                InlineKeyboardButton("🇷🇺 RU", callback_data="lang:ru"),
                InlineKeyboardButton("🇬🇧 EN", callback_data="lang:en"),
            ],
            [InlineKeyboardButton(t(lang, "verify"), callback_data="verify")],
        ]
    )


def kb_safe_menu(lang: str) -> InlineKeyboardMarkup:
    # Same layout as before; texts translate
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(t(lang, "menu_requests"), callback_data="safe:requests"),
                InlineKeyboardButton(t(lang, "menu_account"), callback_data="safe:account"),
            ],
            [InlineKeyboardButton(t(lang, "menu_shop"), callback_data="safe:shop")],
            [InlineKeyboardButton(t(lang, "menu_help"), callback_data="safe:help")],
            [
                InlineKeyboardButton("🇪🇪 ET", callback_data="lang:et"),
                InlineKeyboardButton("🇷🇺 RU", callback_data="lang:ru"),
                InlineKeyboardButton("🇬🇧 EN", callback_data="lang:en"),
            ],
        ]
    )


def kb_requests_home(lang: str, has_any: bool, operator_online: bool) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    if operator_online:
        rows.append([InlineKeyboardButton(t(lang, "request_new"), callback_data="req:new")])
    if has_any:
        rows.append([InlineKeyboardButton(t(lang, "menu_list"), callback_data="req:list")])
    rows.append([InlineKeyboardButton(t(lang, "home"), callback_data="safe:home")])
    return InlineKeyboardMarkup(rows)


def kb_requests_list(lang: str, reqs: List[asyncpg.Record]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for r in reqs:
        rid = int(r["id"])
        st = str(r["status"])
        total = cents_to_eur_str(int(r["total_cents"]))
        rows.append([InlineKeyboardButton(f"#{rid} — {st} — {total}", callback_data=f"req:view:{rid}")])
    rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="safe:requests")])
    rows.append([InlineKeyboardButton(t(lang, "home"), callback_data="safe:home")])
    return InlineKeyboardMarkup(rows)


def kb_request_detail(lang: str, request_id: int, can_cancel: bool) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    if can_cancel:
        rows.append([InlineKeyboardButton(t(lang, "request_cancel"), callback_data=f"req:cancel:{request_id}")])
    rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="req:list")])
    rows.append([InlineKeyboardButton(t(lang, "home"), callback_data="safe:home")])
    return InlineKeyboardMarkup(rows)


def kb_request_cancel_confirm(lang: str, request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(t(lang, "request_cancel_confirm"), callback_data=f"req:confirm:{request_id}")],
            [InlineKeyboardButton(t(lang, "back"), callback_data=f"req:view:{request_id}")],
        ]
    )


def kb_discount_choice(lang: str, balance_cents: int) -> InlineKeyboardMarkup:
    bal = cents_to_eur_str(int(balance_cents))
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"{t(lang, 'discount_yes')} ({bal})", callback_data="disc:yes")],
            [InlineKeyboardButton(t(lang, "discount_no"), callback_data="disc:no")],
            [InlineKeyboardButton(t(lang, "back"), callback_data="safe:requests")],
            [InlineKeyboardButton(t(lang, "home"), callback_data="safe:home")],
        ]
    )


def kb_admin_claim_decision(claim_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Accept", callback_data=f"adm:acc:{claim_id}"),
                InlineKeyboardButton("❌ Decline", callback_data=f"adm:dec:{claim_id}"),
            ],
            [InlineKeyboardButton("🚫 Ban user", callback_data=f"adm:ban:{claim_id}")],
        ]
    )


def kb_admin_remove(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("❌ Remove SAFE", callback_data=f"adm:rem:{user_id}")],
            [InlineKeyboardButton("🚫 Ban user", callback_data=f"adm:banuid:{user_id}")],
        ]
    )


def kb_admin_request(request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👀 Review", callback_data=f"r:review:{request_id}"),
                InlineKeyboardButton("💰 Fee", callback_data=f"r:fee:{request_id}"),
                InlineKeyboardButton("✅ Complete", callback_data=f"r:complete:{request_id}"),
            ]
        ]
    )


# ================== DB HELPERS ==================
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
        user.id,
        user.first_name,
        user.last_name,
        user.username,
    )


async def ensure_user_exists(pool: asyncpg.Pool, user_id: int) -> None:
    await pool.execute(
        "INSERT INTO users (user_id, updated_at) VALUES ($1, now()) ON CONFLICT (user_id) DO NOTHING",
        user_id,
    )


async def get_user(pool: asyncpg.Pool, user_id: int) -> Optional[asyncpg.Record]:
    return await pool.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)


async def get_user_by_username(pool: asyncpg.Pool, username: str) -> Optional[asyncpg.Record]:
    u = username.strip()
    if u.startswith("@"):
        u = u[1:]
    return await pool.fetchrow("SELECT * FROM users WHERE lower(username)=lower($1)", u)


async def set_language(pool: asyncpg.Pool, user_id: int, lang: str) -> None:
    await pool.execute("UPDATE users SET language=$1, updated_at=now() WHERE user_id=$2", lang, user_id)


async def set_state(pool: asyncpg.Pool, user_id: int, state: Optional[str]) -> None:
    await pool.execute("UPDATE users SET state=$1, updated_at=now() WHERE user_id=$2", state, user_id)


async def set_status(pool: asyncpg.Pool, user_id: int, status: str) -> None:
    await pool.execute("UPDATE users SET status=$1, updated_at=now() WHERE user_id=$2", status, user_id)


async def add_spent(pool: asyncpg.Pool, user_id: int, add_cents: int) -> None:
    await pool.execute(
        "UPDATE users SET spent_cents = spent_cents + $1, updated_at=now() WHERE user_id=$2",
        int(add_cents),
        user_id,
    )


# ---------- DISCOUNT ----------
async def add_discount(pool: asyncpg.Pool, user_id: int, add_cents: int) -> None:
    await pool.execute(
        "UPDATE users SET discount_cents = discount_cents + $1, updated_at=now() WHERE user_id=$2",
        int(add_cents),
        int(user_id),
    )


async def get_discount_cents(pool: asyncpg.Pool, user_id: int) -> int:
    row = await pool.fetchrow("SELECT discount_cents FROM users WHERE user_id=$1", int(user_id))
    return int(row["discount_cents"] if row and row["discount_cents"] is not None else 0)


async def subtract_discount(pool: asyncpg.Pool, user_id: int, sub_cents: int) -> None:
    await pool.execute(
        "UPDATE users SET discount_cents = GREATEST(discount_cents - $1, 0), updated_at=now() WHERE user_id=$2",
        int(sub_cents),
        int(user_id),
    )


async def create_claim(pool: asyncpg.Pool, user_id: int, ref_username: str) -> int:
    row = await pool.fetchrow(
        "INSERT INTO claims (user_id, ref_username, status) VALUES ($1, $2, 'PENDING') RETURNING id",
        user_id,
        ref_username,
    )
    return int(row["id"])


async def get_claim(pool: asyncpg.Pool, claim_id: int) -> Optional[asyncpg.Record]:
    return await pool.fetchrow("SELECT * FROM claims WHERE id=$1", claim_id)


async def decide_claim(pool: asyncpg.Pool, claim_id: int, decision: str) -> None:
    await pool.execute("UPDATE claims SET status=$1, decided_at=now() WHERE id=$2", decision, claim_id)


async def get_setting(pool: asyncpg.Pool, key: str, default: str) -> str:
    row = await pool.fetchrow("SELECT value FROM settings WHERE key=$1", key)
    return row["value"] if row else default


async def set_setting(pool: asyncpg.Pool, key: str, value: str) -> None:
    await pool.execute(
        "INSERT INTO settings (key, value) VALUES ($1, $2) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        key,
        value,
    )


# Requests
async def create_request(pool: asyncpg.Pool, user_id: int, title: str, details: str, use_discount: bool) -> int:
    row = await pool.fetchrow(
        """
        INSERT INTO requests (user_id, title, details, fee_cents, total_cents, use_discount, discount_used_cents, status)
        VALUES ($1, $2, $3, 0, 0, $4, 0, 'NEW')
        RETURNING id
        """,
        user_id,
        title,
        details,
        bool(use_discount),
    )
    return int(row["id"])


async def get_request(pool: asyncpg.Pool, request_id: int) -> Optional[asyncpg.Record]:
    return await pool.fetchrow("SELECT * FROM requests WHERE id=$1", request_id)


async def list_user_active_requests(pool: asyncpg.Pool, user_id: int) -> List[asyncpg.Record]:
    return await pool.fetch(
        """
        SELECT id, status, total_cents
        FROM requests
        WHERE user_id=$1 AND status NOT IN ('DONE','CANCELLED')
        ORDER BY id DESC
        """,
        user_id,
    )


async def cancel_request(pool: asyncpg.Pool, request_id: int) -> None:
    await pool.execute("UPDATE requests SET status='CANCELLED' WHERE id=$1", int(request_id))


async def set_request_fee(pool: asyncpg.Pool, request_id: int, fee_cents: int) -> int:
    """
    Sets fee and total. If request.use_discount is true, applies user's discount balance.
    Returns discount_used_cents.
    """
    r = await get_request(pool, int(request_id))
    if not r:
        return 0

    user_id = int(r["user_id"])
    use_discount = bool(r.get("use_discount") or False)

    discount_used = 0
    total = int(fee_cents)

    if use_discount and fee_cents > 0:
        bal = await get_discount_cents(pool, user_id)
        discount_used = min(int(fee_cents), int(bal))
        total = int(fee_cents) - int(discount_used)
        if discount_used > 0:
            await subtract_discount(pool, user_id, discount_used)

    await pool.execute(
        """
        UPDATE requests
        SET fee_cents=$1,
            discount_used_cents=$2,
            total_cents=$3,
            status=CASE WHEN status='NEW' THEN 'IN_REVIEW' ELSE status END
        WHERE id=$4
        """,
        int(fee_cents),
        int(discount_used),
        int(total),
        int(request_id),
    )
    return int(discount_used)


async def set_request_status(pool: asyncpg.Pool, request_id: int, status: str) -> None:
    await pool.execute("UPDATE requests SET status=$1 WHERE id=$2", status, int(request_id))


async def mark_request_done(pool: asyncpg.Pool, request_id: int) -> None:
    await pool.execute("UPDATE requests SET status='DONE' WHERE id=$1", int(request_id))


async def save_admin_message_id(pool: asyncpg.Pool, request_id: int, message_id: int) -> None:
    await pool.execute("UPDATE requests SET admin_message_id=$1 WHERE id=$2", int(message_id), int(request_id))


async def count_done_requests(pool: asyncpg.Pool, user_id: int) -> int:
    row = await pool.fetchrow("SELECT COUNT(*) AS c FROM requests WHERE user_id=$1 AND status='DONE'", user_id)
    return int(row["c"] if row else 0)


# ================== LIFECYCLE ==================
async def on_startup(app: Application) -> None:
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    app.bot_data["db_pool"] = pool
    async with pool.acquire() as conn:
        await conn.execute(CREATE_USERS_SQL)
        for q in ALTER_USERS_SQL:
            await conn.execute(q)

        await conn.execute(CREATE_CLAIMS_SQL)
        await conn.execute(CREATE_SETTINGS_SQL)
        await conn.execute(CREATE_REQUESTS_SQL)
        for q in ALTER_REQUESTS_SQL:
            await conn.execute(q)

        await conn.execute(CREATE_STOCK_SQL)

        cur = await conn.fetchrow("SELECT value FROM settings WHERE key='operator_online'")
        if not cur:
            await conn.execute("INSERT INTO settings (key, value) VALUES ('operator_online', 'true')")


async def on_shutdown(app: Application) -> None:
    pool = app.bot_data.get("db_pool")
    if pool:
        await pool.close()


# ================== UTIL ==================
def is_admin(uid: Optional[int]) -> bool:
    return uid == ADMIN_ID_INT


async def is_operator_online(pool: asyncpg.Pool) -> bool:
    v = await get_setting(pool, "operator_online", "true")
    return str(v).lower() in ("1", "true", "yes", "on")


# ================== HOME ==================
async def send_home(chat_id: int, lang: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    online = await is_operator_online(pool)
    status_line = t(lang, "op_online") if online else t(lang, "op_offline")
    caption = t(lang, "safe_welcome") + "\n\n" + esc(status_line)

    try:
        with open(HOME_IMAGE_PATH, "rb") as f:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(f, filename="home.png"),
                caption=caption,
                reply_markup=kb_safe_menu(lang),
                parse_mode="HTML",
            )
    except FileNotFoundError:
        await context.bot.send_message(
            chat_id=chat_id,
            text=caption,
            reply_markup=kb_safe_menu(lang),
            parse_mode="HTML",
        )


# ================== ADMIN REQUEST MESSAGE ==================
async def build_admin_request_text(pool: asyncpg.Pool, request_id: int) -> str:
    r = await get_request(pool, request_id)
    if not r:
        return "Request not found."

    user_id = int(r["user_id"])
    u = await get_user(pool, user_id)
    uname = f"@{u['username']}" if u and u.get("username") else "(no username)"
    name = ((u.get("first_name") or "") + " " + (u.get("last_name") or "")).strip() if u else "(no name)"

    st = str(r["status"])
    fee = cents_to_eur_str(int(r["fee_cents"]))
    total = cents_to_eur_str(int(r["total_cents"]))
    use_disc = "YES" if bool(r.get("use_discount") or False) else "NO"
    disc_used = cents_to_eur_str(int(r.get("discount_used_cents") or 0))

    return (
        "REQUEST\n\n"
        f"Request ID: {request_id}\n"
        f"Status: {st}\n"
        f"User ID: {user_id}\n"
        f"Name: {name}\n"
        f"Username: {uname}\n\n"
        f"Title: {r['title']}\n\n"
        f"Details:\n{r['details']}\n\n"
        f"Use discount: {use_disc}\n"
        f"Discount used: {disc_used}\n"
        f"Fee: {fee}\n"
        f"TOTAL: {total}\n"
    )


async def notify_admin_request(pool: asyncpg.Pool, context: ContextTypes.DEFAULT_TYPE, request_id: int) -> None:
    text = await build_admin_request_text(pool, request_id)
    sent = await context.bot.send_message(
        chat_id=ADMIN_ID_INT,
        text=text,
        reply_markup=kb_admin_request(request_id),
    )
    await save_admin_message_id(pool, request_id, sent.message_id)


async def refresh_admin_request_message(pool: asyncpg.Pool, context: ContextTypes.DEFAULT_TYPE, request_id: int) -> None:
    r = await get_request(pool, request_id)
    if not r:
        return

    mid = r["admin_message_id"]
    if not mid:
        await notify_admin_request(pool, context, request_id)
        return

    text = await build_admin_request_text(pool, request_id)
    st = str(r["status"])
    markup = None if st in ("DONE", "CANCELLED") else kb_admin_request(request_id)

    try:
        await context.bot.edit_message_text(
            chat_id=ADMIN_ID_INT,
            message_id=int(mid),
            text=text,
            reply_markup=markup,
        )
    except Exception:
        await notify_admin_request(pool, context, request_id)


# ================== USER FLOW ==================
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

    if await is_banned(pool, user.id):
        await update.message.reply_text(t(lang, "banned"))
        return

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

    if await is_banned(pool, user.id):
        await edit_ui(query, t(lang, "banned"), reply_markup=None, parse_mode="HTML")
        return

    data = query.data or ""
    is_photo = bool(query.message and getattr(query.message, "photo", None))

    if data.startswith("lang:"):
        new_lang = data.split(":", 1)[1]
        if new_lang not in ("et", "ru", "en"):
            new_lang = "et"
        await set_language(pool, user.id, new_lang)

        # refresh current screen (same message)
        if status == "SAFE":
            online = await is_operator_online(pool)
            status_line = t(new_lang, "op_online") if online else t(new_lang, "op_offline")
            caption = t(new_lang, "safe_welcome") + "\n\n" + esc(status_line)
            await edit_ui(query, caption, reply_markup=kb_safe_menu(new_lang), parse_mode="HTML")
            return

        if status == "PENDING":
            if is_photo:
                await query.edit_message_caption(caption=t(new_lang, "already_pending"), reply_markup=kb_languages())
            else:
                await query.edit_message_text(t(new_lang, "already_pending"), reply_markup=kb_languages())
            return

        if state == "WAITING_REF":
            if is_photo:
                await query.edit_message_caption(caption=t(new_lang, "waiting_ref"), reply_markup=kb_languages())
            else:
                await query.edit_message_text(t(new_lang, "waiting_ref"), reply_markup=kb_languages())
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
            online = await is_operator_online(pool)
            status_line = t(lang, "op_online") if online else t(lang, "op_offline")
            caption = t(lang, "safe_welcome") + "\n\n" + esc(status_line)
            await edit_ui(query, caption, reply_markup=kb_safe_menu(lang), parse_mode="HTML")
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

    await upsert_user(pool, user)
    db_user = await get_user(pool, user.id)
    lang = (db_user["language"] if db_user and db_user["language"] else "et")
    status = (db_user["status"] if db_user and db_user["status"] else "NEW")

    if await is_banned(pool, user.id):
        await edit_ui(query, t(lang, "banned"), reply_markup=None, parse_mode="HTML")
        return

    if status != "SAFE":
        await edit_ui(query, t(lang, "do_start"), reply_markup=kb_languages(), parse_mode="HTML")
        return

    data = query.data or ""

    async def show_home() -> None:
        online = await is_operator_online(pool)
        status_line = t(lang, "op_online") if online else t(lang, "op_offline")
        caption = t(lang, "safe_welcome") + "\n\n" + esc(status_line)
        await edit_ui(query, caption, reply_markup=kb_safe_menu(lang), parse_mode="HTML")

    if data == "safe:home":
        await show_home()
        return

    if data == "safe:help":
        await edit_ui(query, t(lang, "help_text"), reply_markup=kb_safe_menu(lang), parse_mode="HTML")
        return

    if data == "safe:account":
        spent = int(db_user["spent_cents"] or 0)
        disc = int(db_user["discount_cents"] or 0)
        done_count = await count_done_requests(pool, user.id)
        text = (
            f"{t(lang, 'account_text')}\n\n"
            f"User ID: <code>{user.id}</code>\n"
            f"Spent: <code>{esc(cents_to_eur_str(spent))}</code>\n"
            f"Discount: <code>{esc(cents_to_eur_str(disc))}</code>\n"
            f"Completed: <code>{done_count}</code>"
        )
        await edit_ui(query, text, reply_markup=kb_safe_menu(lang), parse_mode="HTML")
        return

    if data == "safe:shop":
        await edit_ui(
            query,
            t(lang, "shop_title"),
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Open", callback_data="shop:page:0")],
                    [InlineKeyboardButton(t(lang, "home"), callback_data="safe:home")],
                ]
            ),
            parse_mode="HTML",
        )
        return

    if data == "safe:requests":
        reqs = await list_user_active_requests(pool, user.id)
        online = await is_operator_online(pool)
        has_any = len(reqs) > 0
        header = t(lang, "requests_title")
        if not online:
            header = header + "\n\n" + esc(t(lang, "op_offline_block"))
        await edit_ui(query, header, reply_markup=kb_requests_home(lang, has_any, online), parse_mode="HTML")
        return

    await show_home()


# ================== DISCOUNT CHOICE CALLBACK ==================
async def discount_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    if await is_banned(pool, user.id):
        await edit_ui(query, t(lang, "banned"), reply_markup=None, parse_mode="HTML")
        return

    if status != "SAFE":
        await edit_ui(query, t(lang, "do_start"), reply_markup=kb_languages(), parse_mode="HTML")
        return

    online = await is_operator_online(pool)
    if not online:
        await edit_ui(query, t(lang, "op_offline_block"), reply_markup=kb_safe_menu(lang), parse_mode="HTML")
        return

    data = query.data or ""
    use_discount = (data == "disc:yes")

    context.user_data["req_wizard"] = {"step": "TITLE", "use_discount": use_discount}
    await edit_ui(query, t(lang, "request_enter_title"), reply_markup=None, parse_mode="HTML")


# ================== REQUEST USER CALLBACKS ==================
async def request_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    if await is_banned(pool, user.id):
        await edit_ui(query, t(lang, "banned"), reply_markup=None, parse_mode="HTML")
        return

    if status != "SAFE":
        await edit_ui(query, t(lang, "do_start"), reply_markup=kb_languages(), parse_mode="HTML")
        return

    data = query.data or ""
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    rid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    if action == "new":
        online = await is_operator_online(pool)
        if not online:
            await edit_ui(query, t(lang, "op_offline_block"), reply_markup=kb_requests_home(lang, False, online), parse_mode="HTML")
            return

        bal = await get_discount_cents(pool, user.id)
        if bal > 0:
            prompt = t(lang, "discount_use_prompt").format(amount=cents_to_eur_str(bal))
            await edit_ui(query, prompt, reply_markup=kb_discount_choice(lang, bal), parse_mode="HTML")
            return

        context.user_data["req_wizard"] = {"step": "TITLE", "use_discount": False}
        await edit_ui(query, t(lang, "request_enter_title"), reply_markup=None, parse_mode="HTML")
        return

    if action == "list":
        reqs = await list_user_active_requests(pool, user.id)
        if not reqs:
            online = await is_operator_online(pool)
            await edit_ui(query, t(lang, "requests_empty"), reply_markup=kb_requests_home(lang, False, online), parse_mode="HTML")
            return
        await edit_ui(query, t(lang, "menu_list"), reply_markup=kb_requests_list(lang, reqs), parse_mode="HTML")
        return

    if action == "view" and rid:
        r = await get_request(pool, rid)
        if not r or int(r["user_id"]) != int(user.id):
            online = await is_operator_online(pool)
            await edit_ui(query, "Not found.", reply_markup=kb_requests_home(lang, True, online), parse_mode="HTML")
            return

        st = str(r["status"])
        can_cancel = st not in ("DONE", "CANCELLED")
        total = cents_to_eur_str(int(r["total_cents"] or 0))

        text = (
            f"<b>Request #{rid}</b>\n"
            f"Status: <code>{esc(st)}</code>\n"
            f"Total: <code>{esc(total)}</code>\n\n"
            f"<b>{esc(r['title'])}</b>\n\n"
            f"{esc(r['details'])}"
        )
        await edit_ui(query, text, reply_markup=kb_request_detail(lang, rid, can_cancel), parse_mode="HTML")
        return

    if action == "cancel" and rid:
        await edit_ui(query, "Cancel?", reply_markup=kb_request_cancel_confirm(lang, rid), parse_mode="HTML")
        return

    if action == "confirm" and rid:
        r = await get_request(pool, rid)
        if r and int(r["user_id"]) == int(user.id) and str(r["status"]) not in ("DONE", "CANCELLED"):
            await cancel_request(pool, rid)
            await refresh_admin_request_message(pool, context, rid)

        online = await is_operator_online(pool)
        await edit_ui(query, t(lang, "request_cancelled_user"), reply_markup=kb_requests_home(lang, True, online), parse_mode="HTML")
        return

    online = await is_operator_online(pool)
    await edit_ui(query, t(lang, "requests_title"), reply_markup=kb_requests_home(lang, True, online), parse_mode="HTML")


# ================== ADMIN REQUEST CALLBACKS ==================
async def admin_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    if not update.effective_user or not is_admin(update.effective_user.id):
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "r":
        return

    action = parts[1]
    rid = int(parts[2])
    r = await get_request(pool, rid)
    if not r:
        await query.edit_message_text("Request not found.", reply_markup=None)
        return

    if str(r["status"]) in ("DONE", "CANCELLED"):
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if action == "review":
        await set_request_status(pool, rid, "IN_REVIEW")
        await refresh_admin_request_message(pool, context, rid)
        user_id = int(r["user_id"])
        try:
            await context.bot.send_message(chat_id=user_id, text="👀 In review.")
        except Exception:
            pass
        return

    if action == "fee":
        context.user_data["fee_input"] = {"request_id": rid}
        await context.bot.send_message(chat_id=ADMIN_ID_INT, text=t("et", "admin_fee_prompt"))
        return

    if action == "complete":
        await mark_request_done(pool, rid)

        r2 = await get_request(pool, rid)
        user_id = int(r2["user_id"])
        total_cents = int(r2["total_cents"])
        await add_spent(pool, user_id, total_cents)

        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        await refresh_admin_request_message(pool, context, rid)

        try:
            await context.bot.send_message(chat_id=user_id, text=f"✅ Completed.\nTOTAL: {cents_to_eur_str(total_cents)}")
        except Exception:
            pass
        return


# ================== TEXT HANDLER ==================
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

    if await is_banned(pool, user.id):
        await update.message.reply_text(t(lang, "banned"))
        return

    text = update.message.text.strip()

    # --- ADMIN fee input ---
    if is_admin(user.id) and context.user_data.get("fee_input"):
        finfo = context.user_data.get("fee_input", {})
        rid = int(finfo.get("request_id", 0))
        try:
            fee = float(text.replace(",", "."))
            if fee < 0:
                raise ValueError()
            fee_cents = eur_to_cents(fee)
        except Exception:
            await update.message.reply_text(t("et", "admin_fee_prompt"))
            return

        discount_used = await set_request_fee(pool, rid, fee_cents)
        context.user_data.pop("fee_input", None)

        await update.message.reply_text(f"✅ Fee set: {cents_to_eur_str(fee_cents)}")
        await refresh_admin_request_message(pool, context, rid)

        try:
            if int(discount_used) > 0:
                r = await get_request(pool, rid)
                if r:
                    user_id = int(r["user_id"])
                    u = await get_user(pool, user_id)
                    u_lang = (u["language"] if u and u.get("language") else "et")
                    msg = t(u_lang, "discount_applied").format(
                        amount=cents_to_eur_str(int(discount_used)),
                        total=cents_to_eur_str(int(r["total_cents"])),
                    )
                    await context.bot.send_message(chat_id=user_id, text=msg)
        except Exception:
            pass
        return

    # --- STOCK wizard (ADMIN) ---
    if is_admin(user.id) and context.user_data.get("stock_wizard"):
        wizs = context.user_data.get("stock_wizard", {})
        step = wizs.get("step")
        if step == "TITLE":
            title = text[:80]
            context.user_data["stock_wizard"] = {"step": "PRICE", "title": title}
            await update.message.reply_text(t("et", "stock_price"))
            return
        if step == "PRICE":
            try:
                price = float(text.replace(",", "."))
                if price < 0:
                    raise ValueError()
                price_cents = eur_to_cents(price)
            except Exception:
                await update.message.reply_text(t("et", "stock_invalid_price"))
                return
            context.user_data["stock_wizard"] = {
                "step": "DESC",
                "title": wizs.get("title", "Item"),
                "price_cents": int(price_cents),
            }
            await update.message.reply_text(t("et", "stock_desc"))
            return
        if step == "DESC":
            desc = text[:2000]
            context.user_data["stock_wizard"] = {
                "step": "PHOTO",
                "title": wizs.get("title", "Item"),
                "price_cents": int(wizs.get("price_cents", 0)),
                "description": desc,
            }
            await update.message.reply_text(t("et", "stock_photo"))
            return
        if step == "PHOTO":
            if text.strip() == "/skip":
                title = wizs.get("title", "Item")
                price_cents = int(wizs.get("price_cents", 0))
                desc = wizs.get("description", "")
                context.user_data.pop("stock_wizard", None)
                item_id = await create_stock_item(pool, title, price_cents, desc, None)
                await update.message.reply_text(t("et", "stock_added").format(id=item_id))
                return

    # --- Request wizard ---
    wiz = context.user_data.get("req_wizard")
    if wiz and status == "SAFE":
        if wiz.get("step") == "TITLE":
            title = text[:80]
            context.user_data["req_wizard"] = {
                "step": "DETAILS",
                "title": title,
                "use_discount": bool(wiz.get("use_discount") or False),
            }
            await update.message.reply_text(t(lang, "request_enter_details"))
            return

        if wiz.get("step") == "DETAILS":
            title = wiz.get("title", "Request")
            details = text[:2000]
            use_discount = bool(wiz.get("use_discount") or False)
            context.user_data.pop("req_wizard", None)

            rid = await create_request(pool, user.id, title, details, use_discount)
            await update.message.reply_text(t(lang, "request_sent"))
            await notify_admin_request(pool, context, rid)
            return

    # --- Claim referral ---
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
            reply_markup=kb_admin_claim_decision(claim_id),
        )
        return

    if status == "SAFE":
        await update.message.reply_text(t(lang, "home"))
        return

    await update.message.reply_text(t(lang, "do_start"), reply_markup=kb_languages())


# ================== ADMIN CLAIM CALLBACKS ==================
async def admin_claim_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    if action == "ban":
        await set_banned(pool, target_user_id, True)
        if claim["status"] == "PENDING":
            await decide_claim(pool, claim_id, "DECLINED")
        await set_status(pool, target_user_id, "DECLINED")
        await set_state(pool, target_user_id, None)
        try:
            await context.bot.send_message(chat_id=target_user_id, text=t(target_lang, "banned"))
        except Exception:
            pass
        await query.edit_message_text(base_text + "\n🚫 BANNED")
        return

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


async def admin_ban_userid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    await set_banned(pool, user_id, True)
    await set_status(pool, user_id, "DECLINED")
    await set_state(pool, user_id, None)
    try:
        tu = await get_user(pool, user_id)
        tu_lang = (tu["language"] if tu and tu.get("language") else "et")
        await context.bot.send_message(chat_id=user_id, text=t(tu_lang, "banned"))
    except Exception:
        pass

    await query.edit_message_text((query.message.text or "") + "\n🚫 BANNED")


# ================== ADMIN COMMANDS (ADMIN ONLY) ==================
async def admin_add_safe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Admin-only:
      /add <user_id|@username>
      /add discount @username 5
      /add discount <user_id> 7.50
    """
    user = update.effective_user
    if not user or not is_admin(user.id) or not update.message:
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    args = context.args or []

    if not args:
        await update.message.reply_text("Usage: /add <user_id|@username> | /add discount @username 5")
        return

    if args[0].lower() == "discount":
        if len(args) != 3:
            await update.message.reply_text("Usage: /add discount @username 5")
            return

        target = args[1].strip()
        amount_s = args[2].strip()

        try:
            amount = float(amount_s.replace(",", "."))
            if amount <= 0:
                raise ValueError()
            add_cents = eur_to_cents(amount)
        except Exception:
            await update.message.reply_text("❌ Amount must be positive. Example: /add discount @user 5")
            return

        target_user_id = await resolve_user_id(pool, target)
        if not target_user_id:
            await update.message.reply_text("❌ User not found.")
            return

        await ensure_user_exists(pool, target_user_id)
        await add_discount(pool, target_user_id, add_cents)

        try:
            tu = await get_user(pool, target_user_id)
            tu_lang = (tu["language"] if tu and tu.get("language") else "et")
            msg = t(tu_lang, "discount_added").format(amount=cents_to_eur_str(add_cents))
            await context.bot.send_message(chat_id=target_user_id, text=msg)
        except Exception:
            pass

        await update.message.reply_text(f"✅ Discount added: {cents_to_eur_str(add_cents)} -> {target}")
        return

    if len(args) != 1:
        await update.message.reply_text("Usage: /add <user_id|@username> | /add discount @username 5")
        return

    resolved = await resolve_user_id(pool, args[0])
    if not resolved:
        await update.message.reply_text("❌ User not found.")
        return

    user_id = int(resolved)
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
    if len(args) != 1:
        await update.message.reply_text("Usage: /remove <user_id|@username>")
        return

    resolved = await resolve_user_id(pool, args[0])
    if not resolved:
        await update.message.reply_text("❌ User not found.")
        return

    user_id = int(resolved)
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


async def admin_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not is_admin(update.effective_user.id) or not update.message:
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    args = context.args or []
    if len(args) != 1:
        await update.message.reply_text(TEXTS["et"]["search_usage"])
        return

    token = args[0].strip()
    u = None
    if token.startswith("@"):
        u = await get_user_by_username(pool, token)
    elif token.isdigit():
        u = await get_user(pool, int(token))
    else:
        await update.message.reply_text(TEXTS["et"]["search_usage"])
        return

    if not u:
        await update.message.reply_text(TEXTS["et"]["search_not_found"])
        return

    user_id = int(u["user_id"])
    spent = int(u["spent_cents"] or 0)
    disc = int(u["discount_cents"] or 0)
    done_count = await count_done_requests(pool, user_id)
    status = (u["status"] or "NEW")
    safe_flag = "✅" if status == "SAFE" else "❌"
    banned_flag = "⛔" if bool(u.get("banned") or False) else "✅"
    uname = f"@{u['username']}" if u.get("username") else "(no username)"

    accepted_ref = await count_accepted_referrals(pool, uname if uname.startswith("@") else token)

    msg = (
        "SEARCH RESULT\n\n"
        f"User: {uname}\n"
        f"User ID: {user_id}\n"
        f"SAFE: {safe_flag} ({status})\n"
        f"BAN: {banned_flag}\n"
        f"Verify accepted via this @: {accepted_ref}\n"
        f"Spent: {cents_to_eur_str(spent)}\n"
        f"Discount: {cents_to_eur_str(disc)}\n"
        f"Completed: {done_count}\n"
    )
    await update.message.reply_text(msg)


# ================== ADMIN BAN/LISTS (ADMIN ONLY) ==================
async def admin_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not is_admin(update.effective_user.id) or not update.message:
        return
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    args = context.args or []
    if len(args) != 1:
        await update.message.reply_text("Usage: /ban <user_id|@username>")
        return
    uid = await resolve_user_id(pool, args[0])
    if not uid:
        await update.message.reply_text("❌ User not found.")
        return
    await set_banned(pool, uid, True)
    await update.message.reply_text("✅ Banned.")


async def admin_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not is_admin(update.effective_user.id) or not update.message:
        return
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    args = context.args or []
    if len(args) != 1:
        await update.message.reply_text("Usage: /unban <user_id|@username>")
        return
    uid = await resolve_user_id(pool, args[0])
    if not uid:
        await update.message.reply_text("❌ User not found.")
        return
    await set_banned(pool, uid, False)
    await update.message.reply_text("✅ Unbanned.")


async def admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not is_admin(update.effective_user.id) or not update.message:
        return
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    what = (context.args[0].lower() if context.args else "safe")
    if what not in ("safe", "ban"):
        await update.message.reply_text("Usage: /list safe | /list ban")
        return

    if what == "safe":
        rows = await pool.fetch("SELECT user_id, username FROM users WHERE status='SAFE' ORDER BY updated_at DESC LIMIT 50")
        if not rows:
            await update.message.reply_text("SAFE list is empty.")
            return
        lines = ["SAFE LIST (last 50):"]
        for r in rows:
            uname = f"@{r['username']}" if r.get("username") else "(no username)"
            lines.append(f"{uname} — {int(r['user_id'])}")
        await update.message.reply_text("\n".join(lines))
        return

    rows = await pool.fetch("SELECT user_id, username FROM users WHERE banned=true ORDER BY updated_at DESC LIMIT 50")
    if not rows:
        await update.message.reply_text("BAN list is empty.")
        return
    lines = ["BAN LIST (last 50):"]
    for r in rows:
        uname = f"@{r['username']}" if r.get("username") else "(no username)"
        lines.append(f"{uname} — {int(r['user_id'])}")
    await update.message.reply_text("\n".join(lines))


# ================== STOCK DB ==================
async def create_stock_item(pool: asyncpg.Pool, title: str, price_cents: int, description: str, photo_file_id: Optional[str]) -> int:
    row = await pool.fetchrow(
        "INSERT INTO stock_items (title, price_cents, description, photo_file_id, active) VALUES ($1,$2,$3,$4,true) RETURNING id",
        title,
        int(price_cents),
        description,
        photo_file_id,
    )
    return int(row["id"])


async def remove_stock_item(pool: asyncpg.Pool, item_id: int) -> bool:
    res = await pool.execute("UPDATE stock_items SET active=false WHERE id=$1", int(item_id))
    try:
        n = int(res.split()[-1])
    except Exception:
        n = 0
    return n > 0


async def list_stock_items(pool: asyncpg.Pool, offset: int = 0, limit: int = 8) -> List[asyncpg.Record]:
    return await pool.fetch(
        "SELECT id, title, price_cents, description, photo_file_id FROM stock_items WHERE active=true ORDER BY id DESC OFFSET $1 LIMIT $2",
        int(offset),
        int(limit),
    )


async def count_stock_items(pool: asyncpg.Pool) -> int:
    row = await pool.fetchrow("SELECT COUNT(*) AS c FROM stock_items WHERE active=true")
    return int(row["c"] if row else 0)


def kb_shop_list(lang: str, items: List[asyncpg.Record], page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for it in items:
        iid = int(it["id"])
        title = str(it["title"])
        price = cents_to_eur_str(int(it["price_cents"] or 0))
        rows.append([InlineKeyboardButton(f"{title} — {price}", callback_data=f"shop:item:{iid}:{page}")])

    nav: List[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(t(lang, "shop_prev"), callback_data=f"shop:page:{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(t(lang, "shop_next"), callback_data=f"shop:page:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(t(lang, "back"), callback_data="safe:home")])
    return InlineKeyboardMarkup(rows)


def kb_shop_item(lang: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(t(lang, "back"), callback_data=f"shop:page:{page}")],
            [InlineKeyboardButton(t(lang, "home"), callback_data="safe:home")],
        ]
    )


async def shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    if await is_banned(pool, user.id):
        await edit_ui(query, t(lang, "banned"), reply_markup=None, parse_mode="HTML")
        return

    if status != "SAFE":
        await edit_ui(query, t(lang, "do_start"), reply_markup=kb_languages(), parse_mode="HTML")
        return

    data = query.data or ""
    parts = data.split(":")
    if len(parts) < 2:
        return

    if parts[1] == "page":
        page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        per = 8
        total = await count_stock_items(pool)
        if total <= 0:
            await edit_ui(query, t(lang, "shop_empty"), reply_markup=kb_safe_menu(lang), parse_mode="HTML")
            return
        total_pages = max(1, (total + per - 1) // per)
        page = max(0, min(page, total_pages - 1))
        items = await list_stock_items(pool, offset=page * per, limit=per)
        header = t(lang, "shop_title") + f"\n\nPage {page+1}/{total_pages}"
        await edit_ui(query, header, reply_markup=kb_shop_list(lang, items, page, total_pages), parse_mode="HTML")
        return

    if parts[1] == "item":
        iid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        it = await pool.fetchrow(
            "SELECT id, title, price_cents, description, photo_file_id FROM stock_items WHERE id=$1 AND active=true",
            int(iid),
        )
        if not it:
            await edit_ui(query, t(lang, "stock_not_found"), reply_markup=kb_safe_menu(lang), parse_mode="HTML")
            return

        title = str(it["title"])
        price = cents_to_eur_str(int(it["price_cents"] or 0))
        desc = str(it["description"] or "")
        caption = f"<b>{esc(title)}</b>\n<code>{esc(price)}</code>\n\n{esc(desc)}"

        photo_id = it["photo_file_id"]
        if photo_id:
            try:
                media = InputMediaPhoto(media=str(photo_id), caption=caption, parse_mode=ParseMode.HTML)
                await query.edit_message_media(media=media, reply_markup=kb_shop_item(lang, page))
                return
            except Exception:
                pass

        await edit_ui(query, caption, reply_markup=kb_shop_item(lang, page), parse_mode="HTML")
        return


# ================== STOCK COMMAND (ADMIN) ==================
async def stock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not is_admin(update.effective_user.id) or not update.message:
        return
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    args = context.args or []
    if not args:
        await update.message.reply_text(t("et", "stock_usage"))
        return

    sub = args[0].lower()

    if sub == "add":
        context.user_data["stock_wizard"] = {"step": "TITLE"}
        await update.message.reply_text(t("et", "stock_title"))
        return

    if sub == "remove":
        if len(args) != 2 or not args[1].isdigit():
            await update.message.reply_text(t("et", "stock_usage"))
            return
        ok = await remove_stock_item(pool, int(args[1]))
        await update.message.reply_text(t("et", "stock_removed") if ok else t("et", "stock_not_found"))
        return

    if sub == "list":
        items = await pool.fetch(
            "SELECT id, title, price_cents FROM stock_items WHERE active=true ORDER BY id DESC LIMIT 50"
        )
        if not items:
            await update.message.reply_text(t("et", "shop_empty"))
            return
        lines = ["STOCK (last 50):"]
        for it in items:
            lines.append(f"#{int(it['id'])} — {it['title']} — {cents_to_eur_str(int(it['price_cents'] or 0))}")
        await update.message.reply_text("\n".join(lines))
        return

    await update.message.reply_text(t("et", "stock_usage"))


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Only used for /stock add wizard
    if not update.message or not update.message.photo or not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        return
    wiz = context.user_data.get("stock_wizard")
    if not wiz or wiz.get("step") != "PHOTO":
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    photo = update.message.photo[-1]
    file_id = photo.file_id
    title = wiz.get("title", "Item")
    price_cents = int(wiz.get("price_cents", 0))
    desc = wiz.get("description", "")
    context.user_data.pop("stock_wizard", None)

    item_id = await create_stock_item(pool, title, price_cents, desc, file_id)
    await update.message.reply_text(t("et", "stock_added").format(id=item_id))


# ================== MAIN ==================
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
    app.add_handler(CallbackQueryHandler(on_lang_or_verify, pattern=r"^(lang:.*|verify$)"))
    app.add_handler(CallbackQueryHandler(safe_menu_click, pattern=r"^safe:"))
    app.add_handler(CallbackQueryHandler(discount_choice_callback, pattern=r"^disc:"))
    app.add_handler(CallbackQueryHandler(request_user_callback, pattern=r"^req:"))
    app.add_handler(CallbackQueryHandler(shop_callback, pattern=r"^shop:"))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # admin callbacks
    app.add_handler(CallbackQueryHandler(admin_claim_decision, pattern=r"^adm:(acc|dec|ban):"))
    app.add_handler(CallbackQueryHandler(admin_remove_safe_callback, pattern=r"^adm:rem:"))
    app.add_handler(CallbackQueryHandler(admin_ban_userid_callback, pattern=r"^adm:banuid:"))
    app.add_handler(CallbackQueryHandler(admin_request_callback, pattern=r"^r:"))

    # admin commands
    app.add_handler(CommandHandler("add", admin_add_safe))
    app.add_handler(CommandHandler("remove", admin_remove_safe))
    app.add_handler(CommandHandler("search", admin_search))
    app.add_handler(CommandHandler("ban", admin_ban))
    app.add_handler(CommandHandler("unban", admin_unban))
    app.add_handler(CommandHandler("list", admin_list))
    app.add_handler(CommandHandler("stock", stock_cmd))

    # run
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
