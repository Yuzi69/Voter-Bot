# vote_bot.py
# Python 3.10+
# Telegram Vote Bot Studio
# Aiogram 3 + SQLite + Pillow

import asyncio
import logging
import sqlite3
import time
import io
import os
import math

from PIL import Image, ImageDraw, ImageFont, ImageOps

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton, ReplyKeyboardMarkup,
    KeyboardButton, BufferedInputFile
)

# ==================================================
# CONFIGURATION
# ==================================================

BOT_TOKEN = "9163340229:AAEmiPxczk53WJFo3C6BD83fQlGMpRY2V4Q"
OWNER_ID = 9290242587  # Your Telegram numeric ID

DB = "vote_bot.db"

logging.basicConfig(level=logging.INFO)

dp = Dispatcher()
main_bot = None

active_bots = {}

# ==================================================
# DATABASE
# ==================================================

def connect():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = connect()

    con.executescript("""
    CREATE TABLE IF NOT EXISTS bots(
        bot_id INTEGER PRIMARY KEY,
        token TEXT NOT NULL,
        username TEXT,
        owner_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS admins(
        bot_id INTEGER,
        user_id INTEGER,
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
        data TEXT,
        PRIMARY KEY(bot_id,user_id)
    );

    CREATE TABLE IF NOT EXISTS contests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bot_id INTEGER,
        creator_id INTEGER,
        title TEXT,
        channel_id TEXT,
        message_id INTEGER,
        ends_at INTEGER,
        status TEXT DEFAULT 'active'
    );

    CREATE TABLE IF NOT EXISTS candidates(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contest_id INTEGER,
        name TEXT,
        photo TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS votes(
        contest_id INTEGER,
        user_id INTEGER,
        candidate_id INTEGER,
        updated_at INTEGER,
        PRIMARY KEY(contest_id,user_id)
    );
    """)

    con.commit()
    con.close()


def sql(q, args=(), mode=None):
    con = connect()
    cur = con.execute(q, args)

    if mode == "one":
        result = cur.fetchone()
    elif mode == "all":
        result = cur.fetchall()
    else:
        result = None

    con.commit()
    con.close()

    return result


def admin_add(bot_id, user_id):
    sql(
        "INSERT OR IGNORE INTO admins VALUES(?,?)",
        (bot_id, user_id)
    )


def is_admin(bot_id, user_id):
    return bool(sql(
        "SELECT 1 FROM admins WHERE bot_id=? AND user_id=?",
        (bot_id, user_id), "one"
    ))


def set_state(bot_id, user_id, state, data=""):
    sql("""
    INSERT INTO states VALUES(?,?,?,?)
    ON CONFLICT(bot_id,user_id)
    DO UPDATE SET state=excluded.state,data=excluded.data
    """, (bot_id, user_id, state, data))


def get_state(bot_id, user_id):
    r = sql(
        "SELECT state,data FROM states WHERE bot_id=? AND user_id=?",
        (bot_id, user_id), "one"
    )

    return (r["state"], r["data"]) if r else ("", "")


def clear_state(bot_id, user_id):
    sql(
        "DELETE FROM states WHERE bot_id=? AND user_id=?",
        (bot_id, user_id)
    )


# ==================================================
# BUTTON DESIGN
# ==================================================

def ib(text, data, style="primary"):
    return InlineKeyboardButton(
        text=text,
        callback_data=data,
        style=style
    )


