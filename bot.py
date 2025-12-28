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
  language TEXT DEFAULT 'et',         -- et/ru/en
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

# ------------------ i18n texts ------------------

TEXTS: Dict[str, Dict[str, str]] = {
    "et": {
        "welcome": "Tere! Vali keel ja vajuta Verify 👇",
        "verify": "✅ Verify",
        "waiting_ref": "Kirjuta oma sõbra @username, kelle käest sa selle boti said (näiteks: @mart).",
        "invalid_ref": "Palun kirjuta korrektne @username (peab algama @-ga). Proovi uuesti.",
        "wait_admin": "Aitäh! Oota palun admini vastust. ⏳",
        "already_pending": "Su verifitseerimine on juba ootel. Oota admini vastust. ⏳",
        "already_safe": "Sa oled SAFE nimekirjas ✅",
        "accepted": "✅ Admin kinnitas su verifitseerimise. Sa oled nüüd SAFE.",
        "declined": "❌ Admin lükkas su verifitseerimise tagasi.",
    },
    "ru": {
        "welcome": "Привет! Выбери язык и нажми Verify 👇",
        "verify": "✅ Verify",
        "waiting_ref": "Напиши @username друга, от которого ты получил бота (например: @mart).",
        "invalid_ref": "Пожалуйста, напиши корректный @username (должен начинаться с @). Попробуй ещё раз.",
        "wait_admin": "Спасибо! Пожалуйста, дождись решения админа. ⏳",
        "already_pending": "Твоя проверка уже ожидает решения. ⏳",
        "already_safe": "Ты в SAFE списке ✅",
        "accepted": "✅ Админ подтвердил проверку. Ты теперь SAFE.",
        "declined": "❌ Админ отклонил проверку.",
    },
    "en": {
        "welcome": "Hi! Choose a language and press Verify 👇",
        "verify": "✅ Verify",
        "waiting_ref": "Send your friend's @username who gave you this bot (example: @mart).",
        "invalid_ref": "Please send a valid @username (must start with @). Try again.",
        "wait_admin": "Thanks! Please wait for admin approval. ⏳",
        "already_pending": "Your verification is already pending. ⏳",
        "already_safe": "You are on the SAFE list ✅",
        "accepted": "✅ Admin approved you. You are SAFE now.",
        "declined": "❌ Admin declined your verification.",
    },
}

def t(lang: str, key: str) -> str:
    if lang not in TEXTS:
        lang = "et"
    return TEXTS[lang].get(key, TEXTS["et"].get(key, key))

# ------------------ keyboards ------------------

def kb_language_and_verify(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇪🇪 Eesti", callback_data="lang:et"),
            InlineKeyboardButton("🇷🇺 Русский", callback_data="lang:ru"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
        ],
        [InlineKeyboardButton(t(lang, "verify"), callback_data="verify")],
    ])

def kb_admin_decision(claim_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Accept", callback_data=f"adm:acc:{claim_id}"),
            InlineKeyboardButton("❌ Decline", callback_data=f"adm:dec:{claim_id}"),
        ]
    ])

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

async def get_user(pool: asyncpg.Pool, user_id: int) -> Optional[asyncpg.Record]:
    return await pool.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)

async def set_language(pool: asyncpg.Pool, user_id: int, lang: str) -> None:
    await pool.execute(
        "UPDATE users SET language=$1, updated_at=now() WHERE user_id=$2",
        lang, user_id
    )

async def set_state(pool: asyncpg.Pool, user_id: int, state: Optional[str]) -> None:
    await pool.execute(
        "UPDATE users SET state=$1, updated_at=now() WHERE user_id=$2",
        state, user_id
    )

