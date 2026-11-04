
import asyncio
import sqlite3
import logging
import time
import json
import html
from pathlib import Path

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = "9163340229:AAEmiPxczk53WJFo3C6BD83fQlGMpRY2V4Q"
OWNER_ID = 9290242587

DB_PATH = "voter_bot.db"

MAX_CLONES_PER_USER = 5
MAX_CANDIDATES = 20
MAX_CONTESTS_PER_BOT = 100

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("VOTER")

running_bots = {}
bot_tasks = {}
bot_sessions = {}

main_bot_id = None


# =========================================================
# DATABASE
# =========================================================

def connect():

    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row

    return con


def init_db():

    con = connect()

    con.executescript("""

    CREATE TABLE IF NOT EXISTS instances(
        bot_id INTEGER PRIMARY KEY,
        token TEXT NOT NULL,
        username TEXT,
        owner_id INTEGER NOT NULL,
        enabled INTEGER DEFAULT 1,
        created INTEGER
    );

    CREATE TABLE IF NOT EXISTS admins(
        bot_id INTEGER,
        user_id INTEGER,
        PRIMARY KEY(bot_id,user_id)
    );

    CREATE TABLE IF NOT EXISTS users(
        bot_id INTEGER,
        user_id INTEGER,
        name TEXT,
        username TEXT,
        joined INTEGER,
        PRIMARY KEY(bot_id,user_id)
    );

    CREATE TABLE IF NOT EXISTS states(
        bot_id INTEGER,
        user_id INTEGER,
        state TEXT,
        data TEXT,
        PRIMARY KEY(bot_id,user_id)
    );

    CREATE TABLE IF NOT EXISTS settings(
        bot_id INTEGER PRIMARY KEY,
        vote_channel TEXT
    );

    CREATE TABLE IF NOT EXISTS channels(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id INTEGER,
        chat_id TEXT,
        title TEXT,
        invite TEXT,
        UNIQUE(bot_id,chat_id)
    );

    CREATE TABLE IF NOT EXISTS contests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id INTEGER,
        creator INTEGER,
        title TEXT,
        channel TEXT,
        message_id INTEGER DEFAULT 0,
        created INTEGER,
        ends INTEGER,
        status TEXT DEFAULT 'active'
    );

    CREATE TABLE IF NOT EXISTS candidates(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contest_id INTEGER,
        name TEXT
    );

    CREATE TABLE IF NOT EXISTS votes(
        bot_id INTEGER,
        contest_id INTEGER,
        user_id INTEGER,
        candidate_id INTEGER,
        created INTEGER,
        PRIMARY KEY(bot_id,contest_id,user_id)
    );

    CREATE TABLE IF NOT EXISTS ui_messages(
        bot_id INTEGER,
        user_id INTEGER,
        chat_id INTEGER,
        message_id INTEGER
    );

    CREATE INDEX IF NOT EXISTS votes_contest
    ON votes(bot_id,contest_id);

    CREATE INDEX IF NOT EXISTS contests_bot
    ON contests(bot_id,status);

    """)

    con.commit()
    con.close()


def query(sql, params=(), mode=None):

    con = connect()

    try:

        cur = con.execute(sql, params)

        if mode == "one":
            result = cur.fetchone()

        elif mode == "all":
            result = cur.fetchall()

        else:
            result = cur.lastrowid

        con.commit()

        return result

    finally:
        con.close()


# =========================================================
# TEXT / BUTTON SYSTEM
# =========================================================

FONT_MAP = str.maketrans({
    "a":"ᴀ", "b":"ʙ", "c":"ᴄ", "d":"ᴅ",
    "e":"ᴇ", "f":"ꜰ", "g":"ɢ", "h":"ʜ",
    "i":"ɪ", "j":"ᴊ", "k":"ᴋ", "l":"ʟ",
    "m":"ᴍ", "n":"ɴ", "o":"ᴏ", "p":"ᴘ",
    "q":"q", "r":"ʀ", "s":"ꜱ", "t":"ᴛ",
    "u":"ᴜ", "v":"ᴠ", "w":"ᴡ", "x":"x",
    "y":"ʏ", "z":"ᴢ"
})


def sc(text):
    return str(text).lower().translate(FONT_MAP)


def button(text, data, style="primary"):

    if style not in ("primary", "success", "danger"):
        raise ValueError("Invalid button style")

    return InlineKeyboardButton(
        text=sc(text),
        callback_data=data,
        style=style
    )


def keyboard(rows):

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# =========================================================
# USER / ADMIN
# =========================================================

def is_admin(bid, uid):

    return bool(query(
        "SELECT 1 FROM admins WHERE bot_id=? AND user_id=?",
        (bid, uid),
        "one"
    ))


def add_admin(bid, uid):

    query(
        "INSERT OR IGNORE INTO admins VALUES(?,?)",
        (bid, uid)
    )


