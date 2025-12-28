import os
import datetime
import asyncpg
from typing import Optional, Dict

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
    raise RuntimeError("BOT_TOKEN puudub (Railway Variables -> BOT_TOKEN)")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL puudub (Railway PostgreSQL plugin)")
if not ADMIN_ID or not ADMIN_ID.isdigit():
    raise RuntimeError("ADMIN_ID puudub või pole number (Railway Variables -> ADMIN_ID)")

ADMIN_ID_INT = int(ADMIN_ID)
CLAIM_IMAGE_PATH = "claim.png"

# ------------------ DB schema ------------------

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

# ------------------ i18n ------------------

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

        # SAFE menu text
        "safe_welcome": (
            "👋 Tere tulemast *The UnderGround Market*\n\n"
            "Siin saad vaadata pakkumisi, teha oste ja hallata oma kontot.\n"
            "Vali alt menüüst üks valik 👇"
        ),
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
    },
}

def t(lang: str, key: str) -> str:
    if lang not in TEXTS:
        lang = "et"
    return TEXTS[lang].get(key, TEXTS["et"].get(key, key))

# ------------------ keyboards ------------------

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
    # Shop / Buy / Help / Account + keeled all eraldi real
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

def kb_admin_decision(claim_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept", callback_data=f"adm:acc:{claim_id}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"adm:dec:{claim_id}"),
    ]])