def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="🆕 ɴᴇᴡ ᴄᴏɴᴛᴇꜱᴛ",
                    style="success"
                ),
                KeyboardButton(
                    text="📊 ᴍʏ ᴄᴏɴᴛᴇꜱᴛꜱ",
                    style="primary"
                )
            ],
            [
                KeyboardButton(
                    text="➕ ᴀᴅᴅ ᴄʜᴀɴɴᴇʟ",
                    style="primary"
                ),
                KeyboardButton(
                    text="📢 ꜱᴇᴛ ᴠᴏᴛᴇ ᴄʜᴀɴɴᴇʟ",
                    style="primary"
                )
            ],
            [
                KeyboardButton(
                    text="🤖 ᴄʟᴏɴᴇ ʙᴏᴛ",
                    style="success"
                ),
                KeyboardButton(
                    text="📈 ꜱᴛᴀᴛᴜꜱ",
                    style="primary"
                )
            ],
            [
                KeyboardButton(
                    text="🗑 ʀᴇᴍᴏᴠᴇ ᴄʜᴀɴɴᴇʟ",
                    style="danger"
                ),
                KeyboardButton(
                    text="❓ ʜᴇʟᴘ",
                    style="primary"
                )
            ]
        ],
        resize_keyboard=True
    )


def duration_buttons():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                ib("𝟱 ʜᴏᴜʀꜱ", "time:5"),
                ib("𝟭𝟬 ʜᴏᴜʀꜱ", "time:10")
            ],
            [
                ib("𝟮𝟰 ʜᴏᴜʀꜱ", "time:24"),
                ib("ᴄᴜꜱᴛᴏᴍ", "time:custom", "success")
            ]
        ]
    )


async def home(message):
    await message.answer(
        "<b>⚡ ᴠᴏᴛᴇ ʙᴏᴛ ꜱᴛᴜᴅɪᴏ</b>\n\n"
        "ᴅᴇꜱɪɢɴ • ᴅᴇᴘʟᴏʏ • ᴍᴀɴᴀɢᴇ\n\n"
        "🎯 ꜱᴇʟᴇᴄᴛ ᴀɴ ᴏᴘᴛɪᴏɴ",
        reply_markup=main_menu()
    )


# ==================================================
# CHANNEL SYSTEM
# ==================================================

def get_channels(bot_id):
    return sql(
        "SELECT * FROM channels WHERE bot_id=?",
        (bot_id,), "all"
    )


def get_vote_channel(bot_id):
    r = sql(
        "SELECT vote_channel FROM settings WHERE bot_id=?",
        (bot_id,), "one"
    )

    return r["vote_channel"] if r else None


async def check_join(bot, bot_id, user_id):

    channels = get_channels(bot_id)

    for ch in channels:
        try:
            member = await bot.get_chat_member(
                ch["chat_id"], user_id
            )

            if member.status in (
                ChatMemberStatus.LEFT,
                ChatMemberStatus.KICKED
            ):
                return False

        except Exception:
            return False

    return True


async def join_required(message):

    bot = message.bot
    bid = bot.id
    uid = message.from_user.id

    if is_admin(bid, uid):
        return True

    if await check_join(bot, bid, uid):
        return True

    rows = []

    for ch in get_channels(bid):

        rows.append([
            InlineKeyboardButton(
                text="➕ ᴊᴏɪɴ " + (ch["title"] or "ᴄʜᴀɴɴᴇʟ")[:25],
                url=ch["join_url"]
            )
        ])

    rows.append([
        ib("✅ ᴠᴇʀɪꜰʏ ᴊᴏɪɴ", "verify", "success")
    ])

    await message.answer(
        "<b>🔒 ᴊᴏɪɴ ʀᴇQᴜɪʀᴇᴅ</b>\n\n"
        "ᴊᴏɪɴ ᴀʟʟ ᴄʜᴀɴɴᴇʟꜱ ᴛᴏ ᴜꜱᴇ ᴛʜᴇ ʙᴏᴛ.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=rows
        )
    )

    return False


@dp.callback_query(F.data == "verify")
async def verify(call):

    if await check_join(
        call.bot,
        call.bot.id,
        call.from_user.id
    ):

        await call.message.edit_text(
            "✅ <b>ᴠᴇʀɪꜰɪᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ</b>\n\n"
            "ᴜꜱᴇ /start ᴛᴏ ᴄᴏɴᴛɪɴᴜᴇ."
        )

        await call.answer("Verified!")

    else:
        await call.answer(
            "সব Channel Join করুন।",
            show_alert=True
        )


# ==================================================
# CONTEST SYSTEM
# ==================================================

