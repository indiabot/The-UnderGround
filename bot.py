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

# ================== MONEY/COUNTERS (generic) ==================
# We keep "spent" and "completed count" as generic metrics for completed requests.
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
  state TEXT DEFAULT NULL,            -- NULL/WAITING_REF/REQ_DESC
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

CREATE_SETTINGS_SQL = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# Requests = generic "orders"
CREATE_REQUESTS_SQL = """
CREATE TABLE IF NOT EXISTS requests (
  id SERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL REFERENCES users(user_id),
  title TEXT NOT NULL,
  details TEXT NOT NULL,
  fee_cents INT NOT NULL DEFAULT 0,
  total_cents INT NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'NEW',     -- NEW/IN_REVIEW/DONE/CANCELLED
  admin_message_id BIGINT NULL,
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

ALTER_REQUESTS_SQL = [
    "ALTER TABLE requests ADD COLUMN IF NOT EXISTS fee_cents INT NOT NULL DEFAULT 0;",
    "ALTER TABLE requests ADD COLUMN IF NOT EXISTS total_cents INT NOT NULL DEFAULT 0;",
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

        "safe_welcome": (
            "*Private Hub*\n\n"
            "Siin saad esitada päringuid ja hallata oma infot.\n"
            "Vali alt menüüst üks valik."
        ),

        "help_text": "Help: kirjuta adminile.",
        "account_text": "Account",

        "requests_title": "*Requests*\nVali päring.",
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

        "safe_welcome": (
            "*Private Hub*\n\n"
            "Здесь ты можешь отправлять запросы и управлять информацией.\n"
            "Выбери пункт меню ниже."
        ),

        "help_text": "Help: напиши админу.",
        "account_text": "Account",

        "requests_title": "*Requests*\nВыбери запрос.",
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

        "safe_welcome": (
            "*Private Hub*\n\n"
            "Submit requests and manage your info.\n"
            "Choose an option below."
        ),

        "help_text": "Help: contact admin.",
        "account_text": "Account",

        "requests_title": "*Requests*\nPick a request.",
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
    },
}


def t(lang: str, key: str) -> str:
    if lang not in TEXTS:
        lang = "et"
    return TEXTS[lang].get(key, TEXTS["et"].get(key, key))


# ================== KEYBOARDS ==================
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
            InlineKeyboardButton("Requests", callback_data="safe:requests"),
            InlineKeyboardButton("Account", callback_data="safe:account"),
        ],
        [
            InlineKeyboardButton("Help", callback_data="safe:help"),
        ],
        [
            InlineKeyboardButton("🇪🇪 ET", callback_data="lang:et"),
            InlineKeyboardButton("🇷🇺 RU", callback_data="lang:ru"),
            InlineKeyboardButton("🇬🇧 EN", callback_data="lang:en"),
        ],
    ])


def kb_requests_home(lang: str, has_any: bool) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(t(lang, "request_new"), callback_data="req:new")],
    ]
    if has_any:
        rows.append([InlineKeyboardButton("📄 List", callback_data="req:list")])
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
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "request_cancel_confirm"), callback_data=f"req:confirm:{request_id}")],
        [InlineKeyboardButton(t(lang, "back"), callback_data=f"req:view:{request_id}")],
    ])


def kb_admin_claim_decision(claim_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept", callback_data=f"adm:acc:{claim_id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"adm:dec:{claim_id}"),
    ]])


def kb_admin_remove(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Remove SAFE", callback_data=f"adm:rem:{user_id}")]])


def kb_admin_request(request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Complete", callback_data=f"r:complete:{request_id}"),
        InlineKeyboardButton("💰 Fee", callback_data=f"r:fee:{request_id}"),
    ]])


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
        user.id, user.first_name, user.last_name, user.username
    )


async def ensure_user_exists(pool: asyncpg.Pool, user_id: int) -> None:
    await pool.execute(
        "INSERT INTO users (user_id, updated_at) VALUES ($1, now()) ON CONFLICT (user_id) DO NOTHING",
        user_id
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


async def get_setting(pool: asyncpg.Pool, key: str, default: str) -> str:
    row = await pool.fetchrow("SELECT value FROM settings WHERE key=$1", key)
    return row["value"] if row else default


async def set_setting(pool: asyncpg.Pool, key: str, value: str) -> None:
    await pool.execute(
        "INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        key, value
    )


# Requests
async def create_request(pool: asyncpg.Pool, user_id: int, title: str, details: str) -> int:
    row = await pool.fetchrow(
        """
        INSERT INTO requests (user_id, title, details, fee_cents, total_cents, status)
        VALUES ($1, $2, $3, 0, 0, 'NEW')
        RETURNING id
        """,
        user_id, title, details
    )
    return int(row["id"])


async def get_request(pool: asyncpg.Pool, request_id: int) -> Optional[asyncpg.Record]:
    return await pool.fetchrow("SELECT * FROM requests WHERE id=$1", request_id)


async def list_user_active_requests(pool: asyncpg.Pool, user_id: int) -> List[asyncpg.Record]:
    return await pool.fetch(
        "SELECT id, status, total_cents FROM requests WHERE user_id=$1 AND status NOT IN ('DONE','CANCELLED') ORDER BY id DESC",
        user_id
    )


async def cancel_request(pool: asyncpg.Pool, request_id: int) -> None:
    await pool.execute("UPDATE requests SET status='CANCELLED' WHERE id=$1", int(request_id))


async def set_request_fee(pool: asyncpg.Pool, request_id: int, fee_cents: int) -> None:
    await pool.execute(
        "UPDATE requests SET fee_cents=$1, total_cents=$1 WHERE id=$2",
        int(fee_cents), int(request_id)
    )


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


# ================== HOME ==================
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

    return (
        "REQUEST\n\n"
        f"Request ID: {request_id}\n"
        f"Status: {st}\n"
        f"User ID: {user_id}\n"
        f"Name: {name}\n"
        f"Username: {uname}\n\n"
        f"Title: {r['title']}\n\n"
        f"Details:\n{r['details']}\n\n"
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

        # refresh current simple screen
        if status == "SAFE":
            if is_photo:
                await query.edit_message_caption(
                    caption=t(new_lang, "safe_welcome"),
                    reply_markup=kb_safe_menu(new_lang),
                    parse_mode="Markdown",
                )
            else:
                await query.edit_message_text(
                    t(new_lang, "safe_welcome"),
                    reply_markup=kb_safe_menu(new_lang),
                    parse_mode="Markdown",
                )
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

        if state == "REQ_DESC":
            if is_photo:
                await query.edit_message_caption(caption="...", reply_markup=kb_languages())
            else:
                await query.edit_message_text("...", reply_markup=kb_languages())
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
        done_count = await count_done_requests(pool, user.id)
        await query.edit_message_text(
            f"{t(lang,'account_text')}\n\nUser ID: `{user.id}`\nSpent: `{cents_to_eur_str(spent)}`\nCompleted: `{done_count}`",
            reply_markup=kb_safe_menu(lang),
            parse_mode="Markdown",
        )
        return

    if data == "safe:home":
        await send_home(chat_id, lang, context)
        return

    if data == "safe:requests":
        reqs = await list_user_active_requests(pool, user.id)
        has_any = len(reqs) > 0
        await query.edit_message_text(
            t(lang, "requests_title"),
            reply_markup=kb_requests_home(lang, has_any),
            parse_mode="Markdown",
        )
        return


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
    if status != "SAFE":
        await query.edit_message_text(t(lang, "do_start"), reply_markup=kb_languages())
        return

    data = query.data or ""
    parts = data.split(":")

    # req:new -> start wizard
    if data == "req:new":
        context.user_data["req_wizard"] = {"step": "TITLE"}
        await query.edit_message_text(t(lang, "request_enter_title"), reply_markup=kb_languages())
        return

    # req:list -> show list
    if data == "req:list":
        reqs = await list_user_active_requests(pool, user.id)
        if not reqs:
            await query.edit_message_text(t(lang, "requests_empty"), reply_markup=kb_safe_menu(lang))
            return
        await query.edit_message_text(t(lang, "requests_title"), reply_markup=kb_requests_list(lang, reqs), parse_mode="Markdown")
        return

    # req:view:<id>, req:cancel:<id>, req:confirm:<id>
    if len(parts) == 3 and parts[0] == "req":
        action = parts[1]
        rid = int(parts[2])
        r = await get_request(pool, rid)
        if not r or int(r["user_id"]) != user.id:
            await query.edit_message_text("Not found.")
            return

        st = str(r["status"])
        can_cancel = st in ("NEW", "IN_REVIEW")

        detail = (
            f"*Request* #{rid}\n\n"
            f"Status: {st}\n\n"
            f"Title: {r['title']}\n\n"
            f"Details:\n{r['details']}\n\n"
            f"Fee: {cents_to_eur_str(int(r['fee_cents']))}\n"
            f"TOTAL: {cents_to_eur_str(int(r['total_cents']))}\n"
        )

        if action == "view":
            await query.edit_message_text(detail, reply_markup=kb_request_detail(lang, rid, can_cancel), parse_mode="Markdown")
            return

        if action == "cancel":
            if not can_cancel:
                await query.edit_message_text(detail, reply_markup=kb_request_detail(lang, rid, False), parse_mode="Markdown")
                return
            await query.edit_message_text(detail + "\n❓", reply_markup=kb_request_cancel_confirm(lang, rid), parse_mode="Markdown")
            return

        if action == "confirm":
            if not can_cancel:
                await query.edit_message_text(detail, reply_markup=kb_request_detail(lang, rid, False), parse_mode="Markdown")
                return

            await cancel_request(pool, rid)
            r2 = await get_request(pool, rid)

            # notify admin + remove buttons
            if r2 and r2["admin_message_id"]:
                try:
                    await context.bot.edit_message_reply_markup(
                        chat_id=ADMIN_ID_INT,
                        message_id=int(r2["admin_message_id"]),
                        reply_markup=None
                    )
                except Exception:
                    pass
                try:
                    await context.bot.send_message(chat_id=ADMIN_ID_INT, text=f"Request #{rid} {t(lang,'request_cancelled_admin')}")
                except Exception:
                    pass
                await refresh_admin_request_message(pool, context, rid)

            await query.edit_message_text(t(lang, "request_cancelled_user"), reply_markup=kb_safe_menu(lang))
            return


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

        # remove buttons immediately
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        # refresh admin text (still no buttons)
        await refresh_admin_request_message(pool, context, rid)

        # notify user
        u = await get_user(pool, user_id)
        lang = (u["language"] if u and u.get("language") else "et")
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

        await set_request_fee(pool, rid, fee_cents)
        context.user_data.pop("fee_input", None)

        await update.message.reply_text(f"✅ Fee set: {cents_to_eur_str(fee_cents)}")
        await refresh_admin_request_message(pool, context, rid)
        return

    # --- Request wizard ---
    wiz = context.user_data.get("req_wizard")
    if wiz and status == "SAFE":
        if wiz.get("step") == "TITLE":
            title = text[:80]
            context.user_data["req_wizard"] = {"step": "DETAILS", "title": title}
            await update.message.reply_text(t(lang, "request_enter_details"))
            return
        if wiz.get("step") == "DETAILS":
            title = wiz.get("title", "Request")
            details = text[:2000]
            context.user_data.pop("req_wizard", None)

            rid = await create_request(pool, user.id, title, details)
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
        await send_home(chat.id, lang, context)
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


# ================== ADMIN COMMANDS ==================
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


async def admin_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not is_admin(update.effective_user.id) or not update.message:
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    args = context.args or []
    if len(args) != 1 or not args[0].startswith("@"):
        await update.message.reply_text(TEXTS["et"]["search_usage"])
        return

    u = await get_user_by_username(pool, args[0])
    if not u:
        await update.message.reply_text(TEXTS["et"]["search_not_found"])
        return

    user_id = int(u["user_id"])
    spent = int(u["spent_cents"] or 0)
    done_count = await count_done_requests(pool, user_id)
    status = (u["status"] or "NEW")
    safe_flag = "✅" if status == "SAFE" else "❌"
    uname = f"@{u['username']}" if u.get("username") else "(no username)"

    msg = (
        "SEARCH RESULT\n\n"
        f"User: {uname}\n"
        f"User ID: {user_id}\n"
        f"SAFE: {safe_flag} ({status})\n"
        f"Spent: {cents_to_eur_str(spent)}\n"
        f"Completed: {done_count}\n"
    )
    await update.message.reply_text(msg)


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

    # admin commands
    app.add_handler(CommandHandler("add", admin_add_safe))
    app.add_handler(CommandHandler("remove", admin_remove_safe))
    app.add_handler(CommandHandler("search", admin_search))
    app.add_handler(CommandHandler("shearch", admin_search))  # alias

    # callbacks
    app.add_handler(CallbackQueryHandler(on_lang_or_verify, pattern=r"^(lang:(et|ru|en)|verify)$"))
    app.add_handler(CallbackQueryHandler(safe_menu_click, pattern=r"^safe:(requests|help|account|home)$"))
    app.add_handler(CallbackQueryHandler(request_user_callback, pattern=r"^req:(new|list|view|cancel|confirm)(:\d+)?$"))

    # admin callbacks
    app.add_handler(CallbackQueryHandler(admin_claim_decision, pattern=r"^adm:(acc|dec):\d+$"))
    app.add_handler(CallbackQueryHandler(admin_remove_safe_callback, pattern=r"^adm:rem:\d+$"))
    app.add_handler(CallbackQueryHandler(admin_request_callback, pattern=r"^r:(complete|fee):\d+$"))

    # messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