def kb_admin_remove(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Remove from SAFE", callback_data=f"adm:rem:{user_id}")]])

# ------------------ DB helpers ------------------

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
        """
        INSERT INTO users (user_id, updated_at)
        VALUES ($1, now())
        ON CONFLICT (user_id) DO NOTHING
        """,
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

# ------------------ lifecycle ------------------

async def on_startup(app: Application) -> None:
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    app.bot_data["db_pool"] = pool
    async with pool.acquire() as conn:
        await conn.execute(CREATE_USERS_SQL)
        for q in ALTER_USERS_SQL:
            await conn.execute(q)
        await conn.execute(CREATE_CLAIMS_SQL)

async def on_shutdown(app: Application) -> None:
    pool = app.bot_data.get("db_pool")
    if pool:
        await pool.close()

# ------------------ handler helpers ------------------

async def edit_or_send(query, message, text: str, reply_markup: InlineKeyboardMarkup, is_photo: bool) -> None:
    if query:
        if is_photo:
            await query.edit_message_caption(caption=text, reply_markup=reply_markup, parse_mode="Markdown")
        else:
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

# ------------------ handlers ------------------

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

    # SAFE -> show market menu
    if status == "SAFE":
        await update.message.reply_text(
            t(lang, "safe_welcome"),
            reply_markup=kb_safe_menu(lang),
            parse_mode="Markdown",
        )
        return

    # PENDING -> wait
    if status == "PENDING":
        await update.message.reply_text(t(lang, "already_pending"), reply_markup=kb_languages())
        return

    # NEW / DECLINED -> show claim.png + verify
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

    # language change
    if data.startswith("lang:"):
        new_lang = data.split(":", 1)[1]
        if new_lang not in ("et", "ru", "en"):
            new_lang = "et"
        await set_language(pool, user.id, new_lang)

        # reload
        db_user2 = await get_user(pool, user.id)
        status2 = (db_user2["status"] if db_user2 else "NEW")
        state2 = (db_user2["state"] if db_user2 else None)

        if status2 == "SAFE":
            await edit_or_send(query, None, t(new_lang, "safe_welcome"), kb_safe_menu(new_lang), is_photo=False)
            return
        if status2 == "PENDING":
            await edit_or_send(query, None, t(new_lang, "already_pending"), kb_languages(), is_photo=is_photo)
            return
        if state2 == "WAITING_REF":
            await edit_or_send(query, None, t(new_lang, "waiting_ref"), kb_languages(), is_photo=is_photo)
            return

        await edit_or_send(query, None, t(new_lang, "welcome"), kb_languages_and_verify(new_lang), is_photo=is_photo)
        return

    # verify pressed
    if data == "verify":
        if status == "SAFE":
            await edit_or_send(query, None, t(lang, "safe_welcome"), kb_safe_menu(lang), is_photo=False)
            return
        if status == "PENDING":
            await edit_or_send(query, None, t(lang, "already_pending"), kb_languages(), is_photo=is_photo)
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
        # if not safe, bounce
        await query.edit_message_text(t(lang, "do_start"), reply_markup=kb_languages(), parse_mode="Markdown")
        return

    data = query.data or ""
    if data == "safe:shop":
        await query.edit_message_text("🛍 Shop: coming soon.", reply_markup=kb_safe_menu(lang))
    elif data == "safe:buy":
        await query.edit_message_text("💳 Buy: coming soon.", reply_markup=kb_safe_menu(lang))
    elif data == "safe:help":
        await query.edit_message_text("❓ Help: kirjutage adminile / contact.", reply_markup=kb_safe_menu(lang))
    elif data == "safe:account":
        await query.edit_message_text(
            f"👤 Account\n\nUser ID: `{user.id}`",
            reply_markup=kb_safe_menu(lang),
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text(t(lang, "safe_welcome"), reply_markup=kb_safe_menu(lang), parse_mode="Markdown")

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
            f"Claim ID: {claim_id}\n"
            f"User ID: {user.id}\n"
            f"Name: {full_name or '(no name)'}\n"
            f"Username: {uname}\n"
            f"Referral: {ref_username}\n"
            f"Date: {now_utc}\n"
        )

        await context.bot.send_message(
            chat_id=ADMIN_ID_INT,
            text=admin_text,
            reply_markup=kb_admin_decision(claim_id),
        )
        return

    await update.message.reply_text(t(lang, "do_start"), reply_markup=kb_languages())

# ------------------ admin decisions ------------------

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

        await context.bot.send_message(
            chat_id=target_user_id,
            text=t(target_lang, "accepted"),
            reply_markup=kb_safe_menu(target_lang),
            parse_mode="Markdown",
        )

        await query.edit_message_text(
            base_text + "\n✅ DECISION: ACCEPTED",
            reply_markup=kb_admin_remove(target_user_id),
        )
        return

    if action == "dec":
        await decide_claim(pool, claim_id, "DECLINED")
        await set_status(pool, target_user_id, "DECLINED")
        await set_state(pool, target_user_id, None)

        await context.bot.send_message(chat_id=target_user_id, text=t(target_lang, "declined"), reply_markup=kb_languages())
        await query.edit_message_text(base_text + "\n❌ DECISION: DECLINED")
        return

    await query.edit_message_text("Unknown action.")

async def admin_remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

# ------------------ admin commands ------------------

async def admin_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != ADMIN_ID_INT:
        return
    if not update.message:
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
        await context.bot.send_message(
            chat_id=user_id,
            text=t(target_lang, "added_safe"),
            reply_markup=kb_safe_menu(target_lang),
            parse_mode="Markdown",
        )
    except Exception:
        pass

    await update.message.reply_text("✅ Added to SAFE list.")

async def admin_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or update.effective_user.id != ADMIN_ID_INT:
        return
    if not update.message:
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

# ------------------ main ------------------

def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("add", admin_add_command))
    app.add_handler(CommandHandler("remove", admin_remove_command))

    # language + verify
    app.add_handler(CallbackQueryHandler(on_lang_or_verify, pattern=r"^(lang:(et|ru|en)|verify)$"))

    # safe menu
    app.add_handler(CallbackQueryHandler(safe_menu_click, pattern=r"^safe:(shop|buy|help|account)$"))

    # admin buttons
    app.add_handler(CallbackQueryHandler(admin_decision, pattern=r"^adm:(acc|dec):\d+$"))
    app.add_handler(CallbackQueryHandler(admin_remove_callback, pattern=r"^adm:rem:\d+$"))

    # text
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