def register_user(bid, user):

    query("""
        INSERT INTO users VALUES(?,?,?,?,?)

        ON CONFLICT(bot_id,user_id)
        DO UPDATE SET
        name=excluded.name,
        username=excluded.username

    """, (
        bid,
        user.id,
        user.first_name or "User",
        user.username or "",
        int(time.time())
    ))


def get_owner(bid):

    row = query(
        "SELECT owner_id FROM instances WHERE bot_id=?",
        (bid,),
        "one"
    )

    return row["owner_id"] if row else OWNER_ID


def set_state(bid, uid, state, data=None):

    query("""
        INSERT INTO states VALUES(?,?,?,?)

        ON CONFLICT(bot_id,user_id)
        DO UPDATE SET
        state=excluded.state,
        data=excluded.data

    """, (
        bid,
        uid,
        state,
        json.dumps(data or {})
    ))


def get_state(bid, uid):

    row = query(
        "SELECT state,data FROM states WHERE bot_id=? AND user_id=?",
        (bid, uid),
        "one"
    )

    if not row:
        return None, {}

    try:
        data = json.loads(row["data"] or "{}")
    except Exception:
        data = {}

    return row["state"], data


def clear_state(bid, uid):

    query(
        "DELETE FROM states WHERE bot_id=? AND user_id=?",
        (bid, uid)
    )


# =========================================================
# REPLY KEYBOARD
# =========================================================

def reply_menu(bid, uid):

    rows = [

        [
            KeyboardButton(text="🆕 " + sc("New Contest")),
            KeyboardButton(text="📊 " + sc("My Contests"))
        ],

        [
            KeyboardButton(text="📈 " + sc("Statistics")),
            KeyboardButton(text="🤖 " + sc("Clone Bot"))
        ],

        [
            KeyboardButton(text="❓ " + sc("Help"))
        ]

    ]

    if is_admin(bid, uid):

        rows.insert(2, [
            KeyboardButton(text="⚙️ " + sc("Admin Panel")),
            KeyboardButton(text="📢 " + sc("Vote Channel"))
        ])

        rows.insert(3, [
            KeyboardButton(text="➕ " + sc("Add Channel")),
            KeyboardButton(text="🗑 " + sc("Remove Channel"))
        ])

    if uid == OWNER_ID and bid == main_bot_id:

        rows.append([
            KeyboardButton(text="👑 " + sc("Main Admin"))
        ])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True
    )


# =========================================================
# UI CLEANUP
# =========================================================

async def cleanup(bot, bid, uid, chat_id):

    rows = query("""
        SELECT message_id
        FROM ui_messages
        WHERE bot_id=? AND user_id=? AND chat_id=?

    """, (bid, uid, chat_id), "all")

    for row in rows:

        try:

            await bot.delete_message(
                chat_id,
                row["message_id"]
            )

        except TelegramAPIError:
            pass

    query("""
        DELETE FROM ui_messages
        WHERE bot_id=? AND user_id=? AND chat_id=?

    """, (bid, uid, chat_id))


async def send_ui(message, text, markup=None):

    bot = message.bot
    bid = bot.id
    uid = message.from_user.id
    chat_id = message.chat.id

    await cleanup(
        bot,
        bid,
        uid,
        chat_id
    )

    sent = await message.answer(
        text,
        reply_markup=markup
    )

    query(
        "INSERT INTO ui_messages VALUES(?,?,?,?)",
        (bid, uid, chat_id, sent.message_id)
    )

    return sent


async def edit_ui(call, text, markup=None):

    bot = call.bot
    bid = bot.id
    uid = call.from_user.id
    chat_id = call.message.chat.id

    await cleanup(
        bot,
        bid,
        uid,
        chat_id
    )

    sent = await call.message.answer(
        text,
        reply_markup=markup
    )

    query(
        "INSERT INTO ui_messages VALUES(?,?,?,?)",
        (bid, uid, chat_id, sent.message_id)
    )


# =========================================================
# FORCE JOIN SYSTEM
# ADMIN CONFIGURATION ONLY
# =========================================================

async def check_force_join(bot, uid):

    channels = query(
        "SELECT * FROM channels WHERE bot_id=?",
        (bot.id,),
        "all"
    )

    for channel in channels:

        try:

            member = await bot.get_chat_member(
                channel["chat_id"],
                uid
            )

            if member.status in (
                ChatMemberStatus.LEFT,
                ChatMemberStatus.KICKED
            ):
                return False

        except TelegramAPIError:
            return False

    return True