def contest_data(cid):

    contest = sql(
        "SELECT * FROM contests WHERE id=?",
        (cid,), "one"
    )

    candidates = sql(
        "SELECT * FROM candidates WHERE contest_id=?",
        (cid,), "all"
    )

    counts = sql("""
        SELECT candidate_id,COUNT(*) AS total
        FROM votes WHERE contest_id=?
        GROUP BY candidate_id
    """, (cid,), "all")

    vote_counts = {
        r["candidate_id"]: r["total"]
        for r in counts
    }

    return contest, candidates, vote_counts


def voting_keyboard(cid, candidates, counts, active=True):

    rows = []

    for c in candidates:

        count = counts.get(c["id"], 0)

        rows.append([
            ib(
                f"{c['name']} • {count}",
                f"vote:{cid}:{c['id']}",
                "success"
            )
        ])

    if not active:
        rows.append([
            ib("ᴠᴏᴛɪɴɢ ᴄʟᴏꜱᴇᴅ", "closed", "danger")
        ])

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def create_contest(
    bot_id,
    user_id,
    title,
    channel,
    duration,
    candidates
):

    con = connect()

    cur = con.execute("""
        INSERT INTO contests(
            bot_id,creator_id,title,channel_id,
            ends_at,status
        )
        VALUES(?,?,?,?,?,'active')
    """, (
        bot_id,
        user_id,
        title,
        channel,
        int(time.time()) + duration
    ))

    cid = cur.lastrowid

    for name, photo in candidates:

        con.execute("""
            INSERT INTO candidates(
                contest_id,name,photo
            )
            VALUES(?,?,?)
        """, (cid, name, photo))

    con.commit()
    con.close()

    return cid


async def publish_contest(bot, cid):

    c, candidates, counts = contest_data(cid)

    end_time = time.strftime(
        "%d %b %Y %H:%M UTC",
        time.gmtime(c["ends_at"])
    )

    text = (
        f"🏆 <b>{c['title']}</b>\n\n"
        "👇 ᴠᴏᴛᴇ ꜰᴏʀ ʏᴏᴜʀ ꜰᴀᴠᴏᴜʀɪᴛᴇ\n\n"
        f"⏳ ᴇɴᴅꜱ: <code>{end_time}</code>\n\n"
        "ᴄʟɪᴄᴋ ᴀ ʙᴜᴛᴛᴏɴ ᴛᴏ ᴠᴏᴛᴇ."
    )

    msg = await bot.send_message(
        c["channel_id"],
        text,
        reply_markup=voting_keyboard(
            cid, candidates, counts
        )
    )

    sql(
        "UPDATE contests SET message_id=? WHERE id=?",
        (msg.message_id, cid)
    )


async def refresh_vote(bot, cid):

    c, candidates, counts = contest_data(cid)

    if not c or not c["message_id"]:
        return

    try:

        await bot.edit_message_reply_markup(
            chat_id=c["channel_id"],
            message_id=c["message_id"],
            reply_markup=voting_keyboard(
                cid,
                candidates,
                counts,
                c["status"] == "active"
            )
        )

    except Exception as e:
        logging.warning("Vote refresh: %s", e)


@dp.callback_query(F.data == "closed")
async def closed(call):
    await call.answer(
        "Voting ended.",
        show_alert=True
    )


