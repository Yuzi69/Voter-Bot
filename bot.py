
# ============================================================
# VOTE MAKER BOT
# Aiogram 3 | SQLite | Multi Bot Clone | Voting System
# Small Caps Buttons | Persistent Reply Keyboard
# Auto UI Cleanup | Winner Poster | Admin Panel
# ============================================================

import asyncio
import html
import io
import logging
import sqlite3
import time

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BufferedInputFile
)
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = "9163340229:AAEmiPxczk53WJFo3C6BD83fQlGMpRY2V4Q"
OWNER_ID = 9290242587

DB_PATH = "vote_maker.sqlite3"

MAX_CLONES_PER_USER = 5
MAX_ACTIVE_CONTESTS = 20
MAX_CANDIDATES = 20
MAX_DURATION_HOURS = 720

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("VoteMaker")

router = Router()

main_bot = None
bots = {}


# ============================================================
# FONT SYSTEM - SMALL CAPS WITHOUT UNDERLINE
# ============================================================

FONT_MAP = str.maketrans({
    "a": "ᴀ",
    "b": "ʙ",
    "c": "ᴄ",
    "d": "ᴅ",
    "e": "ᴇ",
    "f": "ꜰ",
    "g": "ɢ",
    "h": "ʜ",
    "i": "ɪ",
    "j": "ᴊ",
    "k": "ᴋ",
    "l": "ʟ",
    "m": "ᴍ",
    "n": "ɴ",
    "o": "ᴏ",
    "p": "ᴘ",
    "q": "q",
    "r": "ʀ",
    "s": "ꜱ",
    "t": "ᴛ",
    "u": "ᴜ",
    "v": "ᴠ",
    "w": "ᴡ",
    "x": "x",
    "y": "ʏ",
    "z": "ᴢ"
})


def small(text):
    return str(text).lower().translate(FONT_MAP)


def ik(text, data, style="primary"):

    allowed = ("primary", "success", "danger")

    if style not in allowed:
        style = "primary"

    return InlineKeyboardButton(
        text=small(text),
        callback_data=data,
        style=style
    )


# ============================================================
# DATABASE
# ============================================================

def db():

    con = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")

    return con


def execute(query, params=(), fetch=None):

    con = db()

    try:

        cur = con.execute(query, params)

        if fetch == "one":
            result = cur.fetchone()

        elif fetch == "all":
            result = cur.fetchall()

        else:
            result = cur.lastrowid

        con.commit()

        return result

    finally:
        con.close()


def init_db():

    con = db()

    con.executescript("""
    CREATE TABLE IF NOT EXISTS bot_instances(
        bot_id INTEGER PRIMARY KEY,
        token TEXT NOT NULL,
        username TEXT,
        owner_id INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        enabled INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS admins(
        bot_id INTEGER,
        user_id INTEGER,
        PRIMARY KEY(bot_id,user_id)
    );

    CREATE TABLE IF NOT EXISTS users(
        bot_id INTEGER,
        user_id INTEGER,
        first_name TEXT,
        username TEXT,
        joined_at INTEGER,
        last_seen INTEGER,
        PRIMARY KEY(bot_id,user_id)
    );

    CREATE TABLE IF NOT EXISTS channels(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id INTEGER,
        chat_id TEXT,
        title TEXT,
        join_url TEXT,
        UNIQUE(bot_id,chat_id)
    );

    CREATE TABLE IF NOT EXISTS settings(
        bot_id INTEGER PRIMARY KEY,
        vote_channel TEXT
    );

    CREATE TABLE IF NOT EXISTS states(
        bot_id INTEGER,
        user_id INTEGER,
        state TEXT,
        data TEXT DEFAULT '',
        PRIMARY KEY(bot_id,user_id)
    );

    CREATE TABLE IF NOT EXISTS ui_messages(
        bot_id INTEGER,
        user_id INTEGER,
        chat_id INTEGER,
        message_id INTEGER,
        PRIMARY KEY(bot_id,user_id,chat_id,message_id)
    );

    CREATE TABLE IF NOT EXISTS contests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id INTEGER,
        creator_id INTEGER,
        title TEXT,
        channel TEXT,
        message_id INTEGER,
        ends_at INTEGER,
        status TEXT DEFAULT 'active',
        created_at INTEGER
    );

    CREATE TABLE IF NOT EXISTS candidates(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contest_id INTEGER,
        name TEXT,
        photo_file_id TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS votes(
        bot_id INTEGER,
        contest_id INTEGER,
        user_id INTEGER,
        candidate_id INTEGER,
        updated_at INTEGER,
        PRIMARY KEY(bot_id,contest_id,user_id)
    );

    CREATE INDEX IF NOT EXISTS idx_votes
    ON votes(bot_id,contest_id);

    CREATE INDEX IF NOT EXISTS idx_contests
    ON contests(bot_id,status);

    """)

    con.commit()
    con.close()


# ============================================================
# USER / ADMIN
# ============================================================

def is_main(bid):
    return bool(main_bot and main_bot.id == bid)


def is_admin(bid, uid):

    return bool(execute(
        "SELECT 1 FROM admins WHERE bot_id=? AND user_id=?",
        (bid, uid),
        "one"
    ))


def add_admin(bid, uid):

    execute(
        "INSERT OR IGNORE INTO admins VALUES(?,?)",
        (bid, uid)
    )


def get_bot_owner(bid):

    row = execute(
        "SELECT owner_id FROM bot_instances WHERE bot_id=?",
        (bid,),
        "one"
    )

    return row["owner_id"] if row else OWNER_ID