async def force_join_ui(message):

    channels = query(
        "SELECT * FROM channels WHERE bot_id=?",
        (message.bot.id,),
        "all"
    )

    rows = []

    for ch in channels:

        rows.append([
            InlineKeyboardButton(
                text=sc(ch["title"]),
                url=ch["invite"]
            )
        ])

    rows.append([
        button("Verify Join", "force:verify", "success")
    ])

    await send_ui(
        message,

        "🔒 <b>CHANNEL VERIFICATION</b>\n\n"
        "Join the required channels and verify to continue.",

        keyboard(rows)
    )


# =========================================================
# CONTEST DATA
# =========================================================

def contest_data(bid, cid):

    contest = query(
        "SELECT * FROM contests WHERE bot_id=? AND id=?",
        (bid, cid),
        "one"
    )

    if not contest:
        return None, [], {}

    candidates = query(
        "SELECT * FROM candidates WHERE contest_id=?",
        (cid,),
        "all"
    )

    votes = query("""
        SELECT candidate_id,COUNT(*) AS total
        FROM votes
        WHERE bot_id=? AND contest_id=?
        GROUP BY candidate_id

    """, (bid, cid), "all")

    counts = {
        row["candidate_id"]: row["total"]
        for row in votes
    }

    return contest, candidates, counts


def vote_keyboard(cid, candidates, counts, active=True):

    rows = []

    for c in candidates:

        total = counts.get(c["id"], 0)

        rows.append([
            button(
                f"{c['name']} • {total}",
                f"vote:{cid}:{c['id']}",
                "success" if active else "danger"
            )
        ])

    if not active:

        rows.append([
            button(
                "Voting Closed",
                "noop",
                "danger"
            )
        ])

    return keyboard(rows)


# =========================================================
# PUBLISH CONTEST
# =========================================================

async def publish_contest(bot, cid):

    contest, candidates, counts = contest_data(
        bot.id,
        cid
    )

    if not contest or not candidates:
        raise ValueError("Contest or candidates missing")

    end = time.strftime(
        "%d %b %Y %H:%M UTC",
        time.gmtime(contest["ends"])
    )

    text = (

        f"🏆 <b>{html.escape(contest['title'])}</b>\n\n"

        "ᴠᴏᴛᴇ ꜰᴏʀ ʏᴏᴜʀ ꜰᴀᴠᴏᴜʀɪᴛᴇ ᴄᴀɴᴅɪᴅᴀᴛᴇ.\n\n"

        f"⏳ ᴇɴᴅꜱ: <code>{end}</code>\n\n"

        "👇 ꜱᴇʟᴇᴄᴛ ʏᴏᴜʀ ᴄᴀɴᴅɪᴅᴀᴛᴇ."

    )

    sent = await bot.send_message(
        contest["channel"],
        text,
        reply_markup=vote_keyboard(
            cid,
            candidates,
            counts
        )
    )

    query(
        "UPDATE contests SET message_id=? WHERE id=? AND bot_id=?",
        (sent.message_id, cid, bot.id)
    )


async def refresh_contest(bot, cid):

    contest, candidates, counts = contest_data(
        bot.id,
        cid
    )

    if not contest or not contest["message_id"]:
        return

    try:

        await bot.edit_message_reply_markup(
            chat_id=contest["channel"],
            message_id=contest["message_id"],
            reply_markup=vote_keyboard(
                cid,
                candidates,
                counts,
                contest["status"] == "active"
            )
        )

    except TelegramAPIError:
        pass


# =========================================================
# FINISH CONTEST
# =========================================================

async def finish_contest(bot, cid):

    contest, candidates, counts = contest_data(
        bot.id,
        cid
    )

    if not contest or contest["status"] != "active":
        return

    if time.time() < contest["ends"]:
        return

    query(
        "UPDATE contests SET status='finished' WHERE bot_id=? AND id=?",
        (bot.id, cid)
    )

    await refresh_contest(bot, cid)

    if not candidates:
        return

    winner = max(
        candidates,
        key=lambda c: (
            counts.get(c["id"], 0),
            -c["id"]
        )
    )

    total = sum(counts.values())

    text = (

        "🏆 <b>ᴠᴏᴛɪɴɢ ʀᴇꜱᴜʟᴛ</b>\n\n"

        f"👑 ᴡɪɴɴᴇʀ: <b>{html.escape(winner['name'])}</b>\n\n"

        f"🗳 ᴡɪɴɴᴇʀ ᴠᴏᴛᴇꜱ: <b>{counts.get(winner['id'], 0)}</b>\n"

        f"📊 ᴛᴏᴛᴀʟ ᴠᴏᴛᴇꜱ: <b>{total}</b>\n\n"

        "🎉 ᴄᴏɴɢʀᴀᴛᴜʟᴀᴛɪᴏɴꜱ!"

    )

    await bot.send_message(
        contest["channel"],
        text
    )