@dp.callback_query(F.data.startswith("vote:"))
async def vote_handler(call):

    bot = call.bot
    bid = bot.id
    uid = call.from_user.id

    if not await check_join(bot, bid, uid):

        await call.answer(
            "আগে Required Channel Join করুন।",
            show_alert=True
        )
        return

    try:
        _, cid, candidate_id = call.data.split(":")
        cid = int(cid)
        candidate_id = int(candidate_id)

    except Exception:
        await call.answer("Invalid vote.")
        return

    contest = sql(
        "SELECT * FROM contests WHERE id=? AND bot_id=?",
        (cid, bid), "one"
    )

    candidate = sql(
        "SELECT * FROM candidates WHERE id=? AND contest_id=?",
        (candidate_id, cid), "one"
    )

    if not contest or not candidate:
        await call.answer("Invalid candidate.")
        return

    if contest["status"] != "active":
        await call.answer("Voting closed.", show_alert=True)
        return

    if time.time() >= contest["ends_at"]:
        await finish_contest(bot, cid)
        await call.answer("Voting time ended.")
        return

    old = sql(
        "SELECT candidate_id FROM votes WHERE contest_id=? AND user_id=?",
        (cid, uid), "one"
    )

    sql("""
        INSERT INTO votes(
            contest_id,user_id,candidate_id,updated_at
        )
        VALUES(?,?,?,?)
        ON CONFLICT(contest_id,user_id)
        DO UPDATE SET
        candidate_id=excluded.candidate_id,
        updated_at=excluded.updated_at
    """, (
        cid, uid, candidate_id, int(time.time())
    ))

    await refresh_vote(bot, cid)

    if old:
        await call.answer(
            "Vote changed: " + candidate["name"]
        )
    else:
        await call.answer(
            "Vote submitted: " + candidate["name"]
        )


# ==================================================
# WINNER IMAGE GENERATOR
# ==================================================

def get_font(size):

    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/system/fonts/Roboto-Bold.ttf"
    ]

    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)

    return ImageFont.load_default()


def winner_image(name, photo=None):

    W, H = 1536, 614

    img = Image.new("RGB", (W, H), (65, 8, 150))

    draw = ImageDraw.Draw(img)

    # Purple gradient background
    for y in range(H):
        ratio = y / H
        color = (
            int(65 + 65 * ratio),
            int(5 + 10 * ratio),
            int(145 + 75 * ratio)
        )
        draw.line((0, y, W, y), fill=color)

    # Decorative circles
    for x, y, r in [
        (100, 100, 200),
        (700, 50, 130),
        (1400, 550, 250)
    ]:
        draw.ellipse(
            (x-r, y-r, x+r, y+r),
            outline=(150, 70, 255),
            width=8
        )

    # Heading
    draw.text(
        (70, 150),
        "VOTE WINNER",
        font=get_font(100),
        fill=(255, 240, 255),
        stroke_width=5,
        stroke_fill=(75, 0, 130)
    )

    draw.text(
        (100, 290),
        "CONGRATULATIONS!",
        font=get_font(48),
        fill=(255, 200, 30)
    )

    # Golden portrait ring
    cx, cy, radius = 1190, 245, 155

    draw.ellipse(
        (cx-radius-15, cy-radius-15,
         cx+radius+15, cy+radius+15),
        fill=(255, 190, 20)
    )

    draw.ellipse(
        (cx-radius, cy-radius,
         cx+radius, cy+radius),
        fill=(95, 15, 180)
    )

    if photo:

        try:
            portrait = Image.open(
                io.BytesIO(photo)
            ).convert("RGB")

            portrait = ImageOps.fit(
                portrait,
                (radius*2, radius*2)
            )

            mask = Image.new(
                "L", (radius*2, radius*2), 0
            )

            ImageDraw.Draw(mask).ellipse(
                (0, 0, radius*2, radius*2),
                fill=255
            )

            img.paste(
                portrait,
                (cx-radius, cy-radius),
                mask
            )

        except Exception:
            pass

    # Golden name ribbon
    draw.polygon([
        (970, 420),
        (1410, 420),
        (1460, 500),
        (920, 500)
    ], fill=(255, 185, 20))

    label = name[:24]
    font = get_font(42)

    box = draw.textbbox((0, 0), label, font=font)
    tw = box[2] - box[0]

    draw.text(
        (cx-tw/2, 435),
        label,
        font=font,
        fill=(75, 25, 0)
    )

    output = io.BytesIO()

    img.save(
        output,
        format="JPEG",
        quality=92
    )

    return output.getvalue()


