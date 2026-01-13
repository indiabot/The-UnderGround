import os
import re
import html
import datetime
from typing import Optional, Dict, Any

import asyncpg
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
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
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
ADMIN_ID = (os.getenv("ADMIN_ID") or "").strip()

# Optional (either URL or local file path)
HOME_PHOTO = (os.getenv("HOME_PHOTO") or "").strip()  # e.g. "home.png" or "https://..."
CLAIM_PHOTO = (os.getenv("CLAIM_PHOTO") or "").strip()  # optional admin claim picture

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")
if not ADMIN_ID.isdigit():
    raise RuntimeError("ADMIN_ID must be numeric")

ADMIN_ID_INT = int(ADMIN_ID)

# ================== DB ==================
CREATE_USERS_SQL = """
CREATE TABLE IF NOT EXISTS users (
  user_id BIGINT PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  last_name TEXT,
  language TEXT NOT NULL DEFAULT 'et',
  status TEXT NOT NULL DEFAULT 'NEW',      -- NEW | PENDING | SAFE | DECLINED
  state TEXT,                              -- WAITING_REF
  banned BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_CLAIMS_SQL = """
CREATE TABLE IF NOT EXISTS claims (
  id SERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  ref_username TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING | ACCEPTED | DECLINED
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_claims_user_id ON claims(user_id);
CREATE INDEX IF NOT EXISTS idx_claims_ref_lower ON claims(lower(ref_username));
"""

CREATE_SETTINGS_SQL = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""

# ================== TEXTS ==================
TEXTS: Dict[str, Dict[str, str]] = {
    "et": {
        "title": "The UndeGround Market",
        "home": "<b>The UndeGround Market</b>\n\n{op_status}",
        "op_online": "✅ Operator: ONLINE",
        "op_offline": "⛔ Operator: OFFLINE",
        "menu_help": "ℹ️ Help",
        "menu_account": "👤 Account",
        "menu_verify": "✅ Verify",
        "menu_home": "🏠 Home",
        "menu_lang": "🌐 Language",
        "help_text": "Help:\n- Press Verify to request access\n- Use language button to change language",
        "account_text": "Account",
        "banned": "⛔ Sul puudub ligipääs botile.",
        "wait_ref": "Kirjuta siia @ref (näiteks @username).",
        "invalid_ref": "❌ Vale @. Kirjuta täpselt nagu @kasutaja (ilma tühikuteta).",
        "already_pending": "⏳ Oled juba pending. Oota admini otsust.",
        "wait_admin": "⏳ Oota admini otsust.",
        "accepted": "✅ Accepted. Sul on nüüd ligipääs.",
        "declined": "❌ Declined.",
        "not_allowed": "Not allowed.",
        "admin_usage": "Usage: {cmd} <user_id|@username>",
        "user_not_found": "❌ User not found in DB.",
        "done": "✅ Done.",
        "list_safe": "SAFE list",
        "list_ban": "BAN list",
        "search_title": "SEARCH RESULT",
        "status": "Status",
        "language": "Language",
        "banned_flag": "Banned",
        "accepted_referrals": "Verify accepted via this @",
        "ref_safe": "Referral SAFE",
        "ref_yes": "YES",
        "ref_no": "NO",
        "op_set_online": "✅ Operator set ONLINE",
        "op_set_offline": "⛔ Operator set OFFLINE",
    },
    "en": {
        "title": "The UndeGround Market",
        "home": "<b>The UndeGround Market</b>\n\n{op_status}",
        "op_online": "✅ Operator: ONLINE",
        "op_offline": "⛔ Operator: OFFLINE",
        "menu_help": "ℹ️ Help",
        "menu_account": "👤 Account",
        "menu_verify": "✅ Verify",
        "menu_home": "🏠 Home",
        "menu_lang": "🌐 Language",
        "help_text": "Help:\n- Press Verify to request access\n- Use language button to change language",
        "account_text": "Account",
        "banned": "⛔ You are banned from using this bot.",
        "wait_ref": "Send @ref (example: @username).",
        "invalid_ref": "❌ Invalid @. Send exactly @username (no spaces).",
        "already_pending": "⏳ You are already pending. Wait for admin decision.",
        "wait_admin": "⏳ Wait for admin decision.",
        "accepted": "✅ Accepted. You now have access.",
        "declined": "❌ Declined.",
        "not_allowed": "Not allowed.",
        "admin_usage": "Usage: {cmd} <user_id|@username>",
        "user_not_found": "❌ User not found in DB.",
        "done": "✅ Done.",
        "list_safe": "SAFE list",
        "list_ban": "BAN list",
        "search_title": "SEARCH RESULT",
        "status": "Status",
        "language": "Language",
        "banned_flag": "Banned",
        "accepted_referrals": "Verify accepted via this @",
        "ref_safe": "Referral SAFE",
        "ref_yes": "YES",
        "ref_no": "NO",
        "op_set_online": "✅ Operator set ONLINE",
        "op_set_offline": "⛔ Operator set OFFLINE",
    },
    "ru": {
        "title": "The UndeGround Market",
        "home": "<b>The UndeGround Market</b>\n\n{op_status}",
        "op_online": "✅ Оператор: ONLINE",
        "op_offline": "⛔ Оператор: OFFLINE",
        "menu_help": "ℹ️ Help",
        "menu_account": "👤 Account",
        "menu_verify": "✅ Verify",
        "menu_home": "🏠 Home",
        "menu_lang": "🌐 Language",
        "help_text": "Help:\n- Press Verify to request access\n- Use language button to change language",
        "account_text": "Account",
        "banned": "⛔ Вам запрещено пользоваться ботом.",
        "wait_ref": "Отправь @ref (пример: @username).",
        "invalid_ref": "❌ Неверный @. Отправь @username (без пробелов).",
        "already_pending": "⏳ Ты уже pending. Жди решения админа.",
        "wait_admin": "⏳ Жди решения админа.",
        "accepted": "✅ Принято. Теперь у тебя есть доступ.",
        "declined": "❌ Отклонено.",
        "not_allowed": "Not allowed.",
        "admin_usage": "Usage: {cmd} <user_id|@username>",
        "user_not_found": "❌ Пользователь не найден в базе.",
        "done": "✅ Done.",
        "list_safe": "SAFE list",
        "list_ban": "BAN list",
        "search_title": "SEARCH RESULT",
        "status": "Status",
        "language": "Language",
        "banned_flag": "Banned",
        "accepted_referrals": "Verify accepted via this @",
        "ref_safe": "Referral SAFE",
        "ref_yes": "YES",
        "ref_no": "NO",
        "op_set_online": "✅ Оператор ONLINE",
        "op_set_offline": "⛔ Оператор OFFLINE",
    },
}


def esc(s: str) -> str:
    return html.escape(s or "")


def t(lang: str, key: str) -> str:
    lang = lang if lang in TEXTS else "et"
    return TEXTS[lang].get(key, TEXTS["et"].get(key, key))


def is_admin(user_id: int) -> bool:
    return int(user_id) == int(ADMIN_ID_INT)


def normalize_username(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    if u.startswith("@"):
        u = u[1:]
    return u.lower()


def validate_ref(text: str) -> bool:
    if not text:
        return False
    if not text.startswith("@"):
        return False
    if " " in text:
        return False
    if len(text) < 2:
        return False
    return bool(re.match(r"^@[A-Za-z0-9_]{2,64}$", text))


# ================== DB HELPERS ==================
async def db_pool(app: Application) -> asyncpg.Pool:
    return app.bot_data["db_pool"]


async def on_startup(app: Application) -> None:
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    app.bot_data["db_pool"] = pool
    async with pool.acquire() as conn:
        await conn.execute(CREATE_USERS_SQL)
        await conn.execute(CREATE_CLAIMS_SQL)
        await conn.execute(CREATE_SETTINGS_SQL)
        # default operator flag
        await conn.execute(
            "INSERT INTO settings(key,value) VALUES('operator_online','0') "
            "ON CONFLICT (key) DO NOTHING"
        )


async def on_shutdown(app: Application) -> None:
    pool: asyncpg.Pool = app.bot_data.get("db_pool")
    if pool:
        await pool.close()


async def upsert_user(pool: asyncpg.Pool, user) -> None:
    await pool.execute(
        """
        INSERT INTO users(user_id, username, first_name, last_name, updated_at)
        VALUES($1, $2, $3, $4, now())
        ON CONFLICT (user_id) DO UPDATE SET
          username=EXCLUDED.username,
          first_name=EXCLUDED.first_name,
          last_name=EXCLUDED.last_name,
          updated_at=now()
        """,
        int(user.id),
        (user.username or None),
        (user.first_name or None),
        (user.last_name or None),
    )


async def get_user(pool: asyncpg.Pool, user_id: int) -> Optional[Dict[str, Any]]:
    row = await pool.fetchrow("SELECT * FROM users WHERE user_id=$1", int(user_id))
    return dict(row) if row else None


async def get_user_by_username(pool: asyncpg.Pool, username: str) -> Optional[Dict[str, Any]]:
    u = normalize_username(username)
    if not u:
        return None
    row = await pool.fetchrow("SELECT * FROM users WHERE lower(username)=lower($1)", u)
    return dict(row) if row else None


async def set_language(pool: asyncpg.Pool, user_id: int, lang: str) -> None:
    if lang not in TEXTS:
        lang = "et"
    await pool.execute(
        "UPDATE users SET language=$1, updated_at=now() WHERE user_id=$2",
        lang,
        int(user_id),
    )


async def set_status(pool: asyncpg.Pool, user_id: int, status: str) -> None:
    await pool.execute(
        "UPDATE users SET status=$1, updated_at=now() WHERE user_id=$2",
        status,
        int(user_id),
    )


async def set_state(pool: asyncpg.Pool, user_id: int, state: Optional[str]) -> None:
    await pool.execute(
        "UPDATE users SET state=$1, updated_at=now() WHERE user_id=$2",
        state,
        int(user_id),
    )


async def is_banned(pool: asyncpg.Pool, user_id: int) -> bool:
    row = await pool.fetchrow("SELECT banned FROM users WHERE user_id=$1", int(user_id))
    return bool(row and row["banned"])


async def set_banned(pool: asyncpg.Pool, user_id: int, banned: bool) -> None:
    await pool.execute(
        "UPDATE users SET banned=$1, updated_at=now() WHERE user_id=$2",
        bool(banned),
        int(user_id),
    )


async def create_claim(pool: asyncpg.Pool, user_id: int, ref_username: str) -> int:
    row = await pool.fetchrow(
        "INSERT INTO claims(user_id, ref_username, status) VALUES($1,$2,'PENDING') RETURNING id",
        int(user_id),
        ref_username.strip(),
    )
    return int(row["id"])


async def get_claim(pool: asyncpg.Pool, claim_id: int) -> Optional[Dict[str, Any]]:
    row = await pool.fetchrow("SELECT * FROM claims WHERE id=$1", int(claim_id))
    return dict(row) if row else None


async def decide_claim(pool: asyncpg.Pool, claim_id: int, status: str) -> None:
    await pool.execute(
        "UPDATE claims SET status=$1, decided_at=now() WHERE id=$2",
        status,
        int(claim_id),
    )


async def count_accepted_referrals(pool: asyncpg.Pool, ref_username: str) -> int:
    ref = ref_username.strip()
    if not ref.startswith("@"):
        ref = "@" + ref
    row = await pool.fetchrow(
        "SELECT COUNT(*) AS c FROM claims WHERE status='ACCEPTED' AND lower(ref_username)=lower($1)",
        ref,
    )
    return int(row["c"] if row else 0)


async def is_ref_safe(pool: asyncpg.Pool, ref_username: str) -> bool:
    u = normalize_username(ref_username)
    if not u:
        return False
    row = await pool.fetchrow(
        "SELECT 1 FROM users WHERE lower(username)=lower($1) AND status='SAFE' AND banned=false",
        u,
    )
    return bool(row)


async def get_operator_online(pool: asyncpg.Pool) -> bool:
    row = await pool.fetchrow("SELECT value FROM settings WHERE key='operator_online'")
    return bool(row and (row["value"] == "1"))


async def set_operator_online(pool: asyncpg.Pool, online: bool) -> None:
    await pool.execute(
        "INSERT INTO settings(key,value) VALUES('operator_online',$1) "
        "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
        "1" if online else "0",
    )


async def resolve_user_id(pool: asyncpg.Pool, token: str) -> Optional[int]:
    s = (token or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s.startswith("@"):
        u = await get_user_by_username(pool, s)
        return int(u["user_id"]) if u else None
    return None


# ================== UI HELPERS ==================
def kb_languages() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🇪🇪 ET", callback_data="lang:et"),
                InlineKeyboardButton("🇬🇧 EN", callback_data="lang:en"),
                InlineKeyboardButton("🇷🇺 RU", callback_data="lang:ru"),
            ]
        ]
    )


def kb_main(lang: str, status: str) -> InlineKeyboardMarkup:
    rows = []
    rows.append(
        [
            InlineKeyboardButton(t(lang, "menu_home"), callback_data="nav:home"),
            InlineKeyboardButton(t(lang, "menu_lang"), callback_data="nav:lang"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(t(lang, "menu_account"), callback_data="nav:account"),
            InlineKeyboardButton(t(lang, "menu_help"), callback_data="nav:help"),
        ]
    )
    if status in ("NEW", "DECLINED"):
        rows.append([InlineKeyboardButton(t(lang, "menu_verify"), callback_data="nav:verify")])
    return InlineKeyboardMarkup(rows)


def kb_admin_claim(claim_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Accept", callback_data=f"adm_claim:acc:{claim_id}"),
                InlineKeyboardButton("❌ Decline", callback_data=f"adm_claim:dec:{claim_id}"),
            ],
            [
                InlineKeyboardButton("🚫 Ban", callback_data=f"adm_claim:ban:{claim_id}"),
            ],
        ]
    )


async def edit_ui(query, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None) -> None:
    msg = query.message
    is_photo = bool(msg and getattr(msg, "photo", None))
    if is_photo:
        await query.edit_message_caption(caption=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def send_home_message(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str, status: str) -> None:
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    online = await get_operator_online(pool)
    op_status = t(lang, "op_online") if online else t(lang, "op_offline")
    text = t(lang, "home").format(op_status=esc(op_status))

    if update.message:
        chat_id = update.message.chat_id
        # send photo if provided, else text
        if HOME_PHOTO:
            try:
                if HOME_PHOTO.startswith("http://") or HOME_PHOTO.startswith("https://"):
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=HOME_PHOTO,
                        caption=text,
                        reply_markup=kb_main(lang, status),
                        parse_mode=ParseMode.HTML,
                    )
                    return
                else:
                    # local file
                    with open(HOME_PHOTO, "rb") as f:
                        await context.bot.send_photo(
                            chat_id=chat_id,
                            photo=f,
                            caption=text,
                            reply_markup=kb_main(lang, status),
                            parse_mode=ParseMode.HTML,
                        )
                    return
            except Exception:
                pass

        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=kb_main(lang, status),
            parse_mode=ParseMode.HTML,
        )


async def render_account(pool: asyncpg.Pool, lang: str, user_id: int) -> str:
    u = await get_user(pool, user_id)
    uname = f"@{u['username']}" if u and u.get("username") else "(no username)"
    return (
        f"<b>{esc(t(lang,'account_text'))}</b>\n\n"
        f"User ID: <code>{user_id}</code>\n"
        f"Username: <code>{esc(uname)}</code>\n"
        f"{esc(t(lang,'status'))}: <code>{esc(u['status'] if u else 'NEW')}</code>\n"
        f"{esc(t(lang,'language'))}: <code>{esc(u['language'] if u else lang)}</code>\n"
    )


async def render_home(pool: asyncpg.Pool, lang: str) -> str:
    online = await get_operator_online(pool)
    op_status = t(lang, "op_online") if online else t(lang, "op_offline")
    return t(lang, "home").format(op_status=esc(op_status))


# ================== COMMANDS ==================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        return
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    user = update.effective_user

    await upsert_user(pool, user)
    dbu = await get_user(pool, user.id)
    lang = (dbu.get("language") if dbu else "et") or "et"
    status = (dbu.get("status") if dbu else "NEW") or "NEW"

    if await is_banned(pool, user.id):
        await update.message.reply_text(t(lang, "banned"))
        return

    await send_home_message(update, context, lang, status)


async def admin_add_safe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not is_admin(update.effective_user.id):
        return
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    if not context.args:
        await update.message.reply_text(t("et", "admin_usage").format(cmd="/add"))
        return
    uid = await resolve_user_id(pool, context.args[0])
    if not uid:
        await update.message.reply_text(t("et", "user_not_found"))
        return
    await set_status(pool, uid, "SAFE")
    await update.message.reply_text(t("et", "done"))


async def admin_remove_safe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not is_admin(update.effective_user.id):
        return
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    if not context.args:
        await update.message.reply_text(t("et", "admin_usage").format(cmd="/remove"))
        return
    uid = await resolve_user_id(pool, context.args[0])
    if not uid:
        await update.message.reply_text(t("et", "user_not_found"))
        return
    await set_status(pool, uid, "DECLINED")
    await update.message.reply_text(t("et", "done"))


async def admin_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not is_admin(update.effective_user.id):
        return
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    if not context.args:
        await update.message.reply_text(t("et", "admin_usage").format(cmd="/ban"))
        return
    uid = await resolve_user_id(pool, context.args[0])
    if not uid:
        await update.message.reply_text(t("et", "user_not_found"))
        return
    await set_banned(pool, uid, True)
    await update.message.reply_text(t("et", "done"))


async def admin_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not is_admin(update.effective_user.id):
        return
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    if not context.args:
        await update.message.reply_text(t("et", "admin_usage").format(cmd="/unban"))
        return
    uid = await resolve_user_id(pool, context.args[0])
    if not uid:
        await update.message.reply_text(t("et", "user_not_found"))
        return
    await set_banned(pool, uid, False)
    await update.message.reply_text(t("et", "done"))


async def admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not is_admin(update.effective_user.id):
        return
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    arg = (context.args[0].lower() if context.args else "safe")
    if arg not in ("safe", "ban"):
        arg = "safe"

    if arg == "safe":
        rows = await pool.fetch(
            "SELECT user_id, username FROM users WHERE status='SAFE' AND banned=false ORDER BY updated_at DESC LIMIT 200"
        )
        lines = [f"{t('et','list_safe')}: {len(rows)}"]
    else:
        rows = await pool.fetch(
            "SELECT user_id, username FROM users WHERE banned=true ORDER BY updated_at DESC LIMIT 200"
        )
        lines = [f"{t('et','list_ban')}: {len(rows)}"]

    for r in rows:
        uname = f"@{r['username']}" if r["username"] else "(no username)"
        lines.append(f"- {r['user_id']} {uname}")
    await update.message.reply_text("\n".join(lines))


async def admin_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not is_admin(update.effective_user.id):
        return
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    if not context.args:
        await update.message.reply_text(t("et", "admin_usage").format(cmd="/search"))
        return
    token = context.args[0].strip()
    u: Optional[Dict[str, Any]] = None
    if token.isdigit():
        u = await get_user(pool, int(token))
    elif token.startswith("@"):
        u = await get_user_by_username(pool, token)

    if not u:
        await update.message.reply_text(t("et", "user_not_found"))
        return

    lang = u.get("language") or "et"
    uname = f"@{u['username']}" if u.get("username") else "(no username)"
    accepted_ref = 0
    if u.get("username"):
        accepted_ref = await count_accepted_referrals(pool, f"@{u['username']}")

    msg = (
        f"<b>{esc(t(lang,'search_title'))}</b>\n\n"
        f"User ID: <code>{u['user_id']}</code>\n"
        f"Username: <code>{esc(uname)}</code>\n"
        f"{esc(t(lang,'status'))}: <code>{esc(u.get('status',''))}</code>\n"
        f"{esc(t(lang,'language'))}: <code>{esc(lang)}</code>\n"
        f"{esc(t(lang,'banned_flag'))}: <code>{'YES' if u.get('banned') else 'NO'}</code>\n"
        f"{esc(t(lang,'accepted_referrals'))}: <code>{accepted_ref}</code>\n"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def admin_online(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not is_admin(update.effective_user.id):
        return
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    await set_operator_online(pool, True)
    await update.message.reply_text(t("et", "op_set_online"))


async def admin_offline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user or not is_admin(update.effective_user.id):
        return
    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    await set_operator_online(pool, False)
    await update.message.reply_text(t("et", "op_set_offline"))


# ================== CALLBACKS ==================
async def nav_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    user = update.effective_user

    await upsert_user(pool, user)
    dbu = await get_user(pool, user.id)
    lang = (dbu.get("language") if dbu else "et") or "et"
    status = (dbu.get("status") if dbu else "NEW") or "NEW"
    state = (dbu.get("state") if dbu else None)

    if await is_banned(pool, user.id):
        await edit_ui(query, t(lang, "banned"), reply_markup=None)
        return

    data = query.data or ""
    _, page = data.split(":", 1)

    if page == "lang":
        await edit_ui(query, esc(t(lang, "choose_lang")), reply_markup=kb_languages())
        return

    if page == "home":
        text = await render_home(pool, lang)
        await edit_ui(query, text, reply_markup=kb_main(lang, status))
        return

    if page == "help":
        await edit_ui(query, esc(t(lang, "help_text")).replace("\n", "\n"), reply_markup=kb_main(lang, status))
        return

    if page == "account":
        text = await render_account(pool, lang, user.id)
        await edit_ui(query, text, reply_markup=kb_main(lang, status))
        return

    if page == "verify":
        if status == "PENDING":
            await edit_ui(query, esc(t(lang, "already_pending")), reply_markup=kb_main(lang, status))
            return
        if status == "SAFE":
            # already safe - show home
            text = await render_home(pool, lang)
            await edit_ui(query, text, reply_markup=kb_main(lang, status))
            return
        await set_state(pool, user.id, "WAITING_REF")
        await edit_ui(query, esc(t(lang, "wait_ref")), reply_markup=kb_languages())
        return

    # fallback
    text = await render_home(pool, lang)
    await edit_ui(query, text, reply_markup=kb_main(lang, status))


async def lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    user = update.effective_user

    await upsert_user(pool, user)
    dbu = await get_user(pool, user.id)
    current_lang = (dbu.get("language") if dbu else "et") or "et"
    status = (dbu.get("status") if dbu else "NEW") or "NEW"
    state = (dbu.get("state") if dbu else None)

    if await is_banned(pool, user.id):
        await edit_ui(query, t(current_lang, "banned"), reply_markup=None)
        return

    data = query.data or ""
    _, new_lang = data.split(":", 1)
    if new_lang not in TEXTS:
        new_lang = "et"

    await set_language(pool, user.id, new_lang)

    # re-render based on state/status
    dbu2 = await get_user(pool, user.id)
    status2 = (dbu2.get("status") if dbu2 else status) or status
    state2 = (dbu2.get("state") if dbu2 else state)

    if state2 == "WAITING_REF":
        await edit_ui(query, esc(t(new_lang, "wait_ref")), reply_markup=kb_languages())
        return

    if status2 == "PENDING":
        await edit_ui(query, esc(t(new_lang, "wait_admin")), reply_markup=kb_languages())
        return

    text = await render_home(pool, new_lang)
    await edit_ui(query, text, reply_markup=kb_main(new_lang, status2))


async def admin_claim_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not update.effective_user:
        return
    await query.answer()

    if not is_admin(update.effective_user.id):
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

    if claim["status"] in ("ACCEPTED", "DECLINED"):
        await query.edit_message_text(f"Already decided: {claim['status']}")
        return

    target_user_id = int(claim["user_id"])
    target_user = await get_user(pool, target_user_id)
    target_lang = (target_user.get("language") if target_user else "et") or "et"

    if action == "ban":
        await set_banned(pool, target_user_id, True)
        await decide_claim(pool, claim_id, "DECLINED")
        await set_status(pool, target_user_id, "DECLINED")
        await set_state(pool, target_user_id, None)
        try:
            await context.bot.send_message(chat_id=target_user_id, text=t(target_lang, "banned"))
        except Exception:
            pass
        await query.edit_message_text((query.message.text or "") + "\n\n🚫 BANNED")
        return

    if action == "acc":
        await decide_claim(pool, claim_id, "ACCEPTED")
        await set_status(pool, target_user_id, "SAFE")
        await set_state(pool, target_user_id, None)
        try:
            await context.bot.send_message(chat_id=target_user_id, text=t(target_lang, "accepted"))
        except Exception:
            pass
        await query.edit_message_text((query.message.text or "") + "\n\n✅ ACCEPTED")
        return

    if action == "dec":
        await decide_claim(pool, claim_id, "DECLINED")
        await set_status(pool, target_user_id, "DECLINED")
        await set_state(pool, target_user_id, None)
        try:
            await context.bot.send_message(chat_id=target_user_id, text=t(target_lang, "declined"))
        except Exception:
            pass
        await query.edit_message_text((query.message.text or "") + "\n\n❌ DECLINED")
        return

    await query.edit_message_text("Unknown action.")


# ================== TEXT HANDLER ==================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text or not update.effective_user:
        return

    pool: asyncpg.Pool = context.application.bot_data["db_pool"]
    user = update.effective_user
    text = update.message.text.strip()

    await upsert_user(pool, user)
    dbu = await get_user(pool, user.id)
    lang = (dbu.get("language") if dbu else "et") or "et"
    status = (dbu.get("status") if dbu else "NEW") or "NEW"
    state = (dbu.get("state") if dbu else None)

    if await is_banned(pool, user.id):
        await update.message.reply_text(t(lang, "banned"))
        return

    if status == "PENDING":
        await update.message.reply_text(t(lang, "already_pending"), reply_markup=kb_languages())
        return

    if state == "WAITING_REF":
        if not validate_ref(text):
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

        ref_safe = await is_ref_safe(pool, ref_username)
        ref_safe_txt = t("et", "ref_yes") if ref_safe else t("et", "ref_no")

        accepted_count = await count_accepted_referrals(pool, ref_username)

        admin_text = (
            "NEW CLAIM\n\n"
            f"User ID: {user.id}\n"
            f"Name: {full_name or '(no name)'}\n"
            f"Username: {uname}\n"
            f"Referral: {ref_username}\n"
            f"Referral SAFE: {ref_safe_txt}\n"
            f"Referral accepted count: {accepted_count}\n"
            f"Date: {now_utc}\n"
            f"Claim ID: {claim_id}\n"
        )

        # optional claim photo
        if CLAIM_PHOTO:
            try:
                if CLAIM_PHOTO.startswith("http://") or CLAIM_PHOTO.startswith("https://"):
                    await context.bot.send_photo(
                        chat_id=ADMIN_ID_INT,
                        photo=CLAIM_PHOTO,
                        caption=admin_text,
                        reply_markup=kb_admin_claim(claim_id),
                    )
                else:
                    with open(CLAIM_PHOTO, "rb") as f:
                        await context.bot.send_photo(
                            chat_id=ADMIN_ID_INT,
                            photo=f,
                            caption=admin_text,
                            reply_markup=kb_admin_claim(claim_id),
                        )
            except Exception:
                await context.bot.send_message(
                    chat_id=ADMIN_ID_INT,
                    text=admin_text,
                    reply_markup=kb_admin_claim(claim_id),
                )
        else:
            await context.bot.send_message(
                chat_id=ADMIN_ID_INT,
                text=admin_text,
                reply_markup=kb_admin_claim(claim_id),
            )
        return

    # if SAFE/NEW/DECLINED and user writes random text, show language buttons only
    await update.message.reply_text(t(lang, "choose_lang"), reply_markup=kb_languages())


# ================== MAIN ==================
def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    app.post_init = on_startup
    app.post_shutdown = on_shutdown

    # user
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(lang_callback, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(nav_callback, pattern=r"^nav:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # admin
    app.add_handler(CommandHandler("add", admin_add_safe))
    app.add_handler(CommandHandler("remove", admin_remove_safe))
    app.add_handler(CommandHandler("ban", admin_ban))
    app.add_handler(CommandHandler("unban", admin_unban))
    app.add_handler(CommandHandler("list", admin_list))
    app.add_handler(CommandHandler("search", admin_search))
    app.add_handler(CommandHandler("online", admin_online))
    app.add_handler(CommandHandler("offline", admin_offline))
    app.add_handler(CallbackQueryHandler(admin_claim_decision, pattern=r"^adm_claim:"))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