async def contest_timer(bot, cid):

    while True:

        row = query(
            "SELECT ends,status FROM contests WHERE bot_id=? AND id=?",
            (bot.id, cid),
            "one"
        )

        if not row or row["status"] != "active":
            return

        remaining = row["ends"] - time.time()

        if remaining <= 0:

            try:
                await finish_contest(bot, cid)

            except Exception:
                log.exception("Finish error: %s", cid)

            return

        await asyncio.sleep(
            min(remaining, 30)
        )


# =========================================================
# CREATE BOT ROUTER
# EACH BOT GETS ITS OWN DISPATCHER
# =========================================================

def create_router():

    router = Router()

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    @router.message(CommandStart())
    async def start(message: Message):

        bot = message.bot
        bid = bot.id
        uid = message.from_user.id

        register_user(bid, message.from_user)

        if (
            not is_admin(bid, uid)
            and not await check_force_join(bot, uid)
        ):

            await force_join_ui(message)
            return

        name = html.escape(
            message.from_user.first_name or "User"
        )

        await send_ui(
            message,

            f"👋 ʜᴇʟʟᴏ, <b>{name}</b>!\n\n"
            "ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ᴠᴏᴛᴇ ᴍᴀᴋᴇʀ.\n\n"
            "ᴄʀᴇᴀᴛᴇ ᴄᴏɴᴛᴇꜱᴛꜱ, ᴘᴜʙʟɪꜱʜ ᴠᴏᴛɪɴɢ "
            "ᴀɴᴅ ᴠɪᴇᴡ ʀᴇꜱᴜʟᴛꜱ.",

            reply_menu(bid, uid)
        )

    # -----------------------------------------------------
    # VERIFY FORCE JOIN
    # -----------------------------------------------------

    @router.callback_query(F.data == "force:verify")
    async def verify(call: CallbackQuery):

        if await check_force_join(
            call.bot,
            call.from_user.id
        ):

            await edit_ui(
                call,
                "✅ <b>VERIFICATION SUCCESSFUL</b>\n\n"
                "Send /start to continue."
            )

            await call.answer("Verified")

        else:

            await call.answer(
                "Join all required channels first.",
                show_alert=True
            )

    # -----------------------------------------------------
    # CLOSED BUTTON
    # -----------------------------------------------------

    @router.callback_query(F.data == "noop")
    async def noop(call: CallbackQuery):

        await call.answer(
            "Voting closed.",
            show_alert=True
        )

    # -----------------------------------------------------
    # VOTE
    # -----------------------------------------------------

    @router.callback_query(F.data.startswith("vote:"))
    async def vote(call: CallbackQuery):

        bot = call.bot
        bid = bot.id
        uid = call.from_user.id

        register_user(bid, call.from_user)

        if (
            not is_admin(bid, uid)
            and not await check_force_join(bot, uid)
        ):

            await call.answer(
                "Join required channels first.",
                show_alert=True
            )
            return

        try:

            _, cid, candidate_id = call.data.split(":")
            cid = int(cid)
            candidate_id = int(candidate_id)

        except ValueError:

            await call.answer("Invalid vote.", show_alert=True)
            return

        contest, candidates, counts = contest_data(
            bid,
            cid
        )

        if not contest or contest["status"] != "active":

            await call.answer(
                "Voting is closed.",
                show_alert=True
            )
            return

        if time.time() >= contest["ends"]:

            await finish_contest(bot, cid)

            await call.answer(
                "Voting ended.",
                show_alert=True
            )
            return

        if not any(
            c["id"] == candidate_id for c in candidates
        ):

            await call.answer(
                "Candidate not found.",
                show_alert=True
            )
            return

        previous = query("""
            SELECT candidate_id FROM votes
            WHERE bot_id=? AND contest_id=? AND user_id=?

        """, (bid, cid, uid), "one")

        query("""
            INSERT INTO votes VALUES(?,?,?,?,?)

            ON CONFLICT(bot_id,contest_id,user_id)
            DO UPDATE SET
            candidate_id=excluded.candidate_id,
            created=excluded.created

        """, (
            bid,
            cid,
            uid,
            candidate_id,
            int(time.time())
        ))

        await refresh_contest(bot, cid)

        await call.answer(
            "Vote changed successfully."
            if previous else "Vote submitted successfully."
        )

    # -----------------------------------------------------
    # CLONE TOKEN INPUT
    # -----------------------------------------------------

    async def create_clone(message):

        bid = message.bot.id
        uid = message.from_user.id

        if bid != main_bot_id:

            await send_ui(
                message,
                sc("Cloning is available from the main bot only.")
            )
            return

        if message.chat.type != "private":
            await send_ui(message, sc("Use private chat."))
            return

        count = query(
            "SELECT COUNT(*) AS n FROM instances WHERE owner_id=?",
            (uid,),
            "one"
        )["n"]

        if count >= MAX_CLONES_PER_USER and uid != OWNER_ID:

            await send_ui(
                message,
                sc("You reached your clone limit.")
            )
            return

        set_state(bid, uid, "clone_token")

        await send_ui(
            message,

            "🤖 <b>CREATE CLONE BOT</b>\n\n"
            "Create a bot using @BotFather.\n"
            "Send its API token here.\n\n"
            "⚠️ Never send a token belonging to someone else."
        )

    # -----------------------------------------------------
    # ADMIN PANEL
    # -----------------------------------------------------

    def admin_panel_markup():

        return keyboard([

            [
                button("Users", "panel:users", "primary"),
                button("Statistics", "panel:stats", "success")
            ],

            [
                button("Broadcast", "panel:broadcast", "primary"),
                button("Clone Bots", "panel:clones", "danger")
            ]

        ])

    @router.callback_query(F.data.startswith("panel:"))
    async def panel(call: CallbackQuery):

        bid = call.bot.id
        uid = call.from_user.id

        if not is_admin(bid, uid):

            await call.answer("Admin only.", show_alert=True)
            return

        action = call.data.split(":")[1]

        if action == "stats":

            users = query(
                "SELECT COUNT(*) AS n FROM users WHERE bot_id=?",
                (bid,), "one"
            )["n"]

            contests = query(
                "SELECT COUNT(*) AS n FROM contests WHERE bot_id=?",
                (bid,), "one"
            )["n"]

            votes = query(
                "SELECT COUNT(*) AS n FROM votes WHERE bot_id=?",
                (bid,), "one"
            )["n"]

            text = (
                "📊 <b>BOT STATISTICS</b>\n\n"
                f"Users: <b>{users}</b>\n"
                f"Contests: <b>{contests}</b>\n"
                f"Votes: <b>{votes}</b>"
            )

        elif action == "users":

            rows = query("""
                SELECT user_id,name FROM users
                WHERE bot_id=?
                ORDER BY joined DESC LIMIT 30

            """, (bid,), "all")

            text = "👥 <b>BOT USERS</b>\n\n"

            text += "\n".join(
                f"• {html.escape(r['name'])} "
                f"<code>{r['user_id']}</code>"
                for r in rows
            )

        elif action == "clones":

            if bid != main_bot_id or uid != OWNER_ID:

                await call.answer("Main owner only.", show_alert=True)
                return

            rows = query(
                "SELECT bot_id,username,owner_id FROM instances",
                mode="all"
            )

            text = "🤖 <b>REGISTERED CLONES</b>\n\n"

            text += "\n".join(
                f"@{html.escape(r['username'] or 'unknown')} "
                f"<code>{r['bot_id']}</code>"
                for r in rows
            )

        elif action == "broadcast":

            set_state(bid, uid, "broadcast")

            await edit_ui(
                call,
                "📢 <b>SEND BROADCAST MESSAGE</b>"
            )

            await call.answer()
            return

        else:
            text = "Unknown action."

        await edit_ui(
            call,
            text,
            admin_panel_markup()
        )

        await call.answer()

    # -----------------------------------------------------
    # TEXT HANDLER
    # -----------------------------------------------------

    @router.message(F.text)
    async def text_handler(message: Message):

        bot = message.bot
        bid = bot.id
        uid = message.from_user.id
        text = message.text.strip()

        register_user(bid, message.from_user)

        state, data = get_state(bid, uid)

        # CLONE TOKEN

        if state == "clone_token":

            if bid != main_bot_id:
                return

            try:
                await message.delete()
            except TelegramAPIError:
                pass

            child = None

            try:

                child = Bot(
                    text,
                    default=DefaultBotProperties(
                        parse_mode=ParseMode.HTML
                    )
                )

                me = await child.get_me()

                if me.id == main_bot_id:
                    raise ValueError("Cannot clone main bot.")

                exists = query(
                    "SELECT 1 FROM instances WHERE bot_id=?",
                    (me.id,),
                    "one"
                )

                if exists:
                    raise ValueError("Already registered.")

                query("""
                    INSERT INTO instances
                    VALUES(?,?,?,?,1,?)

                """, (
                    me.id,
                    text,
                    me.username or "",
                    uid,
                    int(time.time())
                ))

                add_admin(me.id, uid)

                clear_state(bid, uid)

                # START CLONE IMMEDIATELY
                task = asyncio.create_task(
                    start_bot(child)
                )

                running_bots[me.id] = child
                bot_tasks[me.id] = task

                await send_ui(
                    message,

                    "✅ <b>CLONE BOT CREATED!</b>\n\n"
                    f"🤖 @{html.escape(me.username or 'unknown')}\n\n"
                    "🟢 Bot startup initiated.\n"
                    "All voting features are available.",

                    reply_menu(bid, uid)
                )

            except Exception as exc:

                if child and child.id not in running_bots:

                    try:
                        await child.session.close()
                    except Exception:
                        pass

                clear_state(bid, uid)

                log.exception("Clone creation error")

                await send_ui(
                    message,
                    "❌ <b>CLONE FAILED</b>\n\n"
                    "Check token or try another token.",
                    reply_menu(bid, uid)
                )

            return

        # FORCE JOIN IS NEVER USER-MANAGEABLE

        if not is_admin(bid, uid):

            if (
                not await check_force_join(bot, uid)
            ):

                await force_join_ui(message)
                return

        # BROADCAST

        if state == "broadcast" and is_admin(bid, uid):

            clear_state(bid, uid)

            users = query(
                "SELECT user_id FROM users WHERE bot_id=?",
                (bid,),
                "all"
            )

            sent = 0
            failed = 0

            for user in users:

                try:

                    await bot.send_message(
                        user["user_id"],
                        text
                    )

                    sent += 1

                except TelegramAPIError:
                    failed += 1

                await asyncio.sleep(0.04)

            await send_ui(
                message,
                f"📢 <b>BROADCAST DONE</b>\n\n"
                f"Sent: {sent}\nFailed: {failed}",
                reply_menu(bid, uid)
            )

            return

        # ADD CHANNEL

        if state == "add_channel":

            if not is_admin(bid, uid):
                clear_state(bid, uid)
                return

            parts = text.split("|", 1)
            target = parts[0].strip()

            try:

                chat = await bot.get_chat(target)

                member = await bot.get_chat_member(
                    chat.id,
                    bot.id
                )

                if member.status not in (
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.OWNER
                ):

                    await send_ui(
                        message,
                        sc("Make the bot an administrator first.")
                    )
                    return

                if len(parts) == 2:
                    invite = parts[1].strip()

                elif chat.username:
                    invite = "https://t.me/" + chat.username

                else:
                    raise ValueError("Private channel needs invite URL.")

                if not invite.startswith("https://t.me/"):
                    raise ValueError("Invalid invite URL.")

                query("""
                    INSERT INTO channels(bot_id,chat_id,title,invite)
                    VALUES(?,?,?,?)

                    ON CONFLICT(bot_id,chat_id)
                    DO UPDATE SET
                    title=excluded.title,
                    invite=excluded.invite

                """, (
                    bid,
                    str(chat.id),
                    chat.title or "Channel",
                    invite
                ))

                clear_state(bid, uid)

                await send_ui(
                    message,
                    "✅ <b>CHANNEL ADDED</b>",
                    reply_menu(bid, uid)
                )

            except Exception:

                await send_ui(
                    message,
                    sc("Channel could not be added. Check permissions and format.")
                )

            return

        # SET VOTE CHANNEL

        if state == "vote_channel":

            if not is_admin(bid, uid):
                clear_state(bid, uid)
                return

            try:

                chat = await bot.get_chat(text)

                member = await bot.get_chat_member(
                    chat.id,
                    bot.id
                )

                if member.status not in (
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.OWNER
                ):
                    raise ValueError("Bot must be channel admin.")

                query("""
                    INSERT INTO settings VALUES(?,?)

                    ON CONFLICT(bot_id)
                    DO UPDATE SET vote_channel=excluded.vote_channel

                """, (bid, str(chat.id)))

                clear_state(bid, uid)

                await send_ui(
                    message,
                    "✅ <b>VOTING CHANNEL SET</b>",
                    reply_menu(bid, uid)
                )

            except Exception:

                await send_ui(
                    message,
                    sc("Could not set channel. Check bot permissions.")
                )

            return

        # REMOVE CHANNEL

        if state == "remove_channel":

            if not is_admin(bid, uid):
                clear_state(bid, uid)
                return

            query("""
                DELETE FROM channels
                WHERE bot_id=? AND (chat_id=? OR title=?)

            """, (bid, text, text))

            clear_state(bid, uid)

            await send_ui(
                message,
                "✅ <b>CHANNEL REMOVED</b>",
                reply_menu(bid, uid)
            )

            return

        # CONTEST TITLE

        if state == "contest_title":

            title = text[:100]

            set_state(
                bid,
                uid,
                "contest_duration",
                {"title": title}
            )

            await send_ui(
                message,

                "⏳ <b>SELECT VOTING DURATION</b>",

                keyboard([

                    [
                        button("5 HOURS", "duration:5", "primary"),
                        button("10 HOURS", "duration:10", "primary")
                    ],

                    [
                        button("24 HOURS", "duration:24", "success"),
                        button("CUSTOM", "duration:custom", "danger")
                    ]

                ])
            )

            return

        # CUSTOM DURATION

        if state == "custom_duration":

            try:

                hours = float(text)

                if not 1 <= hours <= 720:
                    raise ValueError

                title = data["title"]

                set_state(
                    bid,
                    uid,
                    "candidates",
                    {
                        "title": title,
                        "seconds": int(hours * 3600)
                    }
                )

                await send_ui(
                    message,
                    "📝 <b>SEND CANDIDATE NAMES</b>\n\n"
                    "One candidate per line."
                )

            except ValueError:

                await send_ui(
                    message,
                    sc("Enter a number between 1 and 720 hours.")
                )

            return

        # CANDIDATES

        if state == "candidates":

            names = list(dict.fromkeys(
                line.strip()[:50]
                for line in text.splitlines()
                if line.strip()
            ))

            if len(names) < 2:

                await send_ui(
                    message,
                    sc("At least 2 candidates required.")
                )
                return

            if len(names) > MAX_CANDIDATES:

                await send_ui(
                    message,
                    sc("Maximum 20 candidates.")
                )
                return

            setting = query(
                "SELECT vote_channel FROM settings WHERE bot_id=?",
                (bid,),
                "one"
            )

            if not setting or not setting["vote_channel"]:

                await send_ui(
                    message,
                    sc("Admin must set a voting channel first.")
                )
                return

            count = query("""
                SELECT COUNT(*) AS n FROM contests
                WHERE bot_id=?

            """, (bid,), "one")["n"]

            if count >= MAX_CONTESTS_PER_BOT:

                await send_ui(
                    message,
                    sc("Contest limit reached.")
                )
                return

            try:

                title = data["title"]
                seconds = data["seconds"]

                cid = query("""
                    INSERT INTO contests(
                        bot_id,creator,title,channel,created,ends,status
                    )
                    VALUES(?,?,?,?,?,?,?)

                """, (
                    bid,
                    uid,
                    title,
                    setting["vote_channel"],
                    int(time.time()),
                    int(time.time()) + seconds,
                    "active"
                ))

                for name in names:

                    query(
                        "INSERT INTO candidates(contest_id,name) VALUES(?,?)",
                        (cid, name)
                    )

                clear_state(bid, uid)

                await publish_contest(bot, cid)

                asyncio.create_task(
                    contest_timer(bot, cid)
                )

                await send_ui(
                    message,

                    "✅ <b>CONTEST PUBLISHED</b>\n\n"
                    f"Contest ID: <code>{cid}</code>\n"
                    f"Candidates: <b>{len(names)}</b>",

                    reply_menu(bid, uid)
                )

            except Exception:

                log.exception("Contest publishing error")

                await send_ui(
                    message,
                    sc("Publish failed. Check channel permissions.")
                )

            return

        # MENU: NORMAL USERS AND ADMINS

        if text.endswith(sc("Clone Bot")):

            await create_clone(message)
            return

        if text.endswith(sc("New Contest")):

            set_state(
                bid,
                uid,
                "contest_title"
            )

            await send_ui(
                message,
                "✍️ <b>ENTER CONTEST TITLE</b>"
            )

            return

        if text.endswith(sc("My Contests")):

            rows = query("""
                SELECT id,title,status
                FROM contests
                WHERE bot_id=? AND creator=?
                ORDER BY id DESC LIMIT 30

            """, (bid, uid), "all")

            if not rows:

                await send_ui(
                    message,
                    sc("You have not created any contests."),
                    reply_menu(bid, uid)
                )
                return

            lines = ["📊 <b>MY CONTESTS</b>\n"]

            for r in rows:

                lines.append(
                    f"#{r['id']} • {html.escape(r['title'])} "
                    f"• {r['status']}"
                )

            await send_ui(
                message,
                "\n".join(lines),
                reply_menu(bid, uid)
            )

            return

        if text.endswith(sc("Statistics")):

            users = query(
                "SELECT COUNT(*) AS n FROM users WHERE bot_id=?",
                (bid,), "one"
            )["n"]

            contests = query(
                "SELECT COUNT(*) AS n FROM contests WHERE bot_id=?",
                (bid,), "one"
            )["n"]

            votes = query(
                "SELECT COUNT(*) AS n FROM votes WHERE bot_id=?",
                (bid,), "one"
            )["n"]

            await send_ui(
                message,

                f"📈 <b>STATISTICS</b>\n\n"
                f"Users: <b>{users}</b>\n"
                f"Contests: <b>{contests}</b>\n"
                f"Votes: <b>{votes}</b>",

                reply_menu(bid, uid)
            )

            return

        # ADMIN ONLY MENU

        if is_admin(bid, uid):

            if text.endswith(sc("Admin Panel")):

                await send_ui(
                    message,
                    "⚙️ <b>ADMIN PANEL</b>",
                    admin_panel_markup()
                )
                return

            if text.endswith(sc("Main Admin")):

                if bid != main_bot_id or uid != OWNER_ID:
                    await send_ui(
                        message,
                        sc("Main owner only."),
                        reply_menu(bid, uid)
                    )
                    return

                await send_ui(
                    message,
                    "👑 <b>MAIN ADMIN PANEL</b>",
                    admin_panel_markup()
                )
                return

            if text.endswith(sc("Add Channel")):

                set_state(bid, uid, "add_channel")

                await send_ui(
                    message,

                    "➕ <b>ADD FORCE JOIN CHANNEL</b>\n\n"
                    "Public:\n"
                    "<code>@channelname</code>\n\n"
                    "Private:\n"
                    "<code>-100123456789|https://t.me/+invite</code>"
                )

                return

            if text.endswith(sc("Remove Channel")):

                channels = query(
                    "SELECT * FROM channels WHERE bot_id=?",
                    (bid,),
                    "all"
                )

                if not channels:

                    await send_ui(
                        message,
                        sc("No force join channels."),
                        reply_menu(bid, uid)
                    )
                    return

                lines = [
                    "🗑 <b>REMOVE FORCE JOIN CHANNEL</b>",
                    "Send channel ID or exact title:\n"
                ]

                for ch in channels:

                    lines.append(
                        f"{html.escape(ch['title'])} "
                        f"<code>{ch['chat_id']}</code>"
                    )

                set_state(bid, uid, "remove_channel")

                await send_ui(
                    message,
                    "\n".join(lines)
                )

                return

            if text.endswith(sc("Vote Channel")):

                set_state(bid, uid, "vote_channel")

                await send_ui(
                    message,
                    "📢 <b>SEND VOTING CHANNEL ID OR USERNAME</b>"
                )

                return

        if text.endswith(sc("Help")):

            await send_ui(
                message,

                "❓ <b>HELP</b>\n\n"
                "1. Create Contest\n"
                "2. Select Duration\n"
                "3. Add Candidates\n"
                "4. Publish Contest\n"
                "5. Users can vote.\n\n"
                "Normal users can create contests too.\n"
                "Force Join configuration is Admin-only.",

                reply_menu(bid, uid)
            )

            return

        await send_ui(
            message,
            sc("Select an option from the menu."),
            reply_menu(bid, uid)
        )

    return router


