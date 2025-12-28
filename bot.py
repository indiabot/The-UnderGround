import os
import asyncpg
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
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

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN puudub (Railway Variables -> BOT_TOKEN)")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL puudub (Railway PostgreSQL plugin)")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS events (
  id SERIAL PRIMARY KEY,
  event_type TEXT NOT NULL,              -- 'message' või 'button'
  chat_id BIGINT NOT NULL,
  user_id BIGINT,
  username TEXT,
  payload TEXT,                          -- message text või button callback_data
  created_at TIMESTAMPTZ DEFAULT now()
);
"""

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🧪 Proovi", callback_data="btn_proovi")]]
    )

async def db_log(app: Application, event_type: str, chat_id: int, user_id, username, payload: str):
    pool: asyncpg.Pool = app.bot_data["db_pool"]
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO events (event_type, chat_id, user_id, username, payload) VALUES ($1, $2, $3, $4, $5)",
            event_type, chat_id, user_id, username, payload
        )

async def on_startup(app: Application) -> None:
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    app.bot_data["db_pool"] = pool
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLE_SQL)

async def on_shutdown(app: Application) -> None:
    pool = app.bot_data.get("db_pool")
    if pool:
        await pool.close()

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat

    # Logime /start kui "message" (payload: /start)
    await db_log(
        context.application,
        "message",
        chat.id,
        user.id if user else None,
        user.username if user else None,
        "/start",
    )

    await update.message.reply_text(
        "Menüü (nupp on sõnumi all):",
        reply_markup=main_menu(),
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    chat = update.effective_chat
    text = update.message.text

    # Salvesta sõnum DB-sse
    await db_log(
        context.application,
        "message",
        chat.id,
        user.id if user else None,
        user.username if user else None,
        text,
    )

    # Lihtne test vastus + näita menüüd uuesti
    await update.message.reply_text(
        "✅ Test: bot töötab! Vajuta all nuppu 👇",
        reply_markup=main_menu(),
    )

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()  # vajalik, et Telegram lõpetaks “loading” animatsiooni

    user = update.effective_user
    chat = update.effective_chat
    data = query.data or ""

    # Logi nupuvajutus DB-sse
    await db_log(
        context.application,
        "button",
        chat.id,
        user.id if user else None,
        user.username if user else None,
        data,
    )

    if data == "btn_proovi":
        # Muudame sama sõnumi teksti (edit), et oleks ilus
        await query.edit_message_text(
            "✅ Proovi nupp töötab!\n\nKirjuta midagi või vajuta /start, et menüüd uuesti näha."
        )
    else:
        await query.edit_message_text("Tundmatu nupp 🤷")

def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