def register_user(bid, user):

    now = int(time.time())

    execute("""
        INSERT INTO users VALUES(?,?,?,?,?,?)

        ON CONFLICT(bot_id,user_id)
        DO UPDATE SET
        first_name=excluded.first_name,
        username=excluded.username,
        last_seen=excluded.last_seen

    """, (
        bid,
        user.id,
        user.first_name or "User",
        user.username or "",
        now,
        now
    ))


def set_state(bid, uid, state, data=""):

    execute("""
        INSERT INTO states VALUES(?,?,?,?)

        ON CONFLICT(bot_id,user_id)
        DO UPDATE SET
        state=excluded.state,
        data=excluded.data

    """, (bid, uid, state, data))


def get_state(bid, uid):

    row = execute(
        "SELECT state,data FROM states WHERE bot_id=? AND user_id=?",
        (bid, uid),
        "one"
    )

    if not row:
        return "", ""

    return row["state"], row["data"]


def clear_state(bid, uid):

    execute(
        "DELETE FROM states WHERE bot_id=? AND user_id=?",
        (bid, uid)
    )


# ============================================================
# REPLY KEYBOARD
# PERSISTENT - NEVER REMOVE
# ============================================================

def reply_menu(bid, uid):

    if is_admin(bid, uid):

        rows = [

            [
                KeyboardButton(
                    text="🆕 " + small("New Contest")
                ),
                KeyboardButton(
                    text="📊 " + small("My Contests")
                )
            ],

            [
                KeyboardButton(
                    text="🤖 " + small("Clone Bot")
                ),
                KeyboardButton(
                    text="📈 " + small("Statistics")
                )
            ],

            [
                KeyboardButton(
                    text="➕ " + small("Add Channel")
                ),
                KeyboardButton(
                    text="📢 " + small("Vote Channel")
                )
            ],

            [
                KeyboardButton(
                    text="🗑 " + small("Remove Channel")
                ),
                KeyboardButton(
                    text="❓ " + small("Help")
                )
            ]

        ]

    else:

        rows = [

            [
                KeyboardButton(
                    text="🤖 " + small("Clone Bot")
                )
            ],

            [
                KeyboardButton(
                    text="📈 " + small("Statistics")
                ),
                KeyboardButton(
                    text="❓ " + small("Help")
                )
            ]

        ]

    if uid == OWNER_ID and is_main(bid):

        rows.append([
            KeyboardButton(
                text="👑 " + small("Main Admin")
            )
        ])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True
    )


# ============================================================
# INLINE KEYBOARD STYLES
# ONLY PRIMARY / SUCCESS / DANGER
# ============================================================

def admin_inline():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                ik("Clone Bots", "adm:clones", "primary"),
                ik("Users", "adm:users", "success")
            ],

            [
                ik("Statistics", "adm:stats", "primary"),
                ik("Broadcast", "adm:broadcast", "danger")
            ]

        ]
    )


# ============================================================
# UI CLEANUP
# DELETE OLD BOT UI MESSAGES
# PRESERVE REPLY KEYBOARD
# ============================================================

