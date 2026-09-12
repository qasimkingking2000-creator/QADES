#!/usr/bin/env python3
# name=main.py
"""
Loki — كامل (Menu, Welcome+Captcha, Triggers, Ranks, Random Titles, Banking, Purge, Basic File Tools, DevStats)
- Read config from bot_config.json or environment variables
- SQLite persistent storage (loki.db by default)
- NOTE: Voice/music (in-group calls) needs Pyrogram + pytgcalls + user session (not included here)
"""
import os
import json
import time
import logging
import random
import sqlite3
import asyncio
import html
from typing import Any, Dict, List, Optional

import aiohttp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatPermissions,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    filters,
)

# --------------------
# Config
# --------------------
CONFIG_FILE = "bot_config.json"
DEFAULT_CONFIG = {
    "BOT_TOKEN": None,
    "BOT_NAME": "لوكي",
    "OWNER_ID": None,
    "SUDO_IDS": [],
    "DB_PATH": "loki.db",
}

def load_config() -> Dict[str, Any]:
    cfg = DEFAULT_CONFIG.copy()
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg.update({k: v for k, v in data.items() if v is not None})
    except Exception:
        pass
    # env overrides
    if os.environ.get("BOT_TOKEN"):
        cfg["BOT_TOKEN"] = os.environ.get("BOT_TOKEN")
    if os.environ.get("BOT_NAME"):
        cfg["BOT_NAME"] = os.environ.get("BOT_NAME")
    if os.environ.get("OWNER_ID"):
        try:
            cfg["OWNER_ID"] = int(os.environ.get("OWNER_ID"))
        except Exception:
            pass
    if os.environ.get("SUDO_IDS"):
        try:
            cfg["SUDO_IDS"] = [int(x.strip()) for x in os.environ.get("SUDO_IDS").split(",") if x.strip()]
        except Exception:
            pass
    if os.environ.get("LOKI_DB"):
        cfg["DB_PATH"] = os.environ.get("LOKI_DB")
    return cfg

config = load_config()
BOT_TOKEN = config.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not found in bot_config.json or environment")
BOT_NAME = config.get("BOT_NAME", "لوكي")
OWNER_ID = config.get("OWNER_ID")
SUDO_IDS = config.get("SUDO_IDS", [])
DB_PATH = config.get("DB_PATH", "loki.db")

# --------------------
# Logging
# --------------------
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("loki.full")

