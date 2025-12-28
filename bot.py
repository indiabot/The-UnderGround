import os
import asyncpg
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

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

async def on_startup(app: Application) -> None:
    # Loo connection pool ja salvesta bot_data sisse
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    app.bot_data["db_pool"] = pool

    # Tee tabel kui puudub
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLE_SQL)

async def on_shutdown(app: Application) -> None:
    pool = app.bot_data.get("db_pool")
    if pool:
        await pool.close()

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

    # Test vastus
    await update.message.reply_text("✅ Test: bot töötab!")

def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(on_startup)      # käivitub enne pollingut
        .post_shutdown(on_shutdown) # sulgeb pooli korralikult
        .build()
    )

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