async def finish_contest(bot, cid):

    contest = sql(
        "SELECT * FROM contests WHERE id=?",
        (cid,), "one"
    )

    if not contest or contest["status"] != "active":
        return

    if time.time() < contest["ends_at"]:
        return

    sql(
        "UPDATE contests SET status='finished' WHERE id=?",
        (cid,)
    )

    await refresh_vote(bot, cid)

    winner = sql("""
        SELECT c.id,c.name,c.photo,COUNT(v.user_id) AS votes
        FROM candidates c
        LEFT JOIN votes v ON c.id=v.candidate_id
        WHERE c.contest_id=?
        GROUP BY c.id
        ORDER BY votes DESC,c.id ASC
        LIMIT 1
    """, (cid,), "one")

    if not winner:
        return

    photo = None

    if winner["photo"]:
        try:
            file = await bot.get_file(winner["photo"])
            stream = io.BytesIO()

            await bot.download_file(
                file.file_path,
                destination=stream
            )

            photo = stream.getvalue()

        except Exception:
            pass

    image = winner_image(
        winner["name"],
        photo
    )

    total = sql(
        "SELECT COUNT(*) n FROM votes WHERE contest_id=?",
        (cid,), "one"
    )["n"]

    caption = (
        "🏆 <b>ᴠᴏᴛᴇ ᴡɪɴɴᴇʀ</b>\n\n"
        f"👑 <b>{winner['name']}</b>\n"
        f"🗳 ᴠᴏᴛᴇꜱ: <b>{winner['votes']}</b>\n"
        f"📊 ᴛᴏᴛᴀʟ ᴠᴏᴛᴇꜱ: {total}\n\n"
        "✨ ᴄᴏɴɢʀᴀᴛᴜʟᴀᴛɪᴏɴꜱ!"
    )

    await bot.send_photo(
        contest["channel_id"],
        BufferedInputFile(
            image,
            filename="winner.jpg"
        ),
        caption=caption
    )


async def timer(bot, cid):

    while True:

        c = sql(
            "SELECT ends_at,status FROM contests WHERE id=?",
            (cid,), "one"
        )

        if not c or c["status"] != "active":
            return

        remaining = c["ends_at"] - time.time()

        if remaining <= 0:
            await finish_contest(bot, cid)
            return

        await asyncio.sleep(
            min(remaining, 30)
        )


# ==================================================
# START COMMAND
# ==================================================

@dp.message(CommandStart())
async def start(message: Message):

    bid = message.bot.id

    if is_admin(bid, message.from_user.id):
        await home(message)
        return

    if await join_required(message):
        await message.answer(
            "👋 <b>ᴡᴇʟᴄᴏᴍᴇ</b>\n\n"
            "ᴜꜱᴇ ᴠᴏᴛᴇ ʙᴜᴛᴛᴏɴꜱ ᴛᴏ ᴘᴀʀᴛɪᴄɪᴘᴀᴛᴇ."
        )


@dp.message(Command("admin"))
async def admin(message):

    if is_admin(
        message.bot.id,
        message.from_user.id
    ):
        await home(message)
    else:
        await message.answer("Admin only.")


@dp.message(Command("addadmin"))
async def addadmin(message):

    if not is_admin(
        message.bot.id,
        message.from_user.id
    ):
        return

    parts = message.text.split(maxsplit=1)

    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer(
            "Usage: /addadmin USER_ID"
        )
        return

    admin_add(
        message.bot.id,
        int(parts[1])
    )

    await message.answer("✅ Admin added.")


# ==================================================
# DURATION CALLBACK
# ==================================================

@dp.callback_query(F.data.startswith("time:"))
async def duration(call):

    bid = call.bot.id
    uid = call.from_user.id

    if not is_admin(bid, uid):
        await call.answer("Admin only.", show_alert=True)
        return

    state, title = get_state(bid, uid)

    if state != "contest_duration":
        await call.answer("Request expired.")
        return

    value = call.data.split(":")[1]

    if value == "custom":

        set_state(
            bid, uid,
            "custom_duration",
            title
        )

        await call.message.answer(
            "Custom time ঘণ্টায় লিখুন। Example: 3, 12, 36"
        )

    else:

        seconds = int(value) * 3600

        set_state(
            bid, uid,
            "contest_candidates",
            title + "\n" + str(seconds)
        )

        await call.message.answer(
            "প্রতি লাইনে Candidate লিখুন:\n\n"
            "<code>RIFAT 5</code>\n"
            "<code>ALFA 109</code>\n"
            "<code>IRFAN 12 | PHOTO_FILE_ID</code>\n\n"
            "কমপক্ষে ২ জন Candidate দিন।"
        )

    await call.answer()