async def set_status(pool: asyncpg.Pool, user_id: int, status: str) -> None:
    await pool.execute(
        "UPDATE users SET status=$1, updated_at=now() WHERE user_id=$2",
        status, user_id
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
    await pool.execute(
        "UPDATE claims SET status=$1, decided_at=now() WHERE id=$2",
        decision, claim_id
    )

# ------------------ lifecycle ------------------

async def on_startup(app: Application) -> None:
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    app.bot_data["db_pool"] = pool
    async with pool.acquire() as conn:
        await conn.execute(CREATE_USERS_SQL)
        await conn.execute(CREATE_CLAIMS_SQL)

async def on_shutdown(app: Application) -> None:
    pool = app.bot_data.get("db_pool")
    if pool:
        await pool.close()

# ------------------ handlers ------------------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    await upsert_user(pool, user)
    db_user = await get_user(pool, user.id)
    lang = (db_user["language"] if db_user else "et") or "et"
    status = (db_user["status"] if db_user else "NEW") or "NEW"

    if status == "SAFE":
        await update.message.reply_text(t(lang, "already_safe"))
        return

    if status == "PENDING":
        await update.message.reply_text(t(lang, "already_pending"))
        return

    # claim.png läheb kasutajale alguses
    try:
        with open(CLAIM_IMAGE_PATH, "rb") as f:
            await context.bot.send_photo(
                chat_id=chat.id,
                photo=InputFile(f, filename="claim.png"),
                caption=t(lang, "welcome"),
                reply_markup=kb_language_and_verify(lang),
            )
    except FileNotFoundError:
        await update.message.reply_text(
            t(lang, "welcome"),
            reply_markup=kb_language_and_verify(lang),
        )

async def on_language_or_verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    lang = (db_user["language"] if db_user else "et") or "et"
    status = (db_user["status"] if db_user else "NEW") or "NEW"
    data = query.data or ""

    # Language
    if data.startswith("lang:"):
        new_lang = data.split(":", 1)[1]
        if new_lang not in ("et", "ru", "en"):
            new_lang = "et"
        await set_language(pool, user.id, new_lang)

        # Uuenda sama sõnumit (teksti) – pilt jääb samaks
        await query.edit_message_caption(
            caption=t(new_lang, "welcome"),
            reply_markup=kb_language_and_verify(new_lang),
        )
        return

    # Verify
    if data == "verify":
        if status == "SAFE":
            await query.edit_message_caption(caption=t(lang, "already_safe"))
            return
        if status == "PENDING":
            await query.edit_message_caption(caption=t(lang, "already_pending"))
            return

        await set_state(pool, user.id, "WAITING_REF")
        await query.edit_message_caption(caption=t(lang, "waiting_ref"))
        return

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
    lang = (db_user["language"] if db_user else "et") or "et"
    status = (db_user["status"] if db_user else "NEW") or "NEW"
    state = db_user["state"] if db_user else None
    text = update.message.text.strip()

    if status == "SAFE":
        await update.message.reply_text(t(lang, "already_safe"))
        return

    if status == "PENDING":
        await update.message.reply_text(t(lang, "already_pending"))
        return

    # Ootame referral @username
    if state == "WAITING_REF":
        if not text.startswith("@") or len(text) < 2 or " " in text:
            await update.message.reply_text(t(lang, "invalid_ref"))
            return

        ref_username = text
        claim_id = await create_claim(pool, user.id, ref_username)

        await set_state(pool, user.id, None)
        await set_status(pool, user.id, "PENDING")

        await update.message.reply_text(t(lang, "wait_admin"))

        # Adminile ainult tekst + nupud (ilma pildita)
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

    # default
    await update.message.reply_text(
        t(lang, "welcome"),
        reply_markup=kb_language_and_verify(lang),
    )

async def admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    # Only admin
    if not update.effective_user or update.effective_user.id != ADMIN_ID_INT:
        await query.edit_message_text("Not allowed.")
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    data = query.data or ""  # adm:acc:<id> OR adm:dec:<id>
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
    target_lang = (target_user["language"] if target_user else "et") or "et"

    if action == "acc":
        await decide_claim(pool, claim_id, "ACCEPTED")
        await set_status(pool, target_user_id, "SAFE")
        await context.bot.send_message(chat_id=target_user_id, text=t(target_lang, "accepted"))
        await query.edit_message_text((query.message.text or "") + "\n✅ DECISION: ACCEPTED")
        return

    if action == "dec":
        await decide_claim(pool, claim_id, "DECLINED")
        await set_status(pool, target_user_id, "DECLINED")
        await context.bot.send_message(chat_id=target_user_id, text=t(target_lang, "declined"))
        await query.edit_message_text((query.message.text or "") + "\n❌ DECISION: DECLINED")
        return

    await query.edit_message_text("Unknown action.")

def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(on_language_or_verify, pattern=r"^(lang:|verify)$"))
    app.add_handler(CallbackQueryHandler(admin_decision, pattern=r"^adm:(acc|dec):\d+$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
