import os
import datetime
import asyncpg
from typing import Optional

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

# ------------------ texts (ET only) ------------------

WELCOME_TEXT = "Tere! Vajuta Verify 👇"
VERIFY_TEXT = "✅ Verify"
WAITING_REF_TEXT = "Kirjuta oma sõbra @username, kelle käest sa selle boti said (näiteks: @mart)."
INVALID_REF_TEXT = "Palun kirjuta korrektne @username (peab algama @-ga). Proovi uuesti."
WAIT_ADMIN_TEXT = "Aitäh! Oota palun admini vastust. ⏳"
ALREADY_PENDING_TEXT = "Su verifitseerimine on juba ootel. Oota admini vastust. ⏳"
ALREADY_SAFE_TEXT = "Sa oled SAFE nimekirjas ✅ Tee /start"
ACCEPTED_TEXT = "✅ Admin kinnitas su verifitseerimise. Sa oled nüüd SAFE. Tee /start"
DECLINED_TEXT = "❌ Admin lükkas su verifitseerimise tagasi."
REMOVED_SAFE_TEXT = "⚠️ Admin eemaldas sind SAFE listist. Palun tee verifitseerimine uuesti /start kaudu."

# ------------------ keyboards ------------------

def kb_verify() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(VERIFY_TEXT, callback_data="verify")]
    ])

def kb_admin_decision(claim_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Accept", callback_data=f"adm:acc:{claim_id}"),
            InlineKeyboardButton("❌ Decline", callback_data=f"adm:dec:{claim_id}"),
        ]
    ])

def kb_admin_remove(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Remove from SAFE", callback_data=f"adm:rem:{user_id}")]
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
    if not user or not chat or not update.message:
        return

    await upsert_user(pool, user)
    db_user = await get_user(pool, user.id)
    status = (db_user["status"] if db_user else "NEW") or "NEW"

    if status == "SAFE":
        await update.message.reply_text(ALREADY_SAFE_TEXT)
        return

    if status == "PENDING":
        await update.message.reply_text(ALREADY_PENDING_TEXT)
        return

    # NEW / DECLINED
    try:
        with open(CLAIM_IMAGE_PATH, "rb") as f:
            await context.bot.send_photo(
                chat_id=chat.id,
                photo=InputFile(f, filename="claim.png"),
                caption=WELCOME_TEXT,
                reply_markup=kb_verify(),
            )
    except FileNotFoundError:
        await update.message.reply_text(WELCOME_TEXT, reply_markup=kb_verify())

async def on_verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    status = (db_user["status"] if db_user else "NEW") or "NEW"

    if status == "SAFE":
        # kui safe, ütle sama
        if query.message and getattr(query.message, "photo", None):
            await query.edit_message_caption(caption=ALREADY_SAFE_TEXT)
        else:
            await query.edit_message_text(ALREADY_SAFE_TEXT)
        return

    if status == "PENDING":
        if query.message and getattr(query.message, "photo", None):
            await query.edit_message_caption(caption=ALREADY_PENDING_TEXT)
        else:
            await query.edit_message_text(ALREADY_PENDING_TEXT)
        return

    await set_state(pool, user.id, "WAITING_REF")

    if query.message and getattr(query.message, "photo", None):
        await query.edit_message_caption(caption=WAITING_REF_TEXT)
    else:
        await query.edit_message_text(WAITING_REF_TEXT)

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
    status = (db_user["status"] if db_user else "NEW") or "NEW"
    state = db_user["state"] if db_user else None
    text = update.message.text.strip()

    if status == "SAFE":
        await update.message.reply_text(ALREADY_SAFE_TEXT)
        return

    if status == "PENDING":
        await update.message.reply_text(ALREADY_PENDING_TEXT)
        return

    # waiting for @ref
    if state == "WAITING_REF":
        if not text.startswith("@") or len(text) < 2 or " " in text:
            await update.message.reply_text(INVALID_REF_TEXT)
            return

        ref_username = text
        claim_id = await create_claim(pool, user.id, ref_username)

        await set_state(pool, user.id, None)
        await set_status(pool, user.id, "PENDING")

        await update.message.reply_text(WAIT_ADMIN_TEXT)

        # admin: only text + accept/decline
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
    await update.message.reply_text("Tee /start")

async def admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

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

    base_text = query.message.text or ""

    if action == "acc":
        await decide_claim(pool, claim_id, "ACCEPTED")
        await set_status(pool, target_user_id, "SAFE")

        # tell user
        await context.bot.send_message(chat_id=target_user_id, text=ACCEPTED_TEXT)

        # update admin message + remove button
        await query.edit_message_text(
            base_text + "\n✅ DECISION: ACCEPTED",
            reply_markup=kb_admin_remove(target_user_id),
        )
        return

    if action == "dec":
        await decide_claim(pool, claim_id, "DECLINED")
        await set_status(pool, target_user_id, "DECLINED")
        await context.bot.send_message(chat_id=target_user_id, text=DECLINED_TEXT)
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
    data = query.data or ""  # adm:rem:<user_id>
    parts = data.split(":")
    if len(parts) != 3:
        await query.edit_message_text("Bad callback.")
        return

    user_id = int(parts[2])

    # remove from SAFE -> NEW
    await set_status(pool, user_id, "NEW")
    await set_state(pool, user_id, None)

    # tell user
    try:
        await context.bot.send_message(chat_id=user_id, text=REMOVED_SAFE_TEXT)
    except Exception:
        pass

    await query.edit_message_text((query.message.text or "") + "\n🗑 Removed from SAFE.")

async def admin_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # /remove <user_id>
    if not update.effective_user or update.effective_user.id != ADMIN_ID_INT:
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    if not update.message:
        return

    args = context.args or []
    if len(args) != 1 or not args[0].isdigit():
        await update.message.reply_text("Usage: /remove <user_id>")
        return

    user_id = int(args[0])

    await set_status(pool, user_id, "NEW")
    await set_state(pool, user_id, None)

    try:
        await context.bot.send_message(chat_id=user_id, text=REMOVED_SAFE_TEXT)
    except Exception:
        pass

    await update.message.reply_text("✅ Removed from SAFE list.")

def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("remove", admin_remove_command))

    app.add_handler(CallbackQueryHandler(on_verify, pattern=r"^verify$"))
    app.add_handler(CallbackQueryHandler(admin_decision, pattern=r"^adm:(acc|dec):\d+$"))
    app.add_handler(CallbackQueryHandler(admin_remove_callback, pattern=r"^adm:rem:\d+$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