# --------------------
# DB
# --------------------
def get_conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    # basic members & stats
    cur.execute("""
    CREATE TABLE IF NOT EXISTS members (
        chat_id INTEGER,
        user_id INTEGER,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        first_seen INTEGER,
        last_seen INTEGER,
        is_bot INTEGER DEFAULT 0,
        PRIMARY KEY (chat_id, user_id)
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_stats (
        chat_id INTEGER,
        user_id INTEGER,
        messages_count INTEGER DEFAULT 0,
        reactions_count INTEGER DEFAULT 0,
        last_seen INTEGER,
        stolen_count INTEGER DEFAULT 0,
        balance INTEGER DEFAULT 0,
        PRIMARY KEY (chat_id, user_id)
    )""")
    # triggers
    cur.execute("""
    CREATE TABLE IF NOT EXISTS triggers (
        chat_id INTEGER,
        trigger_text TEXT,
        response_type TEXT,
        response_text TEXT,
        is_regex INTEGER DEFAULT 0,
        PRIMARY KEY (chat_id, trigger_text)
    )""")
    # ranks
    cur.execute("""
    CREATE TABLE IF NOT EXISTS ranks (
        chat_id INTEGER,
        rank_name TEXT,
        min_messages INTEGER,
        badge_file_id TEXT,
        PRIMARY KEY (chat_id, rank_name)
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_ranks (
        chat_id INTEGER,
        user_id INTEGER,
        rank_name TEXT,
        assigned_at INTEGER,
        PRIMARY KEY (chat_id, user_id)
    )""")
    # random titles
    cur.execute("""
    CREATE TABLE IF NOT EXISTS random_titles_settings (
        chat_id INTEGER PRIMARY KEY,
        enabled INTEGER DEFAULT 0,
        percent INTEGER DEFAULT 10
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS random_titles_pool (
        chat_id INTEGER,
        title TEXT,
        PRIMARY KEY (chat_id, title)
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS random_titles_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        user_id INTEGER,
        title TEXT,
        assigned_at INTEGER
    )""")
    # welcome & captcha
    cur.execute("""
    CREATE TABLE IF NOT EXISTS welcome_settings (
        chat_id INTEGER PRIMARY KEY,
        enabled INTEGER DEFAULT 0,
        welcome_type TEXT DEFAULT 'text',
        welcome_text TEXT,
        welcome_media_file_id TEXT,
        captcha_type TEXT DEFAULT 'button',
        captcha_timeout INTEGER DEFAULT 60
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS pending_captcha (
        chat_id INTEGER,
        user_id INTEGER,
        code TEXT,
        expires_at INTEGER,
        msg_id INTEGER,
        PRIMARY KEY (chat_id, user_id)
    )""")
    # purge logs
    cur.execute("""
    CREATE TABLE IF NOT EXISTS purge_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        admin_id INTEGER,
        deleted_count INTEGER,
        reason TEXT,
        created_at INTEGER
    )""")
    # banking
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bank_accounts (
        chat_id INTEGER,
        user_id INTEGER,
        bank_name TEXT,
        account_id TEXT,
        balance INTEGER DEFAULT 0,
        PRIMARY KEY (chat_id, user_id, bank_name)
    )""")
    conn.commit()
    conn.close()
    logger.info("DB initialized at %s", DB_PATH)

# --------------------
# Utilities
# --------------------
async def is_chat_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    try:
        m = await context.bot.get_chat_member(chat_id, user_id)
        return m.status in ("administrator", "creator")
    except Exception:
        return False

def is_owner_or_sudo(user_id: int) -> bool:
    if OWNER_ID and user_id == OWNER_ID:
        return True
    if user_id in SUDO_IDS:
        return True
    return False

def mention_from_row(row: sqlite3.Row) -> str:
    # row may contain username/first_name
    if row is None:
        return "(unknown)"
    name = row.get("first_name") or row.get("username") or str(row.get("user_id"))
    uid = row.get("user_id")
    # HTML mention
    return f'<a href="tg://user?id={uid}">{html.escape(name)}</a>'

async def resolve_mention(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> str:
    # prefer members table
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT username, first_name FROM members WHERE user_id=? LIMIT 1", (user_id,))
    r = cur.fetchone()
    conn.close()
    if r:
        name = r["first_name"] or r["username"] or str(user_id)
        return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'
    # fallback try bot.get_chat_member if possible
    try:
        m = await context.bot.get_chat_member(chat_id, user_id)
        name = m.user.first_name or m.user.username or str(user_id)
        return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'
    except Exception:
        return str(user_id)

# members & stats ops
def upsert_member(chat_id: int, user_id: int, username: Optional[str], first_name: Optional[str], last_name: Optional[str], is_bot: bool):
    now = int(time.time())
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO members(chat_id, user_id, username, first_name, last_name, first_seen, last_seen, is_bot) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (chat_id, user_id, username, first_name, last_name, now, now, 1 if is_bot else 0))
    cur.execute("UPDATE members SET username=?, first_name=?, last_name=?, last_seen=?, is_bot=? WHERE chat_id=? AND user_id=?",
                (username, first_name, last_name, now, 1 if is_bot else 0, chat_id, user_id))
    conn.commit()
    conn.close()

def inc_message_count(chat_id: int, user_id: int):
    now = int(time.time())
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO user_stats(chat_id, user_id, messages_count, last_seen, stolen_count, balance) VALUES(?, ?, 0, ?, 0, 0)", (chat_id, user_id, now))
    cur.execute("UPDATE user_stats SET messages_count = messages_count + 1, last_seen = ? WHERE chat_id = ? AND user_id = ?", (now, chat_id, user_id))
    conn.commit()
    conn.close()

# triggers ops
def add_trigger(chat_id: int, keyword: str, rtype: str, response: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO triggers(chat_id, trigger_text, response_type, response_text) VALUES(?, ?, ?, ?)",
                (chat_id, keyword, rtype, response))
    conn.commit()
    conn.close()

def remove_trigger(chat_id: int, keyword: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM triggers WHERE chat_id=? AND trigger_text=?", (chat_id, keyword))
    conn.commit()
    conn.close()

def list_triggers(chat_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT trigger_text, response_type, response_text FROM triggers WHERE chat_id=?", (chat_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

# ranks ops
def add_rank(chat_id: int, name: str, min_messages: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO ranks(chat_id, rank_name, min_messages) VALUES(?, ?, ?)", (chat_id, name, min_messages))
    conn.commit()
    conn.close()

def list_ranks(chat_id: int) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT rank_name, min_messages FROM ranks WHERE chat_id=? ORDER BY min_messages ASC", (chat_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def assign_user_rank(chat_id: int, user_id: int, rank_name: str):
    now = int(time.time())
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO user_ranks(chat_id, user_id, rank_name, assigned_at) VALUES(?, ?, ?, ?)", (chat_id, user_id, rank_name, now))
    conn.commit()
    conn.close()

def get_user_rank(chat_id: int, user_id: int) -> Optional[str]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT rank_name FROM user_ranks WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    r = cur.fetchone()
    conn.close()
    return r["rank_name"] if r else None

def sync_ranks_for_chat(chat_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT rank_name, min_messages FROM ranks WHERE chat_id=? ORDER BY min_messages ASC", (chat_id,))
    ranks = cur.fetchall()
    if not ranks:
        conn.close()
        return
    cur.execute("SELECT user_id, messages_count FROM user_stats WHERE chat_id=?", (chat_id,))
    users = cur.fetchall()
    for u in users:
        best = None
        for r in ranks:
            if u["messages_count"] >= r["min_messages"]:
                best = r["rank_name"]
            else:
                break
        if best:
            cur.execute("INSERT OR REPLACE INTO user_ranks(chat_id, user_id, rank_name, assigned_at) VALUES(?, ?, ?, ?)",
                        (chat_id, u["user_id"], best, int(time.time())))
    conn.commit()
    conn.close()

# random titles ops
def add_random_title(chat_id: int, title: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO random_titles_pool(chat_id, title) VALUES(?, ?)", (chat_id, title))
    conn.commit()
    conn.close()

def list_random_titles(chat_id: int) -> List[str]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT title FROM random_titles_pool WHERE chat_id=?", (chat_id,))
    rows = [r["title"] for r in cur.fetchall()]
    conn.close()
    return rows

def set_random_titles_enabled(chat_id: int, enabled: bool, percent: int = 10):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO random_titles_settings(chat_id, enabled, percent) VALUES(?, ?, ?)", (chat_id, 1 if enabled else 0, percent))
    conn.commit()
    conn.close()

def get_random_titles_setting(chat_id: int) -> Dict[str, Any]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT enabled, percent FROM random_titles_settings WHERE chat_id=?", (chat_id,))
    r = cur.fetchone()
    conn.close()
    if r:
        return {"enabled": bool(r["enabled"]), "percent": r["percent"]}
    return {"enabled": False, "percent": 10}

def save_random_title_history(chat_id: int, user_id: int, title: str):
    now = int(time.time())
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO random_titles_history(chat_id, user_id, title, assigned_at) VALUES(?, ?, ?, ?)", (chat_id, user_id, title, now))
    conn.commit()
    conn.close()

# welcome settings
def get_welcome_settings(chat_id: int) -> Dict[str, Any]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT enabled, welcome_type, welcome_text, welcome_media_file_id, captcha_type, captcha_timeout FROM welcome_settings WHERE chat_id=?", (chat_id,))
    r = cur.fetchone()
    conn.close()
    if r:
        return {"enabled": bool(r["enabled"]), "welcome_type": r["welcome_type"], "welcome_text": r["welcome_text"], "welcome_media_file_id": r["welcome_media_file_id"], "captcha_type": r["captcha_type"], "captcha_timeout": r["captcha_timeout"]}
    return {"enabled": False, "welcome_type": "text", "welcome_text": None, "welcome_media_file_id": None, "captcha_type": "button", "captcha_timeout": 60}

def set_welcome_setting(chat_id: int, key: str, value: Any):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO welcome_settings(chat_id) VALUES(?)", (chat_id,))
    cur.execute(f"UPDATE welcome_settings SET {key} = ? WHERE chat_id = ?", (value, chat_id))
    conn.commit()
    conn.close()

# bank ops
def create_bank_account(chat_id: int, user_id: int, bank_name: str) -> bool:
    bank_name = bank_name.lower()
    if bank_name not in ("alrafidain", "alahli", "alrasheed"):
        return False
    conn = get_conn()
    cur = conn.cursor()
    acct_id = f"{bank_name[:3].upper()}-{user_id}"
    cur.execute("INSERT OR IGNORE INTO bank_accounts(chat_id, user_id, bank_name, account_id, balance) VALUES(?, ?, ?, ?, ?)",
                (chat_id, user_id, bank_name, acct_id, 0))
    conn.commit()
    conn.close()
    recompute_user_total_balance(chat_id, user_id)
    return True

def get_account_balance(chat_id: int, user_id: int, bank_name: str) -> Optional[int]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT balance FROM bank_accounts WHERE chat_id=? AND user_id=? AND bank_name=?", (chat_id, user_id, bank_name))
    r = cur.fetchone()
    conn.close()
    return int(r["balance"]) if r else None

def change_account_balance(chat_id: int, user_id: int, bank_name: str, delta: int) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT balance FROM bank_accounts WHERE chat_id=? AND user_id=? AND bank_name=?", (chat_id, user_id, bank_name))
    r = cur.fetchone()
    if not r:
        conn.close()
        return False
    new = int(r["balance"]) + delta
    if new < 0:
        conn.close()
        return False
    cur.execute("UPDATE bank_accounts SET balance=? WHERE chat_id=? AND user_id=? AND bank_name=?", (new, chat_id, user_id, bank_name))
    conn.commit()
    conn.close()
    recompute_user_total_balance(chat_id, user_id)
    return True

def recompute_user_total_balance(chat_id: int, user_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT SUM(balance) as total FROM bank_accounts WHERE chat_id=? AND user_id=?", (chat_id, user_id))
    r = cur.fetchone()
    total = int(r["total"]) if r and r["total"] is not None else 0
    cur.execute("INSERT OR IGNORE INTO user_stats(chat_id, user_id, messages_count, last_seen, stolen_count, balance) VALUES(?, ?, 0, ?, 0, ?)", (chat_id, user_id, int(time.time()), total))
    cur.execute("UPDATE user_stats SET balance=? WHERE chat_id=? AND user_id=?", (total, chat_id, user_id))
    conn.commit()
    conn.close()

def transfer_between_users(chat_id: int, from_id: int, to_id: int, amount: int, bank_name: str="alrafidain") -> bool:
    if amount <= 0:
        return False
    # ensure accounts exist
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT balance FROM bank_accounts WHERE chat_id=? AND user_id=? AND bank_name=?", (chat_id, from_id, bank_name))
    s = cur.fetchone()
    if not s or s["balance"] < amount:
        conn.close()
        return False
    # subtract
    cur.execute("UPDATE bank_accounts SET balance = balance - ? WHERE chat_id=? AND user_id=? AND bank_name=?", (amount, chat_id, from_id, bank_name))
    # create receiver if not exists
    cur.execute("INSERT OR IGNORE INTO bank_accounts(chat_id, user_id, bank_name, account_id, balance) VALUES(?, ?, ?, ?, ?)", (chat_id, to_id, bank_name, f"{bank_name[:3].upper()}-{to_id}", 0))
    cur.execute("UPDATE bank_accounts SET balance = balance + ? WHERE chat_id=? AND user_id=? AND bank_name=?", (amount, chat_id, to_id, bank_name))
    conn.commit()
    conn.close()
    recompute_user_total_balance(chat_id, from_id)
    recompute_user_total_balance(chat_id, to_id)
    return True

# top queries
def get_top_active_global(limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id, SUM(messages_count) as msgs FROM user_stats GROUP BY user_id ORDER BY msgs DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def get_top_active_in_chat(chat_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id, messages_count FROM user_stats WHERE chat_id=? ORDER BY messages_count DESC LIMIT ?", (chat_id, limit))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def get_top_thieves(limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id, SUM(stolen_count) as stolen FROM user_stats GROUP BY user_id ORDER BY stolen DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def get_top_rich(limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id, SUM(balance) as bal FROM user_stats GROUP BY user_id ORDER BY bal DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

# record theft
def record_theft(chat_id: int, target_id: int, amount: int):
    conn = get_conn()
    cur = conn.cursor()
    # subtract greedily from accounts
    cur.execute("SELECT bank_name, balance FROM bank_accounts WHERE chat_id=? AND user_id=? ORDER BY balance DESC", (chat_id, target_id))
    rows = cur.fetchall()
    rem = amount
    for r in rows:
        if rem <= 0:
            break
        bname = r["bank_name"]
        bal = int(r["balance"])
        take = min(bal, rem)
        cur.execute("UPDATE bank_accounts SET balance=? WHERE chat_id=? AND user_id=? AND bank_name=?", (bal - take, chat_id, target_id, bname))
        rem -= take
    # increment stolen_count
    cur.execute("INSERT OR IGNORE INTO user_stats(chat_id, user_id, messages_count, last_seen, stolen_count, balance) VALUES(?, ?, 0, ?, 0, 0)", (chat_id, target_id, int(time.time())))
    cur.execute("UPDATE user_stats SET stolen_count = stolen_count + 1 WHERE chat_id=? AND user_id=?", (chat_id, target_id))
    conn.commit()
    conn.close()
    recompute_user_total_balance(chat_id, target_id)

# --------------------
# UI: Menu & callbacks
# --------------------
def main_menu_markup():
    kb = [
        [InlineKeyboardButton("القوائم 1", callback_data="menu:list1"),
         InlineKeyboardButton("القوائم 2", callback_data="menu:list2"),
         InlineKeyboardButton("القوائم 3", callback_data="menu:list3")],
        [InlineKeyboardButton("الألعاب 🎮", callback_data="menu:games"),
         InlineKeyboardButton("التوب 🔝", callback_data="menu:top")],
        [InlineKeyboardButton("البنك 🏦", callback_data="menu:bank"),
         InlineKeyboardButton("الأوامر الأساسية", callback_data="menu:help")]
    ]
    return InlineKeyboardMarkup(kb)

def games_menu_markup():
    kb = [
        [InlineKeyboardButton("السحب 🎲", callback_data="games:draw"),
         InlineKeyboardButton("تخمين رقم 🎯", callback_data="games:guess")],
        [InlineKeyboardButton("عودة", callback_data="menu:main")]
    ]
    return InlineKeyboardMarkup(kb)

def top_menu_markup():
    kb = [
        [InlineKeyboardButton("توب متفاعلين (عام)", callback_data="top:global")],
        [InlineKeyboardButton("توب متفاعلين (المجموعة)", callback_data="top:chat")],
        [InlineKeyboardButton("توب حراميه", callback_data="top:thieves")],
        [InlineKeyboardButton("توب أغنياء", callback_data="top:rich")],
        [InlineKeyboardButton("عودة", callback_data="menu:main")]
    ]
    return InlineKeyboardMarkup(kb)

def bank_menu_markup():
    kb = [
        [InlineKeyboardButton("إنشاء حساب 🏦", callback_data="bank:create")],
        [InlineKeyboardButton("رصيد حسابي", callback_data="bank:balance"),
         InlineKeyboardButton("إيداع تجريبي", callback_data="bank:deposit")],
        [InlineKeyboardButton("الـبنوك: الرافدين", callback_data="bank:bank_alrafidain"),
         InlineKeyboardButton("الأهلي", callback_data="bank:bank_alahli"),
         InlineKeyboardButton("الرشيد", callback_data="bank:bank_alrasheed")],
        [InlineKeyboardButton("عودة", callback_data="menu:main")]
    ]
    return InlineKeyboardMarkup(kb)

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"قائمة {BOT_NAME} الرئيسية:", reply_markup=main_menu_markup())

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    await q.answer()
    if data == "menu:main":
        await q.edit_message_text("القائمة الرئيسية:", reply_markup=main_menu_markup())
        return
    if data.startswith("menu:list"):
        await q.edit_message_text(f"أنت في {data.split(':')[1]} — ضع أوامرك هنا.", reply_markup=main_menu_markup())
        return
    if data == "menu:games":
        await q.edit_message_text("قائمة الألعاب:", reply_markup=games_menu_markup())
        return
    if data.startswith("games:"):
        game = data.split(":")[1]
        if game == "draw":
            await q.edit_message_text("لعبة السحب: أرسل /draw لتشغيل السحب.", reply_markup=games_menu_markup())
        elif game == "guess":
            await q.edit_message_text("لعبة التخمين: أرسل /guess <number>.", reply_markup=games_menu_markup())
        return
    if data == "menu:top":
        await q.edit_message_text("قائمة التوب:", reply_markup=top_menu_markup())
        return
    if data.startswith("top:"):
        key = data.split(":")[1]
        if key == "global":
            rows = get_top_active_global(20)
            if not rows:
                await q.edit_message_text("لا توجد بيانات متاحة للتوب العام.", reply_markup=top_menu_markup())
                return
            lines = []
            for i, r in enumerate(rows):
                uid = r["user_id"]
                # try to get stored name
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("SELECT first_name, username FROM members WHERE user_id=? LIMIT 1", (uid,))
                mr = cur.fetchone()
                conn.close()
                name = (mr["first_name"] or mr["username"]) if mr else str(uid)
                lines.append(f"{i+1}. <a href='tg://user?id={uid}'>{html.escape(name)}</a> — رسائل: {r['msgs']}")
            await q.edit_message_text("توب المتفاعلين (عام):\n" + "\n".join(lines), parse_mode="HTML", reply_markup=top_menu_markup())
            return
        if key == "chat":
            chat_id = q.message.chat_id
            rows = get_top_active_in_chat(chat_id, 20)
            if not rows:
                await q.edit_message_text("لا توجد بيانات متاحة للتوب داخل هذه المجموعة.", reply_markup=top_menu_markup())
                return
            lines = []
            for i, r in enumerate(rows):
                uid = r["user_id"]
                # use members table to get name
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("SELECT first_name, username FROM members WHERE chat_id=? AND user_id=? LIMIT 1", (chat_id, uid))
                mr = cur.fetchone()
                conn.close()
                name = (mr["first_name"] or mr["username"]) if mr else str(uid)
                lines.append(f"{i+1}. <a href='tg://user?id={uid}'>{html.escape(name)}</a> — رسائل: {r['messages_count']}")
            await q.edit_message_text("توب المتفاعلين داخل المجموعة:\n" + "\n".join(lines), parse_mode="HTML", reply_markup=top_menu_markup())
            return
        if key == "thieves":
            rows = get_top_thieves(20)
            if not rows:
                await q.edit_message_text("لا توجد سجلات سرقات.", reply_markup=top_menu_markup())
                return
            lines = []
            for i, r in enumerate(rows):
                uid = r["user_id"]
                lines.append(f"{i+1}. <a href='tg://user?id={uid}'>{uid}</a> — سرقات: {r['stolen']}")
            await q.edit_message_text("توب الحرامية:\n" + "\n".join(lines), parse_mode="HTML", reply_markup=top_menu_markup())
            return
        if key == "rich":
            rows = get_top_rich(20)
            if not rows:
                await q.edit_message_text("لا توجد بيانات رصيد.", reply_markup=top_menu_markup())
                return
            lines = []
            for i, r in enumerate(rows):
                uid = r["user_id"]
                lines.append(f"{i+1}. <a href='tg://user?id={uid}'>{uid}</a> — رصيد: {r['bal']}")
            await q.edit_message_text("توب الأغنياء:\n" + "\n".join(lines), parse_mode="HTML", reply_markup=top_menu_markup())
            return
    if data == "menu:bank":
        await q.edit_message_text("قائمة البنك:", reply_markup=bank_menu_markup())
        return
    if data.startswith("bank:"):
        cmd = data.split(":")[1]
        user = q.from_user
        chat_id = q.message.chat_id
        if cmd == "create":
            ok = create_bank_account(chat_id, user.id, "alrafidain")
            if ok:
                await q.edit_message_text("تم إنشاء حسابك في بنك الرافدين.", reply_markup=bank_menu_markup())
            else:
                await q.answer("فشل إنشاء الحساب.", show_alert=True)
            return
        if cmd == "balance":
            banks = ["alrafidain", "alahli", "alrasheed"]
            lines = []
            for b in banks:
                bal = get_account_balance(chat_id, user.id, b)
                lines.append(f"{b}: {bal if bal is not None else 'لا يوجد حساب'}")
            await q.edit_message_text("رصيد الحسابات:\n" + "\n".join(lines), reply_markup=bank_menu_markup())
            return
        if cmd == "deposit":
            await q.edit_message_text("لا يمكنك الإيداع عبر الزر — استخدم /give (المالك) أو /bank_deposit (قيد التنفيذ).", reply_markup=bank_menu_markup())
            return
        if cmd.startswith("bank_"):
            bname = cmd.replace("bank_", "")
            await q.edit_message_text(f"البنك: {bname}\nاستخدم /bank_create {bname} لإنشاء حساب.", reply_markup=bank_menu_markup())
            return
    # fallback
    await q.edit_message_text("اختيار غير معروف.", reply_markup=main_menu_markup())

# --------------------
# Commands: triggers, ranks, random titles, bank, give/steal, purge, shorten, welcome settings
# --------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"مرحباً! هذا بوت الحماية {BOT_NAME}.\nاستخدم /menu لفتح القائمة الرئيسية.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "/menu - فتح القائمة الرئيسية\n"
        "/trigger add <keyword> <type:text|gif|photo|sticker> <response>\n"
        "/trigger remove <keyword>\n"
        "/trigger list\n\n"
        "/ranks add <name> <min_messages>\n"
        "/ranks list\n"
        "/myrank\n\n"
        "/randomtitles add <title>\n"
        "/randomtitles list\n"
        "/randomtitles on|off [percent]\n"
        "/randomtitles run\n\n"
        "/bank_create <bankname> /bank_balance /transfer <amount> @user\n"
        "/give <amount> <user_id> (owner)\n"
        "/steal <amount> (admin, reply to victim)\n"
        "/purge <count> (reply or number)\n"
        "/shorten <url>\n\n"
        "لإعداد الترحيب: /welcome on|off  و /welcome_message <text>  و /welcome_captcha <button|code|math>\n"
    )
    await update.message.reply_text(text)

# TRIGGERS
async def trigger_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /trigger add/remove/list ...
    if not context.args:
        await update.message.reply_text("استعمال: /trigger add/remove/list ...")
        return
    sub = context.args[0].lower()
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    allowed = await is_chat_admin(context, chat_id, user_id) or is_owner_or_sudo(user_id)
    if sub == "add":
        if not allowed:
            await update.message.reply_text("فقط المدراء أو المالك/SUDO يمكنهم إضافة triggers.")
            return
        if len(context.args) < 4:
            await update.message.reply_text("استعمال: /trigger add <keyword> <type> <response>")
            return
        keyword = context.args[1]
        rtype = context.args[2].lower()
        response = " ".join(context.args[3:])
        add_trigger(chat_id, keyword, rtype, response)
        await update.message.reply_text(f"تمت إضافة trigger: {keyword} -> {rtype}")
        return
    if sub == "remove":
        if not allowed:
            await update.message.reply_text("فقط المدراء أو المالك/SUDO يمكنهم إزالة triggers.")
            return
        if len(context.args) < 2:
            await update.message.reply_text("استعمال: /trigger remove <keyword>")
            return
        remove_trigger(chat_id, context.args[1])
        await update.message.reply_text("تمت الإزالة إن كانت موجودة.")
        return
    if sub == "list":
        rows = list_triggers(chat_id)
        if not rows:
            await update.message.reply_text("لا توجد triggers في هذه المجموعة.")
            return
        txt = "Triggers:\n" + "\n".join([f"- {r['trigger_text']} -> {r['response_type']}: {r['response_text']}" for r in rows])
        await update.message.reply_text(txt)
        return
    await update.message.reply_text("sub command غير معروف.")

# RANKS
async def ranks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استعمال: /ranks add|list|sync ...")
        return
    sub = context.args[0].lower()
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    allowed = await is_chat_admin(context, chat_id, user_id) or is_owner_or_sudo(user_id)
    if sub == "add":
        if not allowed:
            await update.message.reply_text("فقط المدراء/المالك يمكنهم إضافة رتب.")
            return
        if len(context.args) < 3:
            await update.message.reply_text("استعمال: /ranks add <name> <min_messages>")
            return
        name = context.args[1]
        try:
            minm = int(context.args[2])
        except Exception:
            await update.message.reply_text("min_messages يجب أن يكون عدد صحيح.")
            return
        add_rank(chat_id, name, minm)
        await update.message.reply_text(f"أضيفت رتبة {name}")
        return
    if sub == "list":
        rows = list_ranks(chat_id)
        if not rows:
            await update.message.reply_text("لا توجد رتب معرفة.")
            return
        txt = "Ranks:\n" + "\n".join([f"- {r['rank_name']}: >= {r['min_messages']} رسائل" for r in rows])
        await update.message.reply_text(txt)
        return
    if sub == "sync":
        if not allowed:
            await update.message.reply_text("فقط المدراء/المالك يمكنهم مزامنة الرتب.")
            return
        sync_ranks_for_chat(chat_id)
        await update.message.reply_text("تم مزامنة الرتب لهذه المجموعة.")
        return
    await update.message.reply_text("sub غير معروف.")

async def myrank_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    r = get_user_rank(chat_id, user_id)
    if r:
        await update.message.reply_text(f"رتبتك: {r}")
    else:
        await update.message.reply_text("ليس لديك رتبة حالياً.")

# RANDOM TITLES
async def randomtitles_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استعمال: /randomtitles add|remove|list|on|off|run ...")
        return
    sub = context.args[0].lower()
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    allowed = await is_chat_admin(context, chat_id, user_id) or is_owner_or_sudo(user_id)
    if sub == "add":
        if not allowed:
            await update.message.reply_text("فقط المدراء/المالك يمكنهم إضافة ألقاب.")
            return
        title = " ".join(context.args[1:])
        add_random_title(chat_id, title)
        await update.message.reply_text("أضيف لقب للقائمة.")
        return
    if sub == "list":
        lst = list_random_titles(chat_id)
        await update.message.reply_text("Pool:\n" + ("\n".join(lst) if lst else "(فارغ)"))
        return
    if sub in ("on", "off"):
        if not allowed:
            await update.message.reply_text("فقط المدراء/المالك يمكنهم تغيير الإعداد.")
            return
        percent = 10
        if len(context.args) >= 2:
            try:
                percent = int(context.args[1])
            except Exception:
                percent = 10
        set_random_titles_enabled(chat_id, sub == "on", percent)
        await update.message.reply_text(f"تم {'تفعيل' if sub=='on' else 'إيقاف'} Random Titles بنسبة {percent}%")
        return
    if sub == "run":
        if not allowed:
            await update.message.reply_text("فقط المدراء/المالك يمكنهم التشغيل اليدوي.")
            return
        await update.message.reply_text("جارٍ توزيع الألقاب الآن...")
        await run_random_titles_for_chat(context, chat_id, announce=True)
        return
    await update.message.reply_text("sub غير معروف.")

async def run_random_titles_for_chat(context: ContextTypes.DEFAULT_TYPE, chat_id: int, announce: bool=True):
    setting = get_random_titles_setting(chat_id)
    if not setting.get("enabled"):
        return
    percent = setting.get("percent", 10)
    pool = list_random_titles(chat_id)
    if not pool:
        return
    # active members last 7 days
    conn = get_conn()
    cur = conn.cursor()
    cutoff = int(time.time()) - 7*24*3600
    cur.execute("SELECT user_id, first_name, username FROM members WHERE chat_id=? AND is_bot=0 AND last_seen>=?", (chat_id, cutoff))
    members = cur.fetchall()
    conn.close()
    if not members:
        return
    k = max(1, int(len(members) * percent / 100))
    selected = random.sample(list(members), min(k, len(members)))
    lines = []
    for m in selected:
        title = random.choice(pool)
        save_random_title_history(chat_id, m["user_id"], title)
        name = m["first_name"] or m["username"] or str(m["user_id"])
        lines.append(f"{html.escape(name)} — «{html.escape(title)}»")
    if announce:
        try:
            await context.bot.send_message(chat_id=chat_id, text="🔔 ألقاب اليوم 🔔\n\n" + "\n".join(lines), parse_mode="HTML")
        except Exception:
            logger.exception("announce failed")

# BANK / GIVE / STEAL / TRANSFER
async def bank_create_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    if not context.args:
        await update.message.reply_text("استعمال: /bank_create <alrafidain|alahli|alrasheed>")
        return
    bank = context.args[0].lower()
    ok = create_bank_account(chat_id, user.id, bank)
    if ok:
        await update.message.reply_text(f"تم إنشاء حسابك في {bank}.")
    else:
        await update.message.reply_text("خيار بنك غير معروف.")

async def bank_balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    banks = ["alrafidain", "alahli", "alrasheed"]
    lines = []
    for b in banks:
        bal = get_account_balance(chat_id, user.id, b)
        lines.append(f"{b}: {bal if bal is not None else 'لا يوجد حساب'}")
    await update.message.reply_text("رصيد الحسابات:\n" + "\n".join(lines))

async def give_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_owner_or_sudo(user.id):
        await update.message.reply_text("فقط المالك/SUDO يمكنه الإضافة.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("استعمال: /give <amount> <user_id>")
        return
    try:
        amt = int(context.args[0])
        target = int(context.args[1])
    except Exception:
        await update.message.reply_text("الاستخدام غير صحيح.")
        return
    create_bank_account(update.effective_chat.id, target, "alrafidain")
    change_account_balance(update.effective_chat.id, target, "alrafidain", amt)
    await update.message.reply_text(f"أضيف {amt} إلى {target} في الرافدين.")

async def steal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # admin or owner
    user = update.effective_user
    chat_id = update.effective_chat.id
    allowed = await is_chat_admin(context, chat_id, user.id) or is_owner_or_sudo(user.id)
    if not allowed:
        await update.message.reply_text("فقط المدراء أو المالك/سودو يمكنهم تسجيل سرقة.")
        return
    if not context.args and not update.message.reply_to_message:
        await update.message.reply_text("استعمال: /steal <amount> (reply or id)")
        return
    try:
        amt = int(context.args[0]) if context.args else 0
    except Exception:
        await update.message.reply_text("amount غير صحيح.")
        return
    target = None
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user.id
    elif len(context.args) >= 2:
        try:
            target = int(context.args[1])
        except Exception:
            await update.message.reply_text("حدد user_id صحيح.")
            return
    record_theft(chat_id, target, amt)
    await update.message.reply_text(f"تم تسجيل سرقة {amt} من {target} (تجربة).")

async def transfer_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("استعمال: /transfer <amount> <@user|user_id> [bank]")
        return
    chat_id = update.effective_chat.id
    try:
        amt = int(context.args[0])
    except Exception:
        await update.message.reply_text("amount غير صحيح.")
        return
    # target
    target = None
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user.id
    else:
        try:
            target = int(context.args[1])
        except Exception:
            await update.message.reply_text("حدد user_id أو رد على المستخدم.")
            return
    bank = context.args[2].lower() if len(context.args) >= 3 else "alrafidain"
    ok = transfer_between_users(chat_id, update.effective_user.id, target, amt, bank)
    if ok:
        await update.message.reply_text(f"تم تحويل {amt} من حسابك إلى {target} في {bank}.")
    else:
        await update.message.reply_text("فشل التحويل (احتمال رصيد غير كافٍ أو حساب غير موجود).")

# PURGE (delete many messages)
async def purge_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # usage: reply to message and send /purge to delete from replied message to current
    msg = update.message
    if not msg:
        return
    chat_id = update.effective_chat.id
    user = update.effective_user
    allowed = await is_chat_admin(context, chat_id, user.id) or is_owner_or_sudo(user.id)
    if not allowed:
        await update.message.reply_text("فقط المدار... (truncated for brevity)