# =========================================================
# START ONE BOT
# SEPARATE DISPATCHER FOR EACH CLONE
# =========================================================

async def start_bot(bot):

    bid = bot.id

    dp = Dispatcher()

    dp.include_router(create_router())

    log.info("Starting bot %s", bid)

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
        handle_signals=False
    )


# =========================================================
# RESTORE CLONES
# =========================================================

async def restore_clones():

    rows = query(
        "SELECT * FROM instances WHERE enabled=1",
        mode="all"
    )

    for row in rows:

        child = None

        try:

            child = Bot(
                row["token"],
                default=DefaultBotProperties(
                    parse_mode=ParseMode.HTML
                )
            )

            me = await child.get_me()

            if me.id != row["bot_id"]:
                await child.session.close()
                continue

            running_bots[me.id] = child

            add_admin(
                me.id,
                row["owner_id"]
            )

            task = asyncio.create_task(
                start_bot(child)
            )

            bot_tasks[me.id] = task

            log.info(
                "Clone restored: @%s",
                me.username
            )

        except Exception:

            log.exception(
                "Clone restore failed: %s",
                row["bot_id"]
            )

            if child:

                try:
                    await child.session.close()
                except Exception:
                    pass


# =========================================================
# RESTORE CONTEST TIMERS
# =========================================================