async def cleanup_ui(bot, bid, uid, chat_id):

    rows = execute("""
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

    execute("""
        DELETE FROM ui_messages
        WHERE bot_id=? AND user_id=? AND chat_id=?

    """, (bid, uid, chat_id))


async def send_ui(
    message,
    text,
    markup=None,
    cleanup=True
):

    bot = message.bot

    bid = bot.id
    uid = message.from_user.id
    chat_id = message.chat.id

    if cleanup:

        await cleanup_ui(
            bot,
            bid,
            uid,
            chat_id
        )

    sent = await message.answer(
        text,
        reply_markup=markup
    )

    execute(
        "INSERT OR IGNORE INTO ui_messages VALUES(?,?,?,?)",
        (bid, uid, chat_id, sent.message_id)
    )

    return sent


async def edit_ui(call, text, markup=None):

    bot = call.bot

    bid = bot.id
    uid = call.from_user.id
    chat_id = call.message.chat.id

    await cleanup_ui(
        bot,
        bid,
        uid,
        chat_id
    )

    sent = await call.message.answer(
        text,
        reply_markup=markup
    )

    execute(
        "INSERT OR IGNORE INTO ui_messages VALUES(?,?,?,?)",
        (bid, uid, chat_id, sent.message_id)
    )


# ============================================================
# CHANNEL SUBSCRIPTION
# ============================================================

async def channels_for(bid):

    return execute(
        "SELECT * FROM channels WHERE bot_id=?",
        (bid,),
        "all"
    )


async def check_join(bot, uid):

    for ch in await channels_for(bot.id):

        try:

            member = await bot.get_chat_member(
                ch["chat_id"],
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


async def show_join(message):

    rows = []

    for ch in await channels_for(message.bot.id):

        rows.append([

            InlineKeyboardButton(
                text=small(ch["title"] or "Join Channel"),
                url=ch["join_url"]
            )

        ])

    rows.append([
        ik("Verify Join", "verify", "success")
    ])

    await send_ui(
        message,

        "🔒 <b>Channel Subscription Required</b>\n\n"
        "Join all required channels to continue.",

        InlineKeyboardMarkup(
            inline_keyboard=rows
        )
    )


@router.callback_query(F.data == "verify")
async def verify_handler(call):

    if await check_join(
        call.bot,
        call.from_user.id
    ):

        await edit_ui(
            call,
            "✅ <b>Successfully Verified!</b>\n\n"
            "Send /start to continue.",
            reply_menu(
                call.bot.id,
                call.from_user.id
            )
        )

        await call.answer("Verified!")

    else:

        await call.answer(
            "Join all required channels first.",
            show_alert=True
        )


# ============================================================
# CONTEST FUNCTIONS
# ============================================================

def get_contest(bid, cid):

    contest = execute(
        "SELECT * FROM contests WHERE bot_id=? AND id=?",
        (bid, cid),
        "one"
    )

    if not contest:
        return None, [], {}

    candidates = execute(
        "SELECT * FROM candidates WHERE contest_id=?",
        (cid,),
        "all"
    )

    vote_rows = execute("""
        SELECT candidate_id,COUNT(*) AS total
        FROM votes
        WHERE bot_id=? AND contest_id=?
        GROUP BY candidate_id

    """, (bid, cid), "all")

    counts = {
        r["candidate_id"]: r["total"]
        for r in vote_rows
    }

    return contest, candidates, counts


def vote_markup(cid, candidates, counts, active=True):

    rows = []

    for candidate in candidates:

        total = counts.get(candidate["id"], 0)

        rows.append([

            ik(
                f"{candidate['name']} • {total}",
                f"vote:{cid}:{candidate['id']}",
                "success" if active else "danger"
            )

        ])

    if not active:

        rows.append([
            ik(
                "Voting Closed",
                "noop",
                "danger"
            )
        ])

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


async def refresh_vote_message(bot, cid):

    contest, candidates, counts = get_contest(
        bot.id,
        cid
    )

    if not contest or not contest["message_id"]:
        return

    try:

        await bot.edit_message_reply_markup(
            chat_id=contest["channel"],
            message_id=contest["message_id"],

            reply_markup=vote_markup(
                cid,
                candidates,
                counts,
                contest["status"] == "active"
            )
        )

    except TelegramAPIError:
        pass


async def publish_contest(bot, cid):

    contest, candidates, counts = get_contest(
        bot.id,
        cid
    )

    if not contest or not candidates:
        raise RuntimeError("Contest not found.")

    end_time = time.strftime(
        "%d %b %Y %H:%M UTC",
        time.gmtime(contest["ends_at"])
    )

    text = (

        f"🏆 <b>{html.escape(contest['title'])}</b>\n\n"

        "ᴠᴏᴛᴇ ꜰᴏʀ ʏᴏᴜʀ ꜰᴀᴠᴏᴜʀɪᴛᴇ ᴄᴀɴᴅɪᴅᴀᴛᴇ.\n\n"

        f"⏳ ᴇɴᴅꜱ: <code>{end_time}</code>\n\n"

        "👇 ꜱᴇʟᴇᴄᴛ ʏᴏᴜʀ ᴄᴀɴᴅɪᴅᴀᴛᴇ."

    )

    sent = await bot.send_message(
        contest["channel"],
        text,

        reply_markup=vote_markup(
            cid,
            candidates,
            counts
        )
    )

    execute(
        "UPDATE contests SET message_id=? WHERE id=? AND bot_id=?",
        (sent.message_id, cid, bot.id)
    )


# ============================================================
# WINNER POSTER
# ============================================================

def load_font(size):

    paths = [

        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",

        "/system/fonts/Roboto-Bold.ttf"

    ]

    for path in paths:

        if Path(path).exists():

            try:
                return ImageFont.truetype(path, size)

            except OSError:
                pass

    return ImageFont.load_default()


def make_winner_poster(name):

    width = 1536
    height = 614

    image = Image.new(
        "RGB",
        (width, height),
        (35, 18, 75)
    )

    draw = ImageDraw.Draw(image)

    for y in range(height):

        t = y / height

        draw.line(
            (0, y, width, y),

            fill=(
                int(42 + 42*t),
                int(17 + 12*t),
                int(90 + 100*t)
            )
        )

    draw.text(
        (75, 125),
        "VOTE WINNER",
        font=load_font(105),
        fill="white"
    )

    draw.text(
        (90, 275),
        "CONGRATULATIONS!",
        font=load_font(48),
        fill=(255, 205, 75)
    )

    draw.ellipse(
        (1040, 45, 1470, 475),
        fill=(255, 194, 45)
    )

    draw.ellipse(
        (1060, 65, 1450, 455),
        fill=(94, 43, 145)
    )

    draw.rounded_rectangle(
        (900, 490, 1515, 585),
        radius=28,
        fill=(255, 194, 45)
    )

    label = name[:24]

    font = load_font(42)

    box = draw.textbbox(
        (0, 0),
        label,
        font=font
    )

    text_width = box[2] - box[0]

    draw.text(
        (1207 - text_width / 2, 510),
        label,
        font=font,
        fill=(48, 25, 70)
    )

    output = io.BytesIO()

    image.save(
        output,
        format="JPEG",
        quality=92
    )

    return output.getvalue()


# ============================================================
# FINISH CONTEST
# ============================================================

async def finish_contest(bot, cid):

    contest, candidates, counts = get_contest(
        bot.id,
        cid
    )

    if not contest:
        return

    if contest["status"] != "active":
        return

    if time.time() < contest["ends_at"]:
        return

    execute(
        "UPDATE contests SET status='finished' WHERE id=? AND bot_id=? AND status='active'",
        (cid, bot.id)
    )

    await refresh_vote_message(
        bot,
        cid
    )

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

    poster = make_winner_poster(
        winner["name"]
    )

    caption = (

        "🏆 <b>ᴠᴏᴛᴇ ᴡɪɴɴᴇʀ</b>\n\n"

        f"👑 <b>{html.escape(winner['name'])}</b>\n"

        f"🗳 ᴠᴏᴛᴇꜱ: <b>{counts.get(winner['id'], 0)}</b>\n"

        f"📊 ᴛᴏᴛᴀʟ ᴠᴏᴛᴇꜱ: <b>{total}</b>\n\n"

        "🎉 ᴄᴏɴɢʀᴀᴛᴜʟᴀᴛɪᴏɴꜱ!"

    )

    await bot.send_photo(
        contest["channel"],

        BufferedInputFile(
            poster,
            filename="winner.jpg"
        ),

        caption=caption
    )


async def contest_timer(bot, cid):

    while True:

        row = execute(
            "SELECT ends_at,status FROM contests WHERE bot_id=? AND id=?",
            (bot.id, cid),
            "one"
        )

        if not row or row["status"] != "active":
            return

        remaining = row["ends_at"] - time.time()

        if remaining <= 0:

            try:

                await finish_contest(
                    bot,
                    cid
                )

            except Exception:

                log.exception(
                    "Contest finishing failed: %s",
                    cid
                )

            return

        await asyncio.sleep(
            min(remaining, 30)
        )


# ============================================================
# START COMMAND
# ============================================================

@router.message(CommandStart())
async def start_handler(message: Message):

    bot = message.bot

    bid = bot.id
    uid = message.from_user.id

    register_user(
        bid,
        message.from_user
    )

    if (
        not is_admin(bid, uid)
        and not await check_join(bot, uid)
    ):

        await show_join(message)
        return

    name = html.escape(
        message.from_user.first_name or "User"
    )

    text = (

        f"👋 ʜᴇʟʟᴏ, <b>{name}</b>!\n\n"

        "ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ᴠᴏᴛᴇ ᴍᴀᴋᴇʀ ʙᴏᴛ.\n\n"

        "ᴄʀᴇᴀᴛᴇ ᴠᴏᴛɪɴɢ ᴄᴏɴᴛᴇꜱᴛꜱ, ᴍᴀɴᴀɢᴇ "
        "ᴠᴏᴛᴇꜱ ᴀɴᴅ ᴅɪꜱᴘʟᴀʏ ᴡɪɴɴᴇʀꜱ.\n\n"

        "👇 ꜱᴇʟᴇᴄᴛ ᴀɴ ᴏᴘᴛɪᴏɴ."

    )

    await send_ui(
        message,
        text,
        reply_menu(bid, uid)
    )


# ============================================================
# NOOP
# ============================================================

@router.callback_query(F.data == "noop")
async def noop_handler(call):

    await call.answer(
        "Voting has closed.",
        show_alert=True
    )


# ============================================================
# CLONE SYSTEM
# ============================================================

async def clone_bot_for_user(message):

    bid = message.bot.id
    uid = message.from_user.id

    if not is_main(bid):

        await send_ui(
            message,
            small("Create clones from the main bot only.")
        )
        return

    count = execute(
        "SELECT COUNT(*) AS n FROM bot_instances WHERE owner_id=?",
        (uid,),
        "one"
    )["n"]

    if (
        count >= MAX_CLONES_PER_USER
        and uid != OWNER_ID
    ):

        await send_ui(
            message,
            small("Clone limit reached. Maximum 5 bots.")
        )
        return

    if message.chat.type != "private":

        await send_ui(
            message,
            small("Open the main bot in private chat first.")
        )
        return

    set_state(
        bid,
        uid,
        "clone_token"
    )

    await send_ui(
        message,

        "🤖 <b>ᴄʟᴏɴᴇ ʙᴏᴛ</b>\n\n"

        "ᴄʀᴇᴀᴛᴇ ᴀ ʙᴏᴛ ᴡɪᴛʜ @BotFather.\n\n"

        "ꜱᴇɴᴅ ʏᴏᴜʀ ʙᴏᴛ ᴀᴘɪ ᴛᴏᴋᴇɴ.\n\n"

        "⚠️ ᴏɴʟʏ ꜱᴇɴᴅ ᴀ ᴛᴏᴋᴇɴ ꜰᴏʀ ᴀ ʙᴏᴛ ʏᴏᴜ ᴏᴡɴ."
    )


# ============================================================
# ADMIN PANEL
# ============================================================

def admin_stats_text():

    users = execute(
        "SELECT COUNT(DISTINCT user_id) AS n FROM users",
        fetch="one"
    )["n"]

    clones = execute(
        "SELECT COUNT(*) AS n FROM bot_instances",
        fetch="one"
    )["n"]

    contests = execute(
        "SELECT COUNT(*) AS n FROM contests WHERE status='active'",
        fetch="one"
    )["n"]

    return (

        "👑 <b>ᴍᴀɪɴ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ</b>\n\n"

        f"👥 ᴜꜱᴇʀꜱ: <b>{users}</b>\n"

        f"🤖 ᴄʟᴏɴᴇ ʙᴏᴛꜱ: <b>{clones}</b>\n"

        f"🏆 ᴀᴄᴛɪᴠᴇ ᴄᴏɴᴛᴇꜱᴛꜱ: <b>{contests}</b>"

    )


@router.callback_query(F.data.startswith("adm:"))
async def admin_callback(call):

    bid = call.bot.id
    uid = call.from_user.id

    if uid != OWNER_ID or not is_main(bid):

        await call.answer(
            "Owner only.",
            show_alert=True
        )
        return

    action = call.data.split(":", 1)[1]

    if action == "stats":

        await edit_ui(
            call,
            admin_stats_text(),
            admin_inline()
        )

    elif action == "clones":

        rows = execute("""
            SELECT bot_id,username,owner_id,enabled
            FROM bot_instances
            ORDER BY created_at DESC
            LIMIT 50
        """, fetch="all")

        lines = [
            "🤖 <b>ʀᴇɢɪꜱᴛᴇʀᴇᴅ ᴄʟᴏɴᴇꜱ</b>\n"
        ]

        for row in rows:

            status = "🟢" if row["enabled"] else "🔴"

            username = row["username"] or str(row["bot_id"])

            lines.append(
                f"{status} @{html.escape(username)}\n"
                f"ᴏᴡɴᴇʀ: <code>{row['owner_id']}</code>"
            )

        await edit_ui(
            call,
            "\n\n".join(lines)[:3900],
            admin_inline()
        )

    elif action == "users":

        rows = execute("""
            SELECT user_id,first_name,username
            FROM users
            WHERE bot_id=?
            ORDER BY last_seen DESC
            LIMIT 30
        """, (bid,), "all")

        lines = ["👥 <b>ᴍᴀɪɴ ʙᴏᴛ ᴜꜱᴇʀꜱ</b>\n"]

        for row in rows:

            lines.append(
                f"• {html.escape(row['first_name'])} "
                f"<code>{row['user_id']}</code>"
            )

        await edit_ui(
            call,
            "\n".join(lines)[:3900],
            admin_inline()
        )

    elif action == "broadcast":

        set_state(
            bid,
            uid,
            "broadcast"
        )

        await edit_ui(
            call,
            small("Send your broadcast message.")
        )

    await call.answer()


# ============================================================
# DURATION CALLBACK
# ============================================================

@router.callback_query(F.data.startswith("duration:"))
async def duration_handler(call):

    bid = call.bot.id
    uid = call.from_user.id

    if not is_admin(bid, uid):

        await call.answer(
            "Admin only.",
            show_alert=True
        )
        return

    state, title = get_state(bid, uid)

    if state != "contest_duration":

        await call.answer(
            "Request expired.",
            show_alert=True
        )
        return

    choice = call.data.split(":", 1)[1]

    if choice == "custom":

        set_state(
            bid,
            uid,
            "custom_duration",
            title
        )

        await edit_ui(
            call,
            small("Enter duration in hours (1–720).")
        )

    else:

        hours = int(choice)

        set_state(
            bid,
            uid,
            "candidates",
            title + "\n" + str(hours * 3600)
        )

        await edit_ui(
            call,

            small("Send candidate names, one per line.")

            + "\n\n<code>RIFAT\nALFA\nIRFAN</code>"
        )

    await call.answer()


# ============================================================
# VOTE CALLBACK
# ============================================================

@router.callback_query(F.data.startswith("vote:"))
async def vote_handler(call):

    bot = call.bot
    bid = bot.id
    uid = call.from_user.id

    register_user(
        bid,
        call.from_user
    )

    if (
        not is_admin(bid, uid)
        and not await check_join(bot, uid)
    ):

        await call.answer(
            "Join all required channels first.",
            show_alert=True
        )
        return

    try:

        _, cid_text, candidate_text = call.data.split(":")

        cid = int(cid_text)
        candidate_id = int(candidate_text)

    except ValueError:

        await call.answer(
            "Invalid vote.",
            show_alert=True
        )
        return

    contest, candidates, counts = get_contest(
        bid,
        cid
    )

    if not contest or contest["status"] != "active":

        await call.answer(
            "Voting is closed.",
            show_alert=True
        )
        return

    if time.time() >= contest["ends_at"]:

        await finish_contest(bot, cid)

        await call.answer(
            "Voting has ended.",
            show_alert=True
        )
        return

    if not any(
        c["id"] == candidate_id
        for c in candidates
    ):

        await call.answer(
            "Candidate not found.",
            show_alert=True
        )
        return

    previous = execute("""
        SELECT candidate_id
        FROM votes
        WHERE bot_id=? AND contest_id=? AND user_id=?

    """, (bid, cid, uid), "one")

    execute("""
        INSERT INTO votes(
            bot_id,contest_id,user_id,candidate_id,updated_at
        )
        VALUES(?,?,?,?,?)

        ON CONFLICT(bot_id,contest_id,user_id)
        DO UPDATE SET
        candidate_id=excluded.candidate_id,
        updated_at=excluded.updated_at

    """, (
        bid,
        cid,
        uid,
        candidate_id,
        int(time.time())
    ))

    await refresh_vote_message(
        bot,
        cid
    )

    await call.answer(
        "Vote changed successfully."
        if previous
        else "Vote submitted successfully."
    )


# ============================================================
# MAIN TEXT HANDLER
# ============================================================

@router.message(F.text)
async def text_handler(message: Message):

    bot = message.bot
    bid = bot.id
    uid = message.from_user.id

    text = message.text.strip()

    state, data = get_state(bid, uid)

    register_user(
        bid,
        message.from_user
    )

    # --------------------------------------------------------
    # CLONE TOKEN
    # --------------------------------------------------------

    if state == "clone_token":

        if not is_main(bid):
            return

        if message.chat.type != "private":

            await send_ui(
                message,
                small("Send the token in private chat.")
            )
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

            if me.id == main_bot.id:
                raise ValueError("Main bot token cannot be cloned.")

            existing = execute(
                "SELECT 1 FROM bot_instances WHERE bot_id=?",
                (me.id,),
                "one"
            )

            if existing:
                raise ValueError("Already registered.")

            execute("""
                INSERT INTO bot_instances(
                    bot_id,token,username,owner_id,created_at,enabled
                )
                VALUES(?,?,?,?,?,1)
            """, (
                me.id,
                text,
                me.username or "",
                uid,
                int(time.time())
            ))

            add_admin(
                me.id,
                uid
            )

            bots[me.id] = child

            clear_state(bid, uid)

            await send_ui(
                message,

                "✅ <b>ᴄʟᴏɴᴇ ʙᴏᴛ ʀᴇɢɪꜱᴛᴇʀᴇᴅ!</b>\n\n"

                f"🤖 @{html.escape(me.username or 'unknown')}\n\n"

                "ʀᴇꜱᴛᴀʀᴛ ʜᴏꜱᴛɪɴɢ ᴛᴏ ᴀᴄᴛɪᴠᴀᴛᴇ ᴛʜᴇ ɴᴇᴡ ʙᴏᴛ.",

                reply_menu(bid, uid)
            )

        except Exception as exc:

            if child:

                try:
                    await child.session.close()
                except Exception:
                    pass

            clear_state(bid, uid)

            log.warning(
                "Clone registration failed: %s",
                str(exc)[:100]
            )

            await send_ui(
                message,
                small("Invalid token or clone registration failed.")
            )

        return

    # --------------------------------------------------------
    # BROADCAST
    # --------------------------------------------------------

    if (
        state == "broadcast"
        and uid == OWNER_ID
        and is_main(bid)
    ):

        clear_state(bid, uid)

        rows = execute(
            "SELECT user_id FROM users WHERE bot_id=?",
            (bid,),
            "all"
        )

        sent = 0
        failed = 0

        for row in rows:

            try:

                await bot.send_message(
                    row["user_id"],
                    text
                )

                sent += 1

            except TelegramAPIError:

                failed += 1

            await asyncio.sleep(0.05)

        await send_ui(
            message,

            f"📢 <b>Broadcast Completed</b>\n\n"
            f"✅ Sent: {sent}\n"
            f"❌ Failed: {failed}",

            reply_menu(bid, uid)
        )

        return

    # --------------------------------------------------------
    # ADD ADMIN COMMAND
    # --------------------------------------------------------

    if text.startswith("/addadmin") and is_admin(bid, uid):

        parts = text.split()

        if len(parts) == 2 and parts[1].isdigit():

            add_admin(
                bid,
                int(parts[1])
            )

            await send_ui(
                message,
                "✅ ᴀᴅᴍɪɴ ᴀᴅᴅᴇᴅ.",
                reply_menu(bid, uid)
            )

        else:

            await send_ui(
                message,
                small("Usage: /addadmin USER_ID")
            )

        return

    # --------------------------------------------------------
    # ADMIN ACCESS
    # --------------------------------------------------------

    if not is_admin(bid, uid):

        if not await check_join(bot, uid):

            await show_join(message)
            return

        if text.endswith(small("Clone Bot")) and is_main(bid):

            await clone_bot_for_user(message)
            return

        if text.endswith(small("Statistics")):

            users = execute(
                "SELECT COUNT(*) AS n FROM users WHERE bot_id=?",
                (bid,),
                "one"
            )["n"]

            contests = execute(
                "SELECT COUNT(*) AS n FROM contests WHERE bot_id=?",
                (bid,),
                "one"
            )["n"]

            votes = execute(
                "SELECT COUNT(*) AS n FROM votes WHERE bot_id=?",
                (bid,),
                "one"
            )["n"]

            await send_ui(
                message,

                f"📈 <b>Statistics</b>\n\n"
                f"👥 Users: {users}\n"
                f"🏆 Contests: {contests}\n"
                f"🗳 Votes: {votes}",

                reply_menu(bid, uid)
            )

            return

        if text.endswith(small("Help")):

            await send_ui(
                message,
                small("Use Clone Bot to create your own voting bot."),
                reply_menu(bid, uid)
            )

            return

        await send_ui(
            message,
            small("Use /start to open the menu."),
            reply_menu(bid, uid)
        )

        return

    # ========================================================
    # ADMIN STATES
    # ========================================================

    # --------------------------------------------------------
    # ADD CHANNEL
    # --------------------------------------------------------

    if state == "add_channel":

        parts = text.split("|", 1)

        target = parts[0].strip()

        try:

            ch = await bot.get_chat(target)

            member = await bot.get_chat_member(
                ch.id,
                bot.id
            )

            if member.status not in (
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER
            ):

                await send_ui(
                    message,
                    small("Make bot administrator in this channel.")
                )
                return

            if len(parts) > 1:

                url = parts[1].strip()

            elif ch.username:

                url = "https://t.me/" + ch.username

            else:

                await send_ui(
                    message,
                    small("Private channel requires an invite URL after |.")
                )
                return

            if not url.startswith("https://t.me/"):
                await send_ui(
                    message,
                    small("Invalid Telegram invite URL.")
                )
                return

            execute("""
                INSERT INTO channels(
                    bot_id,chat_id,title,join_url
                )
                VALUES(?,?,?,?)

                ON CONFLICT(bot_id,chat_id)
                DO UPDATE SET
                title=excluded.title,
                join_url=excluded.join_url

            """, (
                bid,
                str(ch.id),
                ch.title or "Channel",
                url
            ))

            clear_state(bid, uid)

            await send_ui(
                message,
                "✅ ᴄʜᴀɴɴᴇʟ ᴀᴅᴅᴇᴅ!",
                reply_menu(bid, uid)
            )

        except Exception:

            await send_ui(
                message,
                small("Could not add channel. Check ID and permissions.")
            )

        return

    # --------------------------------------------------------
    # VOTE CHANNEL
    # --------------------------------------------------------

    if state == "vote_channel":

        try:

            ch = await bot.get_chat(text)

            member = await bot.get_chat_member(
                ch.id,
                bot.id
            )

            if member.status not in (
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER
            ):

                await send_ui(
                    message,
                    small("Make bot administrator in the voting channel.")
                )
                return

            execute("""
                INSERT INTO settings(bot_id,vote_channel)
                VALUES(?,?)

                ON CONFLICT(bot_id)
                DO UPDATE SET vote_channel=excluded.vote_channel

            """, (
                bid,
                str(ch.id)
            ))

            clear_state(bid, uid)

            await send_ui(
                message,
                "✅ ᴠᴏᴛᴇ ᴄʜᴀɴɴᴇʟ ꜱᴇᴛ!",
                reply_menu(bid, uid)
            )

        except Exception:

            await send_ui(
                message,
                small("Could not set voting channel.")
            )

        return

    # --------------------------------------------------------
    # REMOVE CHANNEL
    # --------------------------------------------------------

    if state == "remove_channel":

        execute("""
            DELETE FROM channels
            WHERE bot_id=? AND (chat_id=? OR title=?)

        """, (bid, text, text))

        clear_state(bid, uid)

        await send_ui(
            message,
            "✅ ᴄʜᴀɴɴᴇʟ ʀᴇᴍᴏᴠᴇᴅ!",
            reply_menu(bid, uid)
        )

        return

    # --------------------------------------------------------
    # CONTEST TITLE
    # --------------------------------------------------------

    if state == "contest_title":

        title = text.replace("\n", " ")[:100]

        set_state(
            bid,
            uid,
            "contest_duration",
            title
        )

        await send_ui(
            message,

            "⏳ <b>SELECT VOTING DURATION</b>",

            InlineKeyboardMarkup(
                inline_keyboard=[

                    [
                        ik("5 HOURS", "duration:5", "primary"),
                        ik("10 HOURS", "duration:10", "primary")
                    ],

                    [
                        ik("24 HOURS", "duration:24", "success"),
                        ik("CUSTOM", "duration:custom", "danger")
                    ]

                ]
            )
        )

        return

    # --------------------------------------------------------
    # CUSTOM DURATION
    # --------------------------------------------------------

    if state == "custom_duration":

        try:

            hours = float(text)

            if not 0 < hours <= MAX_DURATION_HOURS:
                raise ValueError

            set_state(
                bid,
                uid,
                "candidates",
                data + "\n" + str(int(hours * 3600))
            )

            await send_ui(
                message,

                small("Send candidate names, one per line.")

                + "\n\n<code>RIFAT\nALFA\nIRFAN</code>"
            )

        except ValueError:

            await send_ui(
                message,
                small("Enter a duration between 1 and 720 hours.")
            )

        return

    # --------------------------------------------------------
    # CANDIDATES
    # --------------------------------------------------------

    if state == "candidates":

        try:

            title, seconds = data.rsplit("\n", 1)

            names = [
                line.strip()[:50]
                for line in text.splitlines()
                if line.strip()
            ]

            names = list(dict.fromkeys(names))

            if len(names) < 2:

                await send_ui(
                    message,
                    small("At least two candidates are required.")
                )
                return

            if len(names) > MAX_CANDIDATES:

                await send_ui(
                    message,
                    small("Maximum 20 candidates allowed.")
                )
                return

            setting = execute(
                "SELECT vote_channel FROM settings WHERE bot_id=?",
                (bid,),
                "one"
            )

            if not setting or not setting["vote_channel"]:

                await send_ui(
                    message,
                    small("Set voting channel first.")
                )
                return

            active = execute("""
                SELECT COUNT(*) AS n
                FROM contests
                WHERE bot_id=? AND status='active'

            """, (bid,), "one")["n"]

            if active >= MAX_ACTIVE_CONTESTS:

                await send_ui(
                    message,
                    small("Active contest limit reached.")
                )
                return

            cid = execute("""
                INSERT INTO contests(
                    bot_id,creator_id,title,channel,ends_at,created_at
                )
                VALUES(?,?,?,?,?,?)

            """, (
                bid,
                uid,
                title,
                setting["vote_channel"],
                int(time.time()) + int(seconds),
                int(time.time())
            ))

            for name in names:

                execute(
                    "INSERT INTO candidates(contest_id,name) VALUES(?,?)",
                    (cid, name)
                )

            clear_state(bid, uid)

            await publish_contest(
                bot,
                cid
            )

            asyncio.create_task(
                contest_timer(bot, cid)
            )

            await send_ui(
                message,

                "✅ <b>ᴄᴏɴᴛᴇꜱᴛ ᴘᴜʙʟɪꜱʜᴇᴅ!</b>\n\n"
                f"ᴄᴏɴᴛᴇꜱᴛ ɪᴅ: <code>{cid}</code>",

                reply_menu(bid, uid)
            )

        except Exception:

            log.exception(
                "Contest creation failed"
            )

            await send_ui(
                message,
                small("Could not publish contest. Check channel permissions.")
            )

        return

    # ========================================================
    # MENU ACTIONS
    # ========================================================

    if text.endswith(small("New Contest")):

        set_state(
            bid,
            uid,
            "contest_title"
        )

        await send_ui(
            message,
            "✍️ ᴇɴᴛᴇʀ ᴄᴏɴᴛᴇꜱᴛ ᴛɪᴛʟᴇ:"
        )

        return

    if text.endswith(small("My Contests")):

        rows = execute("""
            SELECT id,title,status
            FROM contests
            WHERE bot_id=?
            ORDER BY id DESC
            LIMIT 20

        """, (bid,), "all")

        if not rows:

            await send_ui(
                message,
                small("No contests found."),
                reply_menu(bid, uid)
            )

            return

        lines = ["📊 <b>ᴍʏ ᴄᴏɴᴛᴇꜱᴛꜱ</b>\n"]

        for row in rows:

            lines.append(
                f"#{row['id']} • "
                f"{html.escape(row['title'])} • "
                f"{row['status']}"
            )

        await send_ui(
            message,
            "\n".join(lines),
            reply_menu(bid, uid)
        )

        return

    if text.endswith(small("Clone Bot")):

        await clone_bot_for_user(message)
        return

    if text.endswith(small("Add Channel")):

        set_state(
            bid,
            uid,
            "add_channel"
        )

        await send_ui(
            message,

            "➕ <b>ADD REQUIRED CHANNEL</b>\n\n"

            "ᴘᴜʙʟɪᴄ:\n"
            "<code>@channelname</code>\n\n"

            "ᴘʀɪᴠᴀᴛᴇ:\n"
            "<code>-100123456789|https://t.me/+invite</code>"
        )

        return

    if text.endswith(small("Vote Channel")):

        set_state(
            bid,
            uid,
            "vote_channel"
        )

        await send_ui(
            message,
            small("Send voting channel username or ID.")
        )

        return

    if text.endswith(small("Remove Channel")):

        rows = await channels_for(bid)

        if not rows:

            await send_ui(
                message,
                small("No channels added."),
                reply_menu(bid, uid)
            )
            return

        lines = [
            "🗑 <b>REMOVE CHANNEL</b>\n",
            small("Send channel ID or exact title:")
        ]

        for row in rows:

            lines.append(
                f"{html.escape(row['title'])} | "
                f"<code>{row['chat_id']}</code>"
            )

        set_state(
            bid,
            uid,
            "remove_channel"
        )

        await send_ui(
            message,
            "\n".join(lines)
        )

        return

    if text.endswith(small("Statistics")):

        users = execute(
            "SELECT COUNT(*) AS n FROM users WHERE bot_id=?",
            (bid,),
            "one"
        )["n"]

        contests = execute(
            "SELECT COUNT(*) AS n FROM contests WHERE bot_id=?",
            (bid,),
            "one"
        )["n"]

        votes = execute(
            "SELECT COUNT(*) AS n FROM votes WHERE bot_id=?",
            (bid,),
            "one"
        )["n"]

        await send_ui(
            message,

            f"📈 <b>ꜱᴛᴀᴛɪꜱᴛɪᴄꜱ</b>\n\n"
            f"👥 ᴜꜱᴇʀꜱ: <b>{users}</b>\n"
            f"🏆 ᴄᴏɴᴛᴇꜱᴛꜱ: <b>{contests}</b>\n"
            f"🗳 ᴠᴏᴛᴇꜱ: <b>{votes}</b>",

            reply_menu(bid, uid)
        )

        return

    if text.endswith(small("Help")):

        await send_ui(
            message,

            "❓ <b>ʜᴇʟᴘ ᴍᴇɴᴜ</b>\n\n"

            "1. ᴀᴅᴅ ʀᴇQᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟ.\n"
            "2. ꜱᴇᴛ ᴠᴏᴛᴇ ᴄʜᴀɴɴᴇʟ.\n"
            "3. ᴄʀᴇᴀᴛᴇ ᴄᴏɴᴛᴇꜱᴛ.\n"
            "4. ꜱᴇʟᴇᴄᴛ ᴅᴜʀᴀᴛɪᴏɴ.\n"
            "5. ᴀᴅᴅ ᴄᴀɴᴅɪᴅᴀᴛᴇꜱ.\n"
            "6. ᴘᴜʙʟɪꜱʜ ᴄᴏɴᴛᴇꜱᴛ.",

            reply_menu(bid, uid)
        )

        return

    if (
        text.endswith(small("Main Admin"))
        and uid == OWNER_ID
        and is_main(bid)
    ):

        await send_ui(
            message,
            admin_stats_text(),
            admin_inline()
        )

        return

    await send_ui(
        message,
        small("Use the menu buttons or /start."),
        reply_menu(bid, uid)
    )


# ============================================================
# STARTUP
# ============================================================

async def main():

    global main_bot

    if BOT_TOKEN == "PASTE_MAIN_BOT_TOKEN":
        raise RuntimeError("Set BOT_TOKEN first.")

    if OWNER_ID <= 0:
        raise RuntimeError("Set OWNER_ID first.")

    init_db()

    main_bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )

    me = await main_bot.get_me()

    add_admin(
        me.id,
        OWNER_ID
    )

    bots[me.id] = main_bot

    log.info(
        "Main bot connected: @%s",
        me.username
    )

    # RESTORE CLONES

    saved = execute(
        "SELECT bot_id,token FROM bot_instances WHERE enabled=1",
        fetch="all"
    )

    for row in saved:

        child = None

        try:

            child = Bot(
                row["token"],
                default=DefaultBotProperties(
                    parse_mode=ParseMode.HTML
                )
            )

            clone_me = await child.get_me()

            if clone_me.id != row["bot_id"]:

                await child.session.close()
                continue

            bots[clone_me.id] = child

            add_admin(
                clone_me.id,
                get_bot_owner(clone_me.id)
            )

            log.info(
                "Clone restored: @%s",
                clone_me.username
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

    # RESTORE ACTIVE CONTESTS

    for bid, bot in list(bots.items()):

        active = execute(
            "SELECT id FROM contests WHERE bot_id=? AND status='active'",
            (bid,),
            "all"
        )

        for row in active:

            asyncio.create_task(
                contest_timer(
                    bot,
                    row["id"]
                )
            )

    # START ALL BOTS

    dp = Dispatcher()

    dp.include_router(router)

    log.info(
        "Vote Maker Started | Total Bots: %s",
        len(bots)
    )

    await dp.start_polling(
        *list(bots.values()),
        allowed_updates=dp.resolve_used_update_types()
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        print("Vote Maker Bot stopped.")

    except Exception:

        log.exception("Bot startup failed.")
