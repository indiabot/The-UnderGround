import os
import asyncpg
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN puudub (Railway Variables -> BOT_TOKEN)")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL puudub (Railway Postgres plugin)")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS messages (
  id SERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  user_id BIGINT,
  username TEXT,
  text TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
"""

# 1 nupp klaviatuuril
MENU_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton("Proovi")]],
    resize_keyboard=True,
    one_time_keyboard=False
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
    await update.message.reply_text(
        "Vali menüüst nupp 👇",
        reply_markup=MENU_KEYBOARD
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    text = update.message.text
    chat_id = update.message.chat_id
    user = update.message.from_user
    user_id = user.id if user else None
    username = user.username if user else None

    # Salvesta DB-sse
    pool = context.application.bot_data["db_pool"]
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO messages (chat_id, user_id, username, text) VALUES ($1, $2, $3, $4)",
            chat_id, user_id, username, text
        )

    # Kui vajutati nuppu
    if text == "Proovi":
        await update.message.reply_text("✅ Proovi nupp töötab!", reply_markup=MENU_KEYBOARD)
        return

    # Muu tekst
    await update.message.reply_text("Kirjuta /start et näha menüüd.", reply_markup=MENU_KEYBOARD)

def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
