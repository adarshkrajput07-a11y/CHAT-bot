"""
Bot name - 𝑴𝑨𝑻𝑹𝑰𝑿 𝑪𝑯𝑨𝑻 𝑩𝑶𝑻 💬🍃

Simple group management bot (Miss Rose style).
Setup:  pip install -r requirements.txt
Run:    python rosebot.py   (token + owner id come from config.py)
"""
import asyncio
import os
import sqlite3
import time
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from functools import wraps

from telegram import Update, ChatPermissions
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters,
)

from deep_translator import GoogleTranslator

# Token and owner id live in config.py (kept out of GitHub).
# Falls back to BOT_TOKEN / OWNER_ID environment variables.
try:
    from config import BOT_TOKEN, OWNER_ID
except ImportError:
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
    OWNER_ID = int(os.environ.get("OWNER_ID", "0"))

logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------- database
db = sqlite3.connect("rosebot.db", check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS settings(
    chat_id INTEGER PRIMARY KEY, welcome TEXT, welcome_on INTEGER DEFAULT 1,
    rules TEXT, warn_limit INTEGER DEFAULT 3, flood INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS notes(chat_id INTEGER, name TEXT, text TEXT,
    PRIMARY KEY(chat_id, name));
CREATE TABLE IF NOT EXISTS filters(chat_id INTEGER, kw TEXT, reply TEXT,
    PRIMARY KEY(chat_id, kw));
CREATE TABLE IF NOT EXISTS warns(chat_id INTEGER, user_id INTEGER, n INTEGER,
    PRIMARY KEY(chat_id, user_id));
CREATE TABLE IF NOT EXISTS members(chat_id INTEGER, user_id INTEGER,
    PRIMARY KEY(chat_id, user_id));
""")


def get_setting(chat_id, col):
    db.execute("INSERT OR IGNORE INTO settings(chat_id) VALUES(?)", (chat_id,))
    return db.execute(f"SELECT {col} FROM settings WHERE chat_id=?", (chat_id,)).fetchone()[0]


def set_setting(chat_id, col, val):
    db.execute("INSERT OR IGNORE INTO settings(chat_id) VALUES(?)", (chat_id,))
    db.execute(f"UPDATE settings SET {col}=? WHERE chat_id=?", (val, chat_id))
    db.commit()


# ---------------------------------------------------------------- helpers
async def is_admin(chat, user_id):
    if user_id == OWNER_ID:  # the bot owner can use every admin command
        return True
    m = await chat.get_member(user_id)
    return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)


def admin_only(fn):
    @wraps(fn)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        chat, user = update.effective_chat, update.effective_user
        if chat.type == "private":
            return await update.message.reply_text("Use this command in a group.")
        if not await is_admin(chat, user.id):
            return await update.message.reply_text("Admins only.")
        return await fn(update, ctx)
    return wrapper


def target(update: Update):
    r = update.message.reply_to_message
    return r.from_user if r else None


async def need_target(update: Update):
    t = target(update)
    if not t:
        await update.message.reply_text("Reply to a user's message.")
    return t


# ---------------------------------------------------------------- basics
BOT_NAME = "𝑴𝑨𝑻𝑹𝑰𝑿 𝑪𝑯𝑨𝑻 𝑩𝑶𝑻 💬🍃"


async def start(update: Update, ctx):
    await update.message.reply_text(
        f"Hi! I'm {BOT_NAME}\n"
        "Add me to your group as admin and I'll help manage it. Use /help.")


async def help_cmd(update: Update, ctx):
    await update.message.reply_text(
        f"{BOT_NAME}\n\n"
        "Moderation (reply to user):\n"
        "/ban /unban /kick /mute /unmute /warn /resetwarns /warns\n"
        "/setwarnlimit N\n\n"
        "Welcome: /setwelcome text ({first}, {group}), /welcome on|off\n"
        "Rules: /setrules text, /rules\n"
        "Notes: /save name text (reply works too), /get name, #name, /notes, /clear name\n"
        "Filters: /filter word reply, /stop word, /filters\n"
        "Antiflood: /setflood N (msgs in 5s, 0 = off)\n\n"
        "Translate: /tr [lang] (reply to a message) or /tr lang text\n"
        "Deleted accounts: /zombies (scan), /zombies clean (remove)"
    )


# ---------------------------------------------------------------- moderation
@admin_only
async def ban(update, ctx):
    if t := await need_target(update):
        await update.effective_chat.ban_member(t.id)
        await update.message.reply_text(f"Banned {t.mention_html()}.", parse_mode="HTML")


@admin_only
async def unban(update, ctx):
    if t := await need_target(update):
        await update.effective_chat.unban_member(t.id, only_if_banned=True)
        await update.message.reply_text(f"Unbanned {t.mention_html()}.", parse_mode="HTML")


@admin_only
async def kick(update, ctx):
    if t := await need_target(update):
        await update.effective_chat.ban_member(t.id)
        await update.effective_chat.unban_member(t.id)
        await update.message.reply_text(f"Kicked {t.mention_html()}.", parse_mode="HTML")


@admin_only
async def mute(update, ctx):
    if t := await need_target(update):
        await update.effective_chat.restrict_member(
            t.id, ChatPermissions(can_send_messages=False))
        await update.message.reply_text(f"Muted {t.mention_html()}.", parse_mode="HTML")


@admin_only
async def unmute(update, ctx):
    if t := await need_target(update):
        await update.effective_chat.restrict_member(t.id, ChatPermissions(
            can_send_messages=True, can_send_polls=True,
            can_send_other_messages=True, can_add_web_page_previews=True,
            can_invite_users=True))
        await update.message.reply_text(f"Unmuted {t.mention_html()}.", parse_mode="HTML")


@admin_only
async def warn(update, ctx):
    t = await need_target(update)
    if not t:
        return
    cid = update.effective_chat.id
    db.execute("INSERT OR IGNORE INTO warns VALUES(?,?,0)", (cid, t.id))
    db.execute("UPDATE warns SET n=n+1 WHERE chat_id=? AND user_id=?", (cid, t.id))
    db.commit()
    n = db.execute("SELECT n FROM warns WHERE chat_id=? AND user_id=?", (cid, t.id)).fetchone()[0]
    limit = get_setting(cid, "warn_limit")
    if n >= limit:
        await update.effective_chat.ban_member(t.id)
        db.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?", (cid, t.id))
        db.commit()
        await update.message.reply_text(
            f"{t.mention_html()} hit {limit} warnings and was banned.", parse_mode="HTML")
    else:
        await update.message.reply_text(
            f"Warned {t.mention_html()} ({n}/{limit}).", parse_mode="HTML")


@admin_only
async def resetwarns(update, ctx):
    if t := await need_target(update):
        db.execute("DELETE FROM warns WHERE chat_id=? AND user_id=?",
                   (update.effective_chat.id, t.id))
        db.commit()
        await update.message.reply_text("Warnings reset.")


async def warns_cmd(update, ctx):
    t = target(update) or update.effective_user
    row = db.execute("SELECT n FROM warns WHERE chat_id=? AND user_id=?",
                     (update.effective_chat.id, t.id)).fetchone()
    limit = get_setting(update.effective_chat.id, "warn_limit")
    await update.message.reply_text(f"{t.first_name}: {row[0] if row else 0}/{limit} warnings.")


@admin_only
async def setwarnlimit(update, ctx):
    if ctx.args and ctx.args[0].isdigit() and int(ctx.args[0]) > 0:
        set_setting(update.effective_chat.id, "warn_limit", int(ctx.args[0]))
        await update.message.reply_text("Warn limit updated.")
    else:
        await update.message.reply_text("Usage: /setwarnlimit 3")


# ---------------------------------------------------------------- welcome / rules
@admin_only
async def setwelcome(update, ctx):
    text = " ".join(ctx.args)
    if not text:
        return await update.message.reply_text("Usage: /setwelcome Hi {first}, welcome to {group}!")
    set_setting(update.effective_chat.id, "welcome", text)
    await update.message.reply_text("Welcome message saved.")


@admin_only
async def welcome_toggle(update, ctx):
    if ctx.args and ctx.args[0].lower() in ("on", "off"):
        set_setting(update.effective_chat.id, "welcome_on", int(ctx.args[0].lower() == "on"))
        await update.message.reply_text(f"Welcome {ctx.args[0].lower()}.")
    else:
        await update.message.reply_text("Usage: /welcome on|off")


async def on_new_members(update: Update, ctx):
    cid = update.effective_chat.id
    if not get_setting(cid, "welcome_on"):
        return
    tpl = get_setting(cid, "welcome") or "Welcome, {first}!"
    for u in update.message.new_chat_members:
        if u.is_bot:
            continue
        await update.message.reply_text(
            tpl.replace("{first}", u.first_name).replace("{group}", update.effective_chat.title))


@admin_only
async def setrules(update, ctx):
    text = " ".join(ctx.args)
    if not text:
        return await update.message.reply_text("Usage: /setrules <text>")
    set_setting(update.effective_chat.id, "rules", text)
    await update.message.reply_text("Rules saved.")


async def rules(update, ctx):
    r = get_setting(update.effective_chat.id, "rules")
    await update.message.reply_text(r or "No rules set yet.")


# ---------------------------------------------------------------- notes
@admin_only
async def save(update, ctx):
    if not ctx.args:
        return await update.message.reply_text("Usage: /save name text (or reply to a message)")
    name = ctx.args[0].lower()
    text = " ".join(ctx.args[1:])
    if not text and update.message.reply_to_message:
        text = update.message.reply_to_message.text or ""
    if not text:
        return await update.message.reply_text("Nothing to save.")
    db.execute("INSERT OR REPLACE INTO notes VALUES(?,?,?)",
               (update.effective_chat.id, name, text))
    db.commit()
    await update.message.reply_text(f"Saved note #{name}.")


async def get_note(update, ctx):
    if not ctx.args:
        return await update.message.reply_text("Usage: /get name")
    row = db.execute("SELECT text FROM notes WHERE chat_id=? AND name=?",
                     (update.effective_chat.id, ctx.args[0].lower())).fetchone()
    await update.message.reply_text(row[0] if row else "Note not found.")


async def notes(update, ctx):
    rows = db.execute("SELECT name FROM notes WHERE chat_id=?", (update.effective_chat.id,)).fetchall()
    await update.message.reply_text(
        "Notes:\n" + "\n".join(f"#{r[0]}" for r in rows) if rows else "No notes.")


@admin_only
async def clear(update, ctx):
    if ctx.args:
        db.execute("DELETE FROM notes WHERE chat_id=? AND name=?",
                   (update.effective_chat.id, ctx.args[0].lower()))
        db.commit()
        await update.message.reply_text("Note removed.")


# ---------------------------------------------------------------- filters
@admin_only
async def add_filter(update, ctx):
    if len(ctx.args) < 2:
        return await update.message.reply_text("Usage: /filter keyword reply text")
    db.execute("INSERT OR REPLACE INTO filters VALUES(?,?,?)",
               (update.effective_chat.id, ctx.args[0].lower(), " ".join(ctx.args[1:])))
    db.commit()
    await update.message.reply_text("Filter added.")


@admin_only
async def stop_filter(update, ctx):
    if ctx.args:
        db.execute("DELETE FROM filters WHERE chat_id=? AND kw=?",
                   (update.effective_chat.id, ctx.args[0].lower()))
        db.commit()
        await update.message.reply_text("Filter removed.")


async def list_filters(update, ctx):
    rows = db.execute("SELECT kw FROM filters WHERE chat_id=?", (update.effective_chat.id,)).fetchall()
    await update.message.reply_text(
        "Filters:\n" + "\n".join(f"- {r[0]}" for r in rows) if rows else "No filters.")


# ---------------------------------------------------------------- antiflood
@admin_only
async def setflood(update, ctx):
    if ctx.args and ctx.args[0].isdigit():
        set_setting(update.effective_chat.id, "flood", int(ctx.args[0]))
        await update.message.reply_text("Antiflood updated.")
    else:
        await update.message.reply_text("Usage: /setflood 5  (0 = off)")


recent = defaultdict(lambda: deque(maxlen=50))


async def on_text(update: Update, ctx):
    msg, chat, user = update.message, update.effective_chat, update.effective_user
    if not msg or not msg.text or chat.type == "private":
        return
    cid = chat.id

    # antiflood
    limit = get_setting(cid, "flood")
    if limit:
        q = recent[(cid, user.id)]
        now = time.time()
        q.append(now)
        if sum(1 for t in q if now - t <= 5) >= limit and not await is_admin(chat, user.id):
            q.clear()
            until = datetime.now(timezone.utc) + timedelta(minutes=10)
            await chat.restrict_member(user.id, ChatPermissions(can_send_messages=False),
                                       until_date=until)
            return await msg.reply_text(f"{user.first_name} muted for 10 min (flood).")

    text = msg.text.lower()

    # #note shortcut
    if text.startswith("#"):
        row = db.execute("SELECT text FROM notes WHERE chat_id=? AND name=?",
                         (cid, text[1:].split()[0])).fetchone()
        if row:
            return await msg.reply_text(row[0])

    # keyword filters
    for kw, reply in db.execute("SELECT kw, reply FROM filters WHERE chat_id=?", (cid,)):
        if kw in text:
            return await msg.reply_text(reply)


# ---------------------------------------------------------------- translate
async def translate(update: Update, ctx):
    msg, args = update.message, ctx.args
    if msg.reply_to_message:
        src = msg.reply_to_message
        text = src.text or src.caption
        lang = args[0].lower() if args else "en"
    else:
        if len(args) < 2:
            return await msg.reply_text(
                "Reply to a message with /tr [lang], or use /tr lang text\n"
                "Example: /tr es Hello everyone")
        lang, text = args[0].lower(), " ".join(args[1:])
    if not text:
        return await msg.reply_text("Nothing to translate.")
    try:
        out = await asyncio.to_thread(
            GoogleTranslator(source="auto", target=lang).translate, text)
    except Exception:
        return await msg.reply_text(
            "Translation failed. Check the language code (en, es, hi, ar, fr, ...).")
    await msg.reply_text((out or "Nothing to translate.")[:4096])


# ---------------------------------------------------------------- deleted accounts
async def track(update: Update, ctx):
    """Remember who has been seen in each group (Bot API can't list members)."""
    chat, msg = update.effective_chat, update.message
    users = [update.effective_user] if update.effective_user else []
    if msg and msg.new_chat_members:
        users += msg.new_chat_members
    for u in users:
        if not u.is_bot:
            db.execute("INSERT OR IGNORE INTO members VALUES(?,?)", (chat.id, u.id))
    db.commit()


@admin_only
async def zombies(update: Update, ctx):
    chat = update.effective_chat
    clean = bool(ctx.args) and ctx.args[0].lower() == "clean"
    ids = [r[0] for r in db.execute(
        "SELECT user_id FROM members WHERE chat_id=?", (chat.id,))]
    status = await update.message.reply_text(f"Scanning {len(ids)} known members...")
    found = removed = 0
    for uid in ids:
        try:
            m = await chat.get_member(uid)
        except Exception:
            continue
        if m.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
            db.execute("DELETE FROM members WHERE chat_id=? AND user_id=?", (chat.id, uid))
            continue
        if m.user.first_name == "Deleted Account":
            found += 1
            if clean:
                try:
                    await chat.ban_member(uid)
                    await chat.unban_member(uid)
                    db.execute("DELETE FROM members WHERE chat_id=? AND user_id=?",
                               (chat.id, uid))
                    removed += 1
                except Exception:
                    pass
        await asyncio.sleep(0.05)
    db.commit()
    if clean:
        text = f"Removed {removed} deleted account(s)."
    elif found:
        text = f"Found {found} deleted account(s). Use /zombies clean to remove them."
    else:
        text = "No deleted accounts found."
    await status.edit_text(text + "\n(Only members the bot has seen are checked.)")


# ---------------------------------------------------------------- main
def main():
    if not BOT_TOKEN:
        raise SystemExit("Missing bot token: create config.py (see config.example.py)")
    app = Application.builder().token(BOT_TOKEN).build()
    cmds = {
        "start": start, "help": help_cmd,
        "ban": ban, "unban": unban, "kick": kick, "mute": mute, "unmute": unmute,
        "warn": warn, "resetwarns": resetwarns, "warns": warns_cmd,
        "setwarnlimit": setwarnlimit,
        "setwelcome": setwelcome, "welcome": welcome_toggle,
        "setrules": setrules, "rules": rules,
        "save": save, "get": get_note, "notes": notes, "clear": clear,
        "filter": add_filter, "stop": stop_filter, "filters": list_filters,
        "setflood": setflood, "tr": translate, "zombies": zombies,
    }
    for name, fn in cmds.items():
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS, track), group=-1)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_members))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.run_polling()


if __name__ == "__main__":
    main()
