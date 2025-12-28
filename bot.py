import os
import datetime
import asyncpg
from typing import Optional, Dict, Any

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

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = os.getenv("ADMIN_ID")  # numeric string

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL missing")
if not ADMIN_ID or not ADMIN_ID.isdigit():
    raise RuntimeError("ADMIN_ID missing or not numeric")

ADMIN_ID_INT = int(ADMIN_ID)

CLAIM_IMAGE_PATH = "claim.png"
HOME_IMAGE_PATH = "home.png"
SHOP_IMAGE_PATH = "shop.png"

# ------------------ DB SCHEMA ------------------

CREATE_USERS_SQL = """
CREATE TABLE IF NOT EXISTS users (
  user_id BIGINT PRIMARY KEY,
  first_name TEXT,
  last_name TEXT,
  username TEXT,
  language TEXT DEFAULT 'et',
  status TEXT DEFAULT 'NEW',          -- NEW/PENDING/SAFE/DECLINED
  state TEXT DEFAULT NULL,            -- NULL/WAITING_REF
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
  photo_file_id TEXT NOT NULL,
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
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();",
]

# ------------------ TEXTS ------------------

TEXTS: Dict[str, Dict[str, str]] = {
    "et": {
        "welcome": "Tere! Vajuta Verify 👇",
        "verify": "✅ Verify",
        "waiting_ref": "Kirjuta oma sõbra @username, kelle käest sa selle boti said (näiteks: @mart).",
        "invalid_ref": "Palun kirjuta korrektne @username (peab algama @-ga). Proovi uuesti.",
        "wait_admin": "Aitäh! Oota palun admini vastust. ⏳",
        "already_pending": "Su verifitseerimine on juba ootel. Oota admini vastust. ⏳",
        "accepted": "✅ Admin kinnitas su verifitseerimise. Sa oled nüüd SAFE. Tee /start",
        "declined": "❌ Admin lükkas su verifitseerimise tagasi.",
        "removed_safe": "⚠️ Admin eemaldas sind SAFE listist. Palun tee verifitseerimine uuesti /start kaudu.",
        "added_safe": "✅ Admin lisas sind SAFE listi. Tee /start",
        "do_start": "Tee /start",
        "safe_welcome": (
            "👋 Tere tulemast *The UnderGround Market*\n\n"
            "Siin saad vaadata pakkumisi, teha oste ja hallata oma kontot.\n"
            "Vali alt menüüst üks valik 👇"
        ),
        "shop_title": "🛍 *Shop*\nVali toode 👇",
        "shop_empty": "🛍 Shop on hetkel tühi.",
        "help_text": "❓ Help: kirjuta adminile.",
        "account_text": "👤 Account",
        "buy_soon": "💳 Buy: tuleb varsti.",
        "back_home": "⬅️ Home",
        "admin_add_name": "🧾 /additem\nSaada itemi *nimi*:",
        "admin_add_text": "Saada *lühike tekst* (kirjeldus):",
        "admin_add_photo": "Saada nüüd *pilt* (foto) selle itemi jaoks:",
        "admin_add_done": "✅ Item lisatud Shopi!",
        "admin_remove_pick": "🗑 Vali item, mida eemaldada:",
        "admin_removed": "✅ Item eemaldatud.",
        "admin_remove_empty": "Pole midagi eemaldada.",
        "admin_bad": "Midagi läks valesti.",
    },
    "ru": {
        "welcome": "Привет! Нажми Verify 👇",
        "verify": "✅ Verify",
        "waiting_ref": "Напиши @username друга, от которого ты получил бота (например: @mart).",
        "invalid_ref": "Пожалуйста, напиши корректный @username (должен начинаться с @). Попробуй ещё раз.",
        "wait_admin": "Спасибо! Пожалуйста, дождись решения админа. ⏳",
        "already_pending": "Твоя проверка уже ожидает решения. ⏳",
        "accepted": "✅ Админ подтвердил проверку. Ты теперь SAFE. Напиши /start",
        "declined": "❌ Админ отклонил проверку.",
        "removed_safe": "⚠️ Админ удалил тебя из SAFE списка. Пройди проверку заново через /start.",
        "added_safe": "✅ Админ добавил тебя в SAFE список. Напиши /start",
        "do_start": "Напиши /start",
        "safe_welcome": (
            "👋 Добро пожаловать в *The UnderGround Market*\n\n"
            "Здесь ты можешь смотреть предложения, покупать и управлять аккаунтом.\n"
            "Выбери пункт меню ниже 👇"
        ),
        "shop_title": "🛍 *Shop*\nВыбери товар 👇",
        "shop_empty": "🛍 Shop сейчас пуст.",
        "help_text": "❓ Help: напиши админу.",
        "account_text": "👤 Account",
        "buy_soon": "💳 Buy: скоро будет.",
        "back_home": "⬅️ Home",
        "admin_add_name": "🧾 /additem\nОтправь *название* товара:",
        "admin_add_text": "Отправь *короткий текст* (описание):",
        "admin_add_photo": "Теперь отправь *фото* товара:",
        "admin_add_done": "✅ Товар добавлен в Shop!",
        "admin_remove_pick": "🗑 Выбери товар для удаления:",
        "admin_removed": "✅ Удалено.",
        "admin_remove_empty": "Нечего удалять.",
        "admin_bad": "Что-то пошло не так.",
    },
    "en": {
        "welcome": "Hi! Press Verify 👇",
        "verify": "✅ Verify",
        "waiting_ref": "Send your friend's @username who gave you this bot (example: @mart).",
        "invalid_ref": "Please send a valid @username (must start with @). Try again.",
        "wait_admin": "Thanks! Please wait for admin approval. ⏳",
        "already_pending": "Your verification is already pending. ⏳",
        "accepted": "✅ Admin approved you. You are SAFE now. Send /start",
        "declined": "❌ Admin declined your verification.",
        "removed_safe": "⚠️ Admin removed you from the SAFE list. Please verify again via /start.",
        "added_safe": "✅ Admin added you to the SAFE list. Send /start",
        "do_start": "Send /start",
        "safe_welcome": (
            "👋 Welcome to *The UnderGround Market*\n\n"
            "Here you can browse offers, buy items, and manage your account.\n"
            "Choose an option below 👇"
        ),
        "shop_title": "🛍 *Shop*\nChoose an item 👇",
        "shop_empty": "🛍 Shop is empty right now.",
        "help_text": "❓ Help: contact admin.",
        "account_text": "👤 Account",
        "buy_soon": "💳 Buy: coming soon.",
        "back_home": "⬅️ Home",
        "admin_add_name": "🧾 /additem\nSend item *name*:",
        "admin_add_text": "Send a *short text* (description):",
        "admin_add_photo": "Now send an item *photo*:",
        "admin_add_done": "✅ Item added to Shop!",
        "admin_remove_pick": "🗑 Pick an item to remove:",
        "admin_removed": "✅ Removed.",
        "admin_remove_empty": "Nothing to remove.",
        "admin_bad": "Something went wrong.",
    },
}

def t(lang: str, key: str) -> str:
    if lang not in TEXTS:
        lang = "et"
    return TEXTS[lang].get(key, TEXTS["et"].get(key, key))

# ------------------ KEYBOARDS ------------------

def kb_languages() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🇪🇪 Eesti", callback_data="lang:et"),
        InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru"),
        InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
    ]])

def kb_languages_and_verify(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇪🇪 Eesti", callback_data="lang:et"),
            InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
        ],
        [InlineKeyboardButton(t(lang, "verify"), callback_data="verify")],
    ])

def kb_safe_menu(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛍 Shop", callback_data="safe:shop"),
            InlineKeyboardButton("💳 Buy", callback_data="safe:buy"),
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="safe:help"),
            InlineKeyboardButton("👤 Account", callback_data="safe:account"),
        ],
        [
            InlineKeyboardButton("🇪🇪 Eesti", callback_data="lang:et"),
            InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
        ],
    ])

def kb_shop_items(lang: str, items: list[asyncpg.Record]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for it in items:
        rows.append([InlineKeyboardButton(it["name"], callback_data=f"item:{it['id']}")])
    rows.append([InlineKeyboardButton(t(lang, "back_home"), callback_data="safe:home")])
    rows.append([
        InlineKeyboardButton("🇪🇪 Eesti", callback_data="lang:et"),
        InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru"),
        InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
    ])
    return InlineKeyboardMarkup(rows)

def kb_item_detail(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Shop", callback_data="safe:shop")],
        [InlineKeyboardButton(t(lang, "back_home"), callback_data="safe:home")],
        [
            InlineKeyboardButton("🇪🇪 Eesti", callback_data="lang:et"),
            InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
        ],
    ])

def kb_admin_decision(claim_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept", callback_data=f"adm:acc:{claim_id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"adm:dec:{claim_id}"),
    ]])

def kb_admin_remove(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Remove from SAFE", callback_data=f"adm:rem:{user_id}")]])

def kb_admin_removeitem(items: list[asyncpg.Record]) -> InlineKeyboardMarkup:
    rows = []
    for it in items:
        rows.append([InlineKeyboardButton(f"🗑 {it['name']}", callback_data=f"adm:rmitem:{it['id']}")])
    return InlineKeyboardMarkup(rows) if rows else InlineKeyboardMarkup([])

# ------------------ DB HELPERS ------------------

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

async def list_items(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return await pool.fetch("SELECT id, name, short_text, photo_file_id FROM items ORDER BY id ASC")

async def get_item(pool: asyncpg.Pool, item_id: int) -> Optional[asyncpg.Record]:
    return await pool.fetchrow("SELECT id, name, short_text, photo_file_id FROM items WHERE id=$1", item_id)

async def add_item(pool: asyncpg.Pool, name: str, short_text: str, photo_file_id: str) -> None:
    await pool.execute(
        "INSERT INTO items (name, short_text, photo_file_id) VALUES ($1, $2, $3)",
        name, short_text, photo_file_id
    )

async def remove_item(pool: asyncpg.Pool, item_id: int) -> None:
    await pool.execute("DELETE FROM items WHERE id=$1", item_id)

# ------------------ LIFECYCLE ------------------

async def on_startup(app: Application) -> None:
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    app.bot_data["db_pool"] = pool
    async with pool.acquire() as conn:
        await conn.execute(CREATE_USERS_SQL)
        for q in ALTER_USERS_SQL:
            await conn.execute(q)
        await conn.execute(CREATE_CLAIMS_SQL)
        await conn.execute(CREATE_ITEMS_SQL)

async def on_shutdown(app: Application) -> None:
    pool = app.bot_data.get("db_pool")
    if pool:
        await pool.close()

# ------------------ UTIL ------------------

def is_admin(uid: Optional[int]) -> bool:
    return uid == ADMIN_ID_INT

def reset_additem(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("additem", None)

async def edit_or_send(query, message, text: str, reply_markup: InlineKeyboardMarkup, is_photo: bool, parse_mode: Optional[str] = None) -> None:
    if query:
        if is_photo:
            await query.edit_message_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    else:
        await message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)

# ------------------ HANDLERS ------------------

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
        try:
            with open(HOME_IMAGE_PATH, "rb") as f:
                await context.bot.send_photo(
                    chat_id=chat.id,
                    photo=InputFile(f, filename="home.png"),
                    caption=t(lang, "safe_welcome"),
                    reply_markup=kb_safe_menu(lang),
                    parse_mode="Markdown",
                )
        except FileNotFoundError:
            await update.message.reply_text(t(lang, "safe_welcome"), reply_markup=kb_safe_menu(lang), parse_mode="Markdown")
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

        if status2 == "SAFE":
            await edit_or_send(query, None, t(new_lang, "safe_welcome"), kb_safe_menu(new_lang), is_photo=False, parse_mode="Markdown")
            return
        if status2 == "PENDING":
            await edit_or_send(query, None, t(new_lang, "already_pending"), kb_languages(), is_photo=is_photo)
            return
        if state2 == "WAITING_REF":
            await edit_or_send(query, None, t(new_lang, "waiting_ref"), kb_languages(), is_photo=is_photo)
            return

        await edit_or_send(query, None, t(new_lang, "welcome"), kb_languages_and_verify(new_lang), is_photo=is_photo)
        return

    if data == "verify":
        if status == "PENDING":
            await edit_or_send(query, None, t(lang, "already_pending"), kb_languages(), is_photo=is_photo)
            return
        if status == "SAFE":
            await edit_or_send(query, None, t(lang, "safe_welcome"), kb_safe_menu(lang), is_photo=False, parse_mode="Markdown")
            return

        await set_state(pool, user.id, "WAITING_REF")
        await edit_or_send(query, None, t(lang, "waiting_ref"), kb_languages(), is_photo=is_photo)
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

    if status != "SAFE":
        await query.edit_message_text(t(lang, "do_start"), reply_markup=kb_languages())
        return

    data = query.data or ""

    if data == "safe:home":
        await query.edit_message_text(t(lang, "safe_welcome"), reply_markup=kb_safe_menu(lang), parse_mode="Markdown")
        return

    if data == "safe:buy":
        await query.edit_message_text(t(lang, "buy_soon"), reply_markup=kb_safe_menu(lang))
        return

    if data == "safe:help":
        await query.edit_message_text(t(lang, "help_text"), reply_markup=kb_safe_menu(lang))
        return

    if data == "safe:account":
        await query.edit_message_text(
            f"{t(lang, 'account_text')}\n\nUser ID: `{user.id}`",
            reply_markup=kb_safe_menu(lang),
            parse_mode="Markdown",
        )
        return

    if data == "safe:shop":
        items = await list_items(pool)
        if not items:
            try:
                with open(SHOP_IMAGE_PATH, "rb") as f:
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id,
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
                    chat_id=query.message.chat_id,
                    photo=InputFile(f, filename="shop.png"),
                    caption=t(lang, "shop_title"),
                    reply_markup=kb_shop_items(lang, items),
                    parse_mode="Markdown",
                )
        except FileNotFoundError:
            await query.edit_message_text(
                t(lang, "shop_title"),
                reply_markup=kb_shop_items(lang, items),
                parse_mode="Markdown",
            )
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

    item = await get_item(pool, item_id)
    if not item:
        await query.edit_message_text(t(lang, "admin_bad"), reply_markup=kb_safe_menu(lang))
        return

    caption = f"*{item['name']}*\n\n{item['short_text']}"
    try:
        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=item["photo_file_id"],
            caption=caption,
            reply_markup=kb_item_detail(lang),
            parse_mode="Markdown",
        )
    except Exception:
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=caption,
            reply_markup=kb_item_detail(lang),
            parse_mode="Markdown",
        )

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

    # Admin additem flow (name/text)
    addflow: Optional[Dict[str, Any]] = context.user_data.get("additem")
    if addflow and is_admin(user.id):
        step = addflow.get("step")
        if step == "NAME":
            addflow["name"] = text
            addflow["step"] = "TEXT"
            context.user_data["additem"] = addflow
            await update.message.reply_text(t(lang, "admin_add_text"), parse_mode="Markdown")
            return
        if step == "TEXT":
            addflow["short_text"] = text
            addflow["step"] = "PHOTO"
            context.user_data["additem"] = addflow
            await update.message.reply_text(t(lang, "admin_add_photo"), parse_mode="Markdown")
            return

    if status == "SAFE":
        await update.message.reply_text(t(lang, "safe_welcome"), reply_markup=kb_safe_menu(lang), parse_mode="Markdown")
        return

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
            "🧾 NEW CLAIM\n\n"
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

    await update.message.reply_text(t(lang, "do_start"), reply_markup=kb_languages())

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
    if not name or not short_text:
        reset_additem(context)
        await update.message.reply_text(t(lang, "admin_bad"))
        return

    photo = update.message.photo[-1]
    file_id = photo.file_id

    try:
        await add_item(pool, name=name, short_text=short_text, photo_file_id=file_id)
    except Exception:
        reset_additem(context)
        await update.message.reply_text("❌ Item name already exists or DB error.")
        return

    reset_additem(context)
    await update.message.reply_text(t(lang, "admin_add_done"))

# ------------------ ADMIN CLAIM BUTTONS ------------------

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
    claim_status = claim["status"]
    if claim_status in ("ACCEPTED", "DECLINED"):
        await query.edit_message_text(f"Already decided: {claim_status}")
        return

    target_user = await get_user(pool, target_user_id)
    target_lang = (target_user["language"] if target_user and target_user["language"] else "et")

    base_text = query.message.text or ""

    if action == "acc":
        await decide_claim(pool, claim_id, "ACCEPTED")
        await set_status(pool, target_user_id, "SAFE")
        await set_state(pool, target_user_id, None)

        # ✅ NO MENU HERE (as requested)
        await context.bot.send_message(
            chat_id=target_user_id,
            text=t(target_lang, "accepted"),
        )

        await query.edit_message_text(base_text + "\n✅ DECISION: ACCEPTED", reply_markup=kb_admin_remove(target_user_id))
        return

    if action == "dec":
        await decide_claim(pool, claim_id, "DECLINED")
        await set_status(pool, target_user_id, "DECLINED")
        await set_state(pool, target_user_id, None)

        await context.bot.send_message(chat_id=target_user_id, text=t(target_lang, "declined"), reply_markup=kb_languages())
        await query.edit_message_text(base_text + "\n❌ DECISION: DECLINED")
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

    await set_status(pool, user_id, "NEW")
    await set_state(pool, user_id, None)

    target_user = await get_user(pool, user_id)
    target_lang = (target_user["language"] if target_user and target_user["language"] else "et")

    try:
        await context.bot.send_message(chat_id=user_id, text=t(target_lang, "removed_safe"), reply_markup=kb_languages())
    except Exception:
        pass

    await query.edit_message_text((query.message.text or "") + "\n🗑 Removed from SAFE.")

# ------------------ ADMIN SAFE COMMANDS ------------------

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

    # ✅ NO MENU HERE (as requested)
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
        await context.bot.send_message(chat_id=user_id, text=t(target_lang, "removed_safe"), reply_markup=kb_languages())
    except Exception:
        pass

    await update.message.reply_text("✅ Removed from SAFE list.")

# ------------------ ADMIN ITEM COMMANDS ------------------

async def admin_additem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not is_admin(user.id) or not update.message:
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    db_user = await get_user(pool, user.id)
    lang = (db_user["language"] if db_user and db_user["language"] else "et")

    context.user_data["additem"] = {"step": "NAME"}
    await update.message.reply_text(t(lang, "admin_add_name"), parse_mode="Markdown")

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
    db_user = await get_user(pool, update.effective_user.id)
    lang = (db_user["language"] if db_user and db_user["language"] else "et")

    try:
        item_id = int((query.data or "").split(":", 2)[2])
    except Exception:
        await query.edit_message_text(t(lang, "admin_bad"))
        return

    await remove_item(pool, item_id)
    await query.edit_message_text(t(lang, "admin_removed"))

# ------------------ MAIN ------------------

def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))

    # admin safe
    app.add_handler(CommandHandler("add", admin_add_safe))
    app.add_handler(CommandHandler("remove", admin_remove_safe))

    # admin items
    app.add_handler(CommandHandler("additem", admin_additem))
    app.add_handler(CommandHandler("removeitem", admin_removeitem))

    # callbacks
    app.add_handler(CallbackQueryHandler(on_lang_or_verify, pattern=r"^(lang:(et|ru|en)|verify)$"))
    app.add_handler(CallbackQueryHandler(safe_menu_click, pattern=r"^safe:(shop|buy|help|account|home)$"))
    app.add_handler(CallbackQueryHandler(item_open, pattern=r"^item:\d+$"))
    app.add_handler(CallbackQueryHandler(admin_removeitem_callback, pattern=r"^adm:rmitem:\d+$"))

    app.add_handler(CallbackQueryHandler(admin_decision, pattern=r"^adm:(acc|dec):\d+$"))
    app.add_handler(CallbackQueryHandler(admin_remove_safe_callback, pattern=r"^adm:rem:\d+$"))

    # messages
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