# ==================================================
# MAIN TEXT HANDLER
# ==================================================

@dp.message(F.text)
async def text_handler(message: Message):

    bot = message.bot
    bid = bot.id
    uid = message.from_user.id
    text = message.text.strip()

    state, data = get_state(bid, uid)

    # ---------------- CLONE ----------------

    if state == "clone_token":

        if bid != main_bot.id:
            await message.answer(
                "Main Bot থেকে Clone করুন।"
            )
            return

        try:

            child = Bot(
                text,
                default=DefaultBotProperties(
                    parse_mode=ParseMode.HTML
                )
            )

            me = await child.get_me()

            if me.id == main_bot.id:
                await child.session.close()
                await message.answer(
                    "Main Bot Token দেওয়া যাবে না।"
                )
                return

            sql("""
                INSERT OR REPLACE INTO bots
                VALUES(?,?,?,?)
            """, (
                me.id,
                text,
                me.username,
                uid
            ))

            admin_add(me.id, uid)

            active_bots[me.id] = child

            clear_state(bid, uid)

            await message.answer(
                "✅ <b>ʙᴏᴛ ᴄʟᴏɴᴇᴅ</b>\n\n"
                f"🤖 @{me.username}\n\n"
                "Clone registered successfully.\n"
                "নতুন Clone চালু করতে Main Bot restart করুন।"
            )

        except Exception as e:

            logging.exception(e)

            await message.answer(
                "❌ Invalid Bot Token."
            )

        return

    # ---------------- ADMIN CHECK ----------------

    if not is_admin(bid, uid):

        await join_required(message)
        return

    # ---------------- ADD CHANNEL ----------------

    if state == "add_channel":

        parts = text.split("|", 1)

        channel = parts[0].strip()

        invite = (
            parts[1].strip()
            if len(parts) > 1
            else (
                "https://t.me/" + channel[1:]
                if channel.startswith("@")
                else ""
            )
        )

        if not invite:
            await message.answer(
                "Private Channel Format:\n"
                "<code>-100123456789|https://t.me/+invite</code>"
            )
            return

        try:

            ch = await bot.get_chat(channel)

            member = await bot.get_chat_member(
                ch.id, bot.id
            )

            if member.status not in (
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER
            ):
                await message.answer(
                    "Bot-কে Channel Admin করুন।"
                )
                return

            sql("""
                INSERT OR IGNORE INTO channels(
                    bot_id,chat_id,title,join_url
                ) VALUES(?,?,?,?)
            """, (
                bid,
                str(ch.id),
                ch.title or "Channel",
                invite
            ))

            clear_state(bid, uid)

            await message.answer(
                "✅ Channel Added.",
                reply_markup=main_menu()
            )

        except Exception as e:

            await message.answer(
                "Channel add failed:\n" + str(e)[:200]
            )

        return

    # ---------------- VOTE CHANNEL ----------------

    if state == "set_vote_channel":

        try:

            ch = await bot.get_chat(text)

            member = await bot.get_chat_member(
                ch.id, bot.id
            )

            if member.status not in (
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER
            ):
                await message.answer(
                    "Bot-কে Vote Channel Admin করুন।"
                )
                return

            sql("""
                INSERT INTO settings(bot_id,vote_channel)
                VALUES(?,?)
                ON CONFLICT(bot_id)
                DO UPDATE SET vote_channel=excluded.vote_channel
            """, (bid, str(ch.id)))

            clear_state(bid, uid)

            await message.answer(
                "✅ Vote Channel Set.",
                reply_markup=main_menu()
            )

        except Exception as e:

            await message.answer(
                "Invalid Channel:\n" + str(e)[:200]
            )

        return

    # ---------------- REMOVE CHANNEL ----------------

    if state == "remove_channel":

        sql("""
            DELETE FROM channels
            WHERE bot_id=? AND (chat_id=? OR title=?)
        """, (bid, text, text))

        clear_state(bid, uid)

        await message.answer(
            "✅ Channel Removed.",
            reply_markup=main_menu()
        )

        return

    # ---------------- CONTEST TITLE ----------------

    if state == "contest_title":

        set_state(
            bid, uid,
            "contest_duration",
            text[:100]
        )

        await message.answer(
            "ᴠᴏᴛɪɴɢ ᴛɪᴍᴇ ꜱᴇʟᴇᴄᴛ ᴋᴏʀᴜɴ:",
            reply_markup=duration_buttons()
        )

        return

    # ---------------- CUSTOM TIME ----------------

    if state == "custom_duration":

        try:

            hours = float(text)

            if hours <= 0 or hours > 720:
                raise ValueError

            set_state(
                bid, uid,
                "contest_candidates",
                data + "\n" + str(int(hours * 3600))
            )

            await message.answer(
                "প্রতি লাইনে Candidate লিখুন:\n"
                "<code>RIFAT 5</code>\n"
                "<code>ALFA 109</code>\n"
                "Optional Photo:\n"
                "<code>IRFAN 12 | PHOTO_FILE_ID</code>\n\n"
                "কমপক্ষে ২ জন Candidate দিন।"
            )

        except ValueError:

            await message.answer(
                "Valid time দিন। Maximum 720 hours."
            )

        return

    # ---------------- CREATE CONTEST ----------------

    if state == "contest_candidates":

        try:

            title, seconds = data.rsplit("\n", 1)

            candidates = []

            for line in text.splitlines():

                if not line.strip():
                    continue

                parts = line.split("|", 1)

                name = parts[0].strip()[:50]

                photo = (
                    parts[1].strip()
                    if len(parts) > 1
                    else ""
                )

                if name:
                    candidates.append((name, photo))

            if len(candidates) < 2:

                await message.answer(
                    "কমপক্ষে ২ জন Candidate দিন।"
                )
                return

            channel = get_vote_channel(bid)

            if not channel:

                await message.answer(
                    "আগে Vote Channel Set করুন।"
                )
                return

            cid = create_contest(
                bid,
                uid,
                title,
                channel,
                int(seconds),
                candidates
            )

            await publish_contest(bot, cid)

            asyncio.create_task(
                timer(bot, cid)
            )

            clear_state(bid, uid)

            await message.answer(
                f"✅ Contest #{cid} Published!",
                reply_markup=main_menu()
            )

        except Exception as e:

            logging.exception(e)

            await message.answer(
                "Contest publish failed:\n" + str(e)[:200]
            )

        return

    # ==================================================
    # MENU ACTIONS
    # ==================================================

    if text == "🤖 ᴄʟᴏɴᴇ ʙᴏᴛ":

        if bid != main_bot.id:
            await message.answer(
                "Main Bot ব্যবহার করুন।"
            )
            return

        set_state(
            bid, uid,
            "clone_token"
        )

        await message.answer(
            "BotFather থেকে নতুন Bot Token পাঠান।\n"
            "Token কাউকে শেয়ার করবেন না।"
        )

    elif text == "➕ ᴀᴅᴅ ᴄʜᴀɴɴᴇʟ":

        set_state(
            bid, uid,
            "add_channel"
        )

        await message.answer(
            "Public:\n"
            "<code>@channelusername</code>\n\n"
            "Private:\n"
            "<code>-100123456789|https://t.me/+invite</code>"
        )

    elif text == "📢 ꜱᴇᴛ ᴠᴏᴛᴇ ᴄʜᴀɴɴᴇʟ":

        set_state(
            bid, uid,
            "set_vote_channel"
        )

        await message.answer(
            "Vote Channel-এর @username অথবা -100 ID দিন।"
        )

    elif text == "🗑 ʀᴇᴍᴏᴠᴇ ᴄʜᴀɴɴᴇʟ":

        chs = get_channels(bid)

        if not chs:
            await message.answer("No channels added.")
            return

        listing = "\n".join(
            f"{c['title']} | {c['chat_id']}"
            for c in chs
        )

        set_state(
            bid, uid,
            "remove_channel"
        )

        await message.answer(
            "Remove করতে Channel ID অথবা title পাঠান:\n\n"
            + listing
        )

    elif text == "🆕 ɴᴇᴡ ᴄᴏɴᴛᴇꜱᴛ":

        set_state(
            bid, uid,
            "contest_title"
        )

        await message.answer(
            "Contest Title লিখুন:"
        )

    elif text == "📊 ᴍʏ ᴄᴏɴᴛᴇꜱᴛꜱ":

        rows = sql(
            "SELECT * FROM contests WHERE bot_id=? ORDER BY id DESC LIMIT 20",
            (bid,), "all"
        )

        if not rows:
            await message.answer("No contests.")
            return

        result = ["<b>📊 ᴍʏ ᴄᴏɴᴛᴇꜱᴛꜱ</b>"]

        for r in rows:
            result.append(
                f"#{r['id']} | {r['title']} | {r['status']}"
            )

        await message.answer("\n".join(result))

    elif text == "📈 ꜱᴛᴀᴛᴜꜱ":

        total = sql(
            "SELECT COUNT(*) n FROM contests WHERE bot_id=?",
            (bid,), "one"
        )["n"]

        voters = sql("""
            SELECT COUNT(DISTINCT v.user_id) n
            FROM votes v
            JOIN contests c ON c.id=v.contest_id
            WHERE c.bot_id=?
        """, (bid,), "one")["n"]

        await message.answer(
            f"<b>📈 ꜱᴛᴀᴛᴜꜱ</b>\n\n"
            f"ᴄᴏɴᴛᴇꜱᴛꜱ: {total}\n"
            f"ᴠᴏᴛᴇʀꜱ: {voters}\n"
            f"ᴄʜᴀɴɴᴇʟꜱ: {len(get_channels(bid))}"
        )

    elif text == "❓ ʜᴇʟᴘ":

        await message.answer(
            "<b>ʜᴇʟᴘ & ɢᴜɪᴅᴇ</b>\n\n"
            "1. Add Channel\n"
            "2. Set Vote Channel\n"
            "3. New Contest\n"
            "4. Set Duration\n"
            "5. Add Candidates\n"
            "6. Publish Contest\n\n"
            "Vote can be changed before the timer ends.\n"
            "Admin add: /addadmin USER_ID"
        )

    else:

        await message.answer(
            "Menu থেকে একটি Option নির্বাচন করুন।",
            reply_markup=main_menu()
        )