async def restore_timers():

    for bid, bot in list(running_bots.items()):

        rows = query("""
            SELECT id FROM contests
            WHERE bot_id=? AND status='active'

        """, (bid,), "all")

        for row in rows:

            asyncio.create_task(
                contest_timer(bot, row["id"])
            )


# =========================================================
# MAIN
# =========================================================

async def main():

    global main_bot_id

    if BOT_TOKEN == "PASTE_MAIN_BOT_TOKEN":
        raise RuntimeError("Set your main bot token.")

    if OWNER_ID <= 0:
        raise RuntimeError("Set your OWNER_ID.")

    init_db()

    main_bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )

    me = await main_bot.get_me()

    main_bot_id = me.id

    running_bots[me.id] = main_bot

    add_admin(
        me.id,
        OWNER_ID
    )

    log.info(
        "Main bot: @%s",
        me.username
    )

    await restore_clones()

    await restore_timers()

    main_task = asyncio.create_task(
        start_bot(main_bot)
    )

    try:

        while True:

            await asyncio.sleep(10)

            # Remove finished clone tasks from memory.
            for bid, task in list(bot_tasks.items()):

                if task.done():

                    log.error(
                        "Clone bot stopped: %s",
                        bid
                    )

                    bot_tasks.pop(bid, None)

    finally:

        main_task.cancel()

        for task in list(bot_tasks.values()):
            task.cancel()

        for bot in list(running_bots.values()):

            try:
                await bot.session.close()
            except Exception:
                pass


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print("Bot stopped.")

    except Exception:
        log.exception("Fatal error")