# ==================================================
# STARTUP
# ==================================================

async def main():

    global main_bot

    if BOT_TOKEN == "PASTE_MAIN_BOT_TOKEN":
        raise RuntimeError("BOT_TOKEN বসাও।")

    if OWNER_ID <= 0:
        raise RuntimeError("OWNER_ID বসাও।")

    init_db()

    main_bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )

    main_id = (await main_bot.get_me()).id

    admin_add(main_id, OWNER_ID)

    bots = [main_bot]

    # Load saved clone bots
    saved = sql(
        "SELECT bot_id,token FROM bots",
        (), "all"
    )

    for row in saved:

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

            active_bots[me.id] = child
            bots.append(child)

        except Exception as e:

            logging.error(
                "Clone startup failed: %s",
                e
            )

    # Restore active timers
    for bot in bots:

        contests = sql(
            "SELECT id FROM contests WHERE bot_id=? AND status='active'",
            (bot.id,), "all"
        )

        for c in contests:

            asyncio.create_task(
                timer(bot, c["id"])
            )

    logging.info(
        "Starting %s Telegram bots",
        len(bots)
    )

    await dp.start_polling(
        *bots,
        allowed_updates=dp.resolve_used_update_types()
    )


if __name__ == "__main__":
    asyncio.run(main())
