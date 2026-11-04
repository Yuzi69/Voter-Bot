import asyncio
import sqlite3
import time
import io
import logging

from PIL import Image, ImageDraw, ImageFont

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BufferedInputFile
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus

# ==========================================
# CONFIG
# ==========================================

BOT_TOKEN = "9163340229:AAEmiPxczk53WJFo3C6BD83fQlGMpRY2V4Q"
OWNER_ID = 9290242587

DB = "vote_maker.db"

logging.basicConfig(level=logging.INFO)

dp = Dispatcher()
main_bot = None


# ==========================================
# DATABASE
# ==========================================

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()

    con.executescript("""
    CREATE TABLE IF NOT EXISTS admins(
        user_id INTEGER PRIMARY KEY
    );

    CREATE TABLE IF NOT EXISTS channels(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id TEXT,
        title TEXT,
        url TEXT
    );

    CREATE TABLE IF NOT EXISTS settings(
        id INTEGER PRIMARY KEY,
        vote_channel TEXT
    );

    CREATE TABLE IF NOT EXISTS states(
        user_id INTEGER PRIMARY KEY,
        state TEXT,
        data TEXT
    );

    CREATE TABLE IF NOT EXISTS contests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        channel TEXT,
        message_id INTEGER,
        ends_at INTEGER,
        status TEXT DEFAULT 'active'
    );

    CREATE TABLE IF NOT EXISTS candidates(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contest_id INTEGER,
        name TEXT,
        photo TEXT
    );

    CREATE TABLE IF NOT EXISTS votes(
        contest_id INTEGER,
        user_id INTEGER,
        candidate_id INTEGER,
        PRIMARY KEY(contest_id,user_id)
    );
    """)

    con.execute(
        "INSERT OR IGNORE INTO admins VALUES(?)",
        (OWNER_ID,)
    )

    con.commit()
    con.close()


def query(sql, args=(), fetch=False, all_rows=False):
    con = db()
    cur = con.execute(sql, args)

    if all_rows:
        result = cur.fetchall()
    elif fetch:
        result = cur.fetchone()
    else:
        result = None

    con.commit()
    con.close()

    return result


def set_state(uid, state, data=""):
    query("""
        INSERT INTO states VALUES(?,?,?)
        ON CONFLICT(user_id)
        DO UPDATE SET state=excluded.state,
                      data=excluded.data
    """, (uid, state, data))


def get_state(uid):
    r = query(
        "SELECT * FROM states WHERE user_id=?",
        (uid,), fetch=True
    )
    return (r["state"], r["data"]) if r else ("", "")


def clear_state(uid):
    query("DELETE FROM states WHERE user_id=?", (uid,))


def is_admin(uid):
    return bool(query(
        "SELECT 1 FROM admins WHERE user_id=?",
        (uid,), fetch=True
    ))


# ==========================================
# BUTTON HELPERS
# ==========================================

def under(text):
    return "".join(
        char + "\u0332" for char in text
    )


def button(text, data, style="primary"):
    return InlineKeyboardButton(
        text=under(text),
        callback_data=data,
        style=style
    )


def menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=under("🆕 NEW CONTEST"),
                    style="success"
                ),
                KeyboardButton(
                    text=under("📊 MY CONTESTS"),
                    style="primary"
                )
            ],
            [
                KeyboardButton(
                    text=under("➕ ADD CHANNEL"),
                    style="primary"
                ),
                KeyboardButton(
                    text=under("📢 VOTE CHANNEL"),
                    style="primary"
                )
            ],
            [
                KeyboardButton(
                    text=under("📈 STATISTICS"),
                    style="primary"
                ),
                KeyboardButton(
                    text=under("🗑 REMOVE CHANNEL"),
                    style="danger"
                )
            ],
            [
                KeyboardButton(
                    text=under("❓ HELP"),
                    style="primary"
                )
            ]
        ],
        resize_keyboard=True
    )


# ==========================================
# WELCOME
# ==========================================

@dp.message(CommandStart())
async def start(message: Message):

    name = message.from_user.first_name or "User"

    welcome = (
        f"👋 ʜᴇʟʟᴏ, <b>{name}</b>!\n\n"
        "ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ᴠᴏᴛᴇ ᴍᴀᴋᴇʀ ʙᴏᴛ.\n\n"
        "ᴄʀᴇᴀᴛᴇ ᴠᴏᴛɪɴɢ ᴄᴏɴᴛᴇꜱᴛꜱ, "
        "ᴍᴀɴᴀɢᴇ ᴠᴏᴛᴇꜱ ᴀɴᴅ ᴅɪꜱᴘʟᴀʏ ᴡɪɴɴᴇʀꜱ.\n\n"
        "👇 ꜱᴇʟᴇᴄᴛ ᴀɴ ᴏᴘᴛɪᴏɴ ʙᴇʟᴏᴡ."
    )

    if is_admin(message.from_user.id):
        await message.answer(
            welcome,
            reply_markup=menu()
        )
    else:
        if not await check_join(
            message.bot,
            message.from_user.id
        ):
            await show_join(message)
            return

        await message.answer(welcome)


# ==========================================
# FORCE SUBSCRIPTION
# ==========================================

async def check_join(bot, uid):

    channels = query(
        "SELECT * FROM channels",
        all_rows=True
    )

    for ch in channels:
        try:
            member = await bot.get_chat_member(
                ch["chat_id"], uid
            )

            if member.status in (
                ChatMemberStatus.LEFT,
                ChatMemberStatus.KICKED
            ):
                return False

        except Exception:
            return False

    return True


async def show_join(message):

    channels = query(
        "SELECT * FROM channels",
        all_rows=True
    )

    rows = []

    for ch in channels:
        rows.append([
            InlineKeyboardButton(
                text="➕ " + ch["title"],
                url=ch["url"]
            )
        ])

    rows.append([
        button(
            "VERIFY JOIN",
            "verify",
            "success"
        )
    ])

    await message.answer(
        "🔒 <b>CHANNEL SUBSCRIPTION REQUIRED</b>\n\n"
        "ᴊᴏɪɴ ᴀʟʟ ʀᴇQᴜɪʀᴇᴅ ᴄʜᴀɴɴᴇʟꜱ "
        "ᴛᴏ ᴄᴏɴᴛɪɴᴜᴇ.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=rows
        )
    )


@dp.callback_query(F.data == "verify")
async def verify(call):

    if await check_join(
        call.bot,
        call.from_user.id
    ):
        await call.message.edit_text(
            "✅ ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ "
            "ᴄᴏᴍᴘʟᴇᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ."
        )
        await call.answer("Verified!")

    else:
        await call.answer(
            "Join all required channels first.",
            show_alert=True
        )


# ==========================================
# ADMIN
# ==========================================

@dp.message(Command("admin"))
async def admin(message):

    if is_admin(message.from_user.id):
        await message.answer(
            "⚙️ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ",
            reply_markup=menu()
        )
    else:
        await message.answer("Access denied.")


@dp.message(Command("addadmin"))
async def add_admin(message):

    if not is_admin(message.from_user.id):
        return

    args = message.text.split()

    if len(args) != 2 or not args[1].isdigit():
        await message.answer(
            "Usage: /addadmin USER_ID"
        )
        return

    query(
        "INSERT OR IGNORE INTO admins VALUES(?)",
        (int(args[1]),)
    )

    await message.answer(
        "✅ ᴀᴅᴍɪɴ ᴀᴅᴅᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ."
    )


# ==========================================
# CONTEST CREATION
# ==========================================

@dp.message(F.text)
async def text_handler(message: Message):

    uid = message.from_user.id
    text = message.text.strip()

    if not is_admin(uid):

        if not await check_join(message.bot, uid):
            await show_join(message)

        return

    state, data = get_state(uid)

    # ADD CHANNEL

    if state == "add_channel":

        parts = text.split("|", 1)

        channel = parts[0].strip()

        try:
            ch = await message.bot.get_chat(channel)

            member = await message.bot.get_chat_member(
                ch.id, message.bot.id
            )

            if member.status not in (
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER
            ):
                await message.answer(
                    "Make the bot an administrator first."
                )
                return

            url = (
                parts[1].strip()
                if len(parts) > 1
                else "https://t.me/" + channel.lstrip("@")
            )

            query(
                "INSERT INTO channels(chat_id,title,url) VALUES(?,?,?)",
                (str(ch.id), ch.title or "Channel", url)
            )

            clear_state(uid)

            await message.answer(
                "✅ ᴄʜᴀɴɴᴇʟ ᴀᴅᴅᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ.",
                reply_markup=menu()
            )

        except Exception as e:
            await message.answer(
                "Channel setup failed: " + str(e)[:150]
            )

        return

    # SET VOTE CHANNEL

    if state == "vote_channel":

        try:
            ch = await message.bot.get_chat(text)

            member = await message.bot.get_chat_member(
                ch.id, message.bot.id
            )

            if member.status not in (
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER
            ):
                await message.answer(
                    "Make the bot an administrator first."
                )
                return

            query("""
                INSERT INTO settings(id,vote_channel)
                VALUES(1,?)
                ON CONFLICT(id)
                DO UPDATE SET vote_channel=excluded.vote_channel
            """, (str(ch.id),))

            clear_state(uid)

            await message.answer(
                "✅ ᴠᴏᴛᴇ ᴄʜᴀɴɴᴇʟ ꜱᴇᴛ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ.",
                reply_markup=menu()
            )

        except Exception as e:
            await message.answer(
                "Invalid channel: " + str(e)[:150]
            )

        return

    # REMOVE CHANNEL

    if state == "remove_channel":

        query(
            "DELETE FROM channels WHERE chat_id=? OR title=?",
            (text, text)
        )

        clear_state(uid)

        await message.answer(
            "✅ ᴄʜᴀɴɴᴇʟ ʀᴇᴍᴏᴠᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ.",
            reply_markup=menu()
        )

        return

    # CONTEST TITLE

    if state == "contest_title":

        set_state(uid, "contest_time", text[:100])

        await message.answer(
            "⏳ <b>SELECT VOTING DURATION</b>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        button("5 HOURS", "duration:5"),
                        button("10 HOURS", "duration:10")
                    ],
                    [
                        button("24 HOURS", "duration:24"),
                        button("CUSTOM", "duration:custom", "success")
                    ]
                ]
            )
        )

        return

    # CUSTOM TIME

    if state == "custom_time":

        try:
            hours = float(text)

            if not 0 < hours <= 720:
                raise ValueError

            set_state(
                uid,
                "candidates",
                data + "|" + str(int(hours * 3600))
            )

            await message.answer(
                "ᴇɴᴛᴇʀ ᴄᴀɴᴅɪᴅᴀᴛᴇꜱ, ᴏɴᴇ ᴘᴇʀ ʟɪɴᴇ:\n\n"
                "<code>RIFAT</code>\n"
                "<code>ALFA</code>\n"
                "<code>IRFAN</code>"
            )

        except ValueError:
            await message.answer(
                "Enter a valid duration between 1 and 720 hours."
            )

        return

    # CANDIDATES

    if state == "candidates":

        try:

            title, seconds = data.rsplit("|", 1)

            names = [
                x.strip()[:50]
                for x in text.splitlines()
                if x.strip()
            ]

            if len(names) < 2:
                await message.answer(
                    "Add at least two candidates."
                )
                return

            setting = query(
                "SELECT vote_channel FROM settings WHERE id=1",
                fetch=True
            )

            if not setting:
                await message.answer(
                    "Set the vote channel first."
                )
                return

            channel = setting["vote_channel"]

            cid = query("""
                INSERT INTO contests(title,channel,ends_at)
                VALUES(?,?,?)
            """, (
                title,
                channel,
                int(time.time()) + int(seconds)
            ))

            # Retrieve contest ID
            contest = query(
                "SELECT MAX(id) AS id FROM contests",
                fetch=True
            )
            cid = contest["id"]

            for name in names:
                query(
                    "INSERT INTO candidates(contest_id,name,photo) VALUES(?,?,?)",
                    (cid, name, "")
                )

            clear_state(uid)

            await publish(message.bot, cid)

            asyncio.create_task(
                contest_timer(message.bot, cid)
            )

            await message.answer(
                "✅ ᴄᴏɴᴛᴇꜱᴛ ᴘᴜʙʟɪꜱʜᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ.\n\n"
                f"Contest ID: <code>{cid}</code>",
                reply_markup=menu()
            )

        except Exception:
            logging.exception("Contest creation error")
            await message.answer(
                "Contest creation failed. Check your channel settings."
            )

        return

    # MAIN MENU

    if text == "🆕 NEW CONTEST":

        set_state(uid, "contest_title")

        await message.answer(
            "✍️ ᴇɴᴛᴇʀ ᴄᴏɴᴛᴇꜱᴛ ᴛɪᴛʟᴇ:"
        )

    elif text == "➕ ADD CHANNEL":

        set_state(uid, "add_channel")

        await message.answer(
            "Send channel username or ID.\n\n"
            "Public: <code>@channelusername</code>\n"
            "Private: <code>-100123456789|https://t.me/+invite</code>"
        )

    elif text == "📢 VOTE CHANNEL":

        set_state(uid, "vote_channel")

        await message.answer(
            "Send your voting channel username or ID."
        )

    elif text == "🗑 REMOVE CHANNEL":

        channels = query(
            "SELECT * FROM channels",
            all_rows=True
        )

        if not channels:
            await message.answer("No channels available.")
            return

        listing = "\n".join(
            f"{c['title']} | {c['chat_id']}"
            for c in channels
        )

        set_state(uid, "remove_channel")

        await message.answer(
            "Send channel ID or title to remove:\n\n" + listing
        )

    elif text == "📊 MY CONTESTS":

        contests = query(
            "SELECT * FROM contests ORDER BY id DESC LIMIT 20",
            all_rows=True
        )

        if not contests:
            await message.answer("No contests found.")
            return

        result = ["📊 <b>MY CONTESTS</b>\n"]

        for c in contests:
            result.append(
                f"#{c['id']} • {c['title']} • {c['status']}"
            )

        await message.answer("\n".join(result))

    elif text == "📈 STATISTICS":

        total = query(
            "SELECT COUNT(*) AS n FROM contests",
            fetch=True
        )["n"]

        voters = query(
            "SELECT COUNT(DISTINCT user_id) AS n FROM votes",
            fetch=True
        )["n"]

        await message.answer(
            f"<b>📈 STATISTICS</b>\n\n"
            f"ᴛᴏᴛᴀʟ ᴄᴏɴᴛᴇꜱᴛꜱ: {total}\n"
            f"ᴛᴏᴛᴀʟ ᴠᴏᴛᴇʀꜱ: {voters}"
        )

    elif text == "❓ HELP":

        await message.answer(
            "<b>ᴠᴏᴛᴇ ᴍᴀᴋᴇʀ ʙᴏᴛ ʜᴇʟᴘ</b>\n\n"
            "1. Add required channels.\n"
            "2. Set the voting channel.\n"
            "3. Create a new contest.\n"
            "4. Select voting duration.\n"
            "5. Add candidates.\n"
            "6. Publish your contest.\n\n"
            "Admins can add more admins using /addadmin."
        )


# ==========================================
# DURATION CALLBACK
# ==========================================

@dp.callback_query(F.data.startswith("duration:"))
async def duration(call):

    uid = call.from_user.id

    if not is_admin(uid):
        await call.answer("Access denied.", show_alert=True)
        return

    state, title = get_state(uid)

    if state != "contest_time":
        await call.answer("Request expired.")
        return

    value = call.data.split(":")[1]

    if value == "custom":

        set_state(uid, "custom_time", title)

        await call.message.answer(
            "Enter duration in hours (maximum 720)."
        )

    else:

        seconds = int(value) * 3600

        set_state(
            uid,
            "candidates",
            title + "|" + str(seconds)
        )

        await call.message.answer(
            "ᴇɴᴛᴇʀ ᴄᴀɴᴅɪᴅᴀᴛᴇꜱ, ᴏɴᴇ ᴘᴇʀ ʟɪɴᴇ:\n\n"
            "<code>RIFAT</code>\n"
            "<code>ALFA</code>\n"
            "<code>IRFAN</code>"
        )

    await call.answer()


# ==========================================
# VOTING KEYBOARD
# ==========================================

def vote_keyboard(cid, candidates, counts, active=True):

    rows = []

    for c in candidates:

        count = counts.get(c["id"], 0)

        rows.append([
            button(
                f"{c['name']} • {count}",
                f"vote:{cid}:{c['id']}",
                "success"
            )
        ])

    if not active:
        rows.append([
            button("VOTING CLOSED", "closed", "danger")
        ])

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def get_contest(cid):

    contest = query(
        "SELECT * FROM contests WHERE id=?",
        (cid,), fetch=True
    )

    candidates = query(
        "SELECT * FROM candidates WHERE contest_id=?",
        (cid,), all_rows=True
    )

    votes = query("""
        SELECT candidate_id,COUNT(*) AS total
        FROM votes
        WHERE contest_id=?
        GROUP BY candidate_id
    """, (cid,), all_rows=True)

    counts = {
        v["candidate_id"]: v["total"]
        for v in votes
    }

    return contest, candidates, counts


async def publish(bot, cid):

    contest, candidates, counts = get_contest(cid)

    rows = vote_keyboard(
        cid, candidates, counts
    )

    end = time.strftime(
        "%d %b %Y %H:%M UTC",
        time.gmtime(contest["ends_at"])
    )

    text = (
        f"🏆 <b>{contest['title']}</b>\n\n"
        "ᴠᴏᴛᴇ ꜰᴏʀ ʏᴏᴜʀ ꜰᴀᴠᴏᴜʀɪᴛᴇ.\n\n"
        f"⏳ ᴇɴᴅꜱ: <code>{end}</code>\n\n"
        "👇 ꜱᴇʟᴇᴄᴛ ʏᴏᴜʀ ᴄᴀɴᴅɪᴅᴀᴛᴇ."
    )

    msg = await bot.send_message(
        contest["channel"],
        text,
        reply_markup=rows
    )

    query(
        "UPDATE contests SET message_id=? WHERE id=?",
        (msg.message_id, cid)
    )


# ==========================================
# VOTE HANDLER
# ==========================================

@dp.callback_query(F.data.startswith("vote:"))
async def vote(call):

    uid = call.from_user.id

    if not await check_join(call.bot, uid):
        await call.answer(
            "Join all required channels first.",
            show_alert=True
        )
        return

    try:
        _, cid, candidate_id = call.data.split(":")
        cid = int(cid)
        candidate_id = int(candidate_id)

    except ValueError:
        await call.answer("Invalid vote.")
        return

    contest, candidates, counts = get_contest(cid)

    if not contest or contest["status"] != "active":
        await call.answer(
            "Voting is closed.",
            show_alert=True
        )
        return

    if time.time() >= contest["ends_at"]:
        await finish_contest(call.bot, cid)
        await call.answer("Voting ended.")
        return

    candidate = next(
        (c for c in candidates if c["id"] == candidate_id),
        None
    )

    if not candidate:
        await call.answer("Invalid candidate.")
        return

    previous = query(
        "SELECT * FROM votes WHERE contest_id=? AND user_id=?",
        (cid, uid), fetch=True
    )

    query("""
        INSERT INTO votes(contest_id,user_id,candidate_id)
        VALUES(?,?,?)
        ON CONFLICT(contest_id,user_id)
        DO UPDATE SET candidate_id=excluded.candidate_id
    """, (cid, uid, candidate_id))

    await refresh_votes(call.bot, cid)

    if previous:
        await call.answer(
            "ᴠᴏᴛᴇ ᴄʜᴀɴɢᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ."
        )
    else:
        await call.answer(
            "ᴠᴏᴛᴇ ꜱᴜʙᴍɪᴛᴛᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ."
        )


@dp.callback_query(F.data == "closed")
async def closed(call):
    await call.answer(
        "Voting has ended.",
        show_alert=True
    )


async def refresh_votes(bot, cid):

    contest, candidates, counts = get_contest(cid)

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
    except Exception:
        pass


# ==========================================
# WINNER IMAGE
# ==========================================

def font(size):

    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/system/fonts/Roboto-Bold.ttf"
    ]

    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass

    return ImageFont.load_default()


def make_winner_image(name):

    width, height = 1536, 614

    image = Image.new(
        "RGB",
        (width, height),
        (76, 28, 130)
    )

    draw = ImageDraw.Draw(image)

    # Gradient background
    for y in range(height):
        ratio = y / height

        color = (
            int(50 + ratio * 60),
            int(15 + ratio * 15),
            int(110 + ratio * 100)
        )

        draw.line(
            (0, y, width, y),
            fill=color
        )

    # Decoration
    draw.ellipse(
        (1050, -100, 1450, 300),
        outline=(180, 130, 255),
        width=8
    )

    draw.text(
        (75, 130),
        "VOTE WINNER",
        font=font(105),
        fill="white"
    )

    draw.text(
        (90, 280),
        "CONGRATULATIONS!",
        font=font(45),
        fill=(255, 210, 90)
    )

    # Winner ring
    draw.ellipse(
        (1010, 65, 1450, 505),
        fill=(255, 195, 40)
    )

    draw.ellipse(
        (1030, 85, 1430, 485),
        fill=(100, 45, 160)
    )

    # Name ribbon
    draw.rounded_rectangle(
        (900, 490, 1530, 585),
        radius=30,
        fill=(255, 195, 40)
    )

    label = name[:25]

    draw.text(
        (950, 510),
        label,
        font=font(43),
        fill=(55, 25, 80)
    )

    output = io.BytesIO()

    image.save(
        output,
        format="JPEG",
        quality=92
    )

    return output.getvalue()


# ==========================================
# FINISH CONTEST
# ==========================================

async def finish_contest(bot, cid):

    contest, candidates, counts = get_contest(cid)

    if not contest or contest["status"] != "active":
        return

    if time.time() < contest["ends_at"]:
        return

    query(
        "UPDATE contests SET status='finished' WHERE id=?",
        (cid,)
    )

    await refresh_votes(bot, cid)

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

    image = make_winner_image(
        winner["name"]
    )

    caption = (
        "🏆 <b>ᴠᴏᴛᴇ ᴡɪɴɴᴇʀ</b>\n\n"
        f"👑 <b>{winner['name']}</b>\n"
        f"🗳 ᴠᴏᴛᴇꜱ: {counts.get(winner['id'], 0)}\n"
        f"📊 ᴛᴏᴛᴀʟ ᴠᴏᴛᴇꜱ: {total}\n\n"
        "🎉 ᴄᴏɴɢʀᴀᴛᴜʟᴀᴛɪᴏɴꜱ!"
    )

    await bot.send_photo(
        contest["channel"],
        BufferedInputFile(
            image,
            filename="winner.jpg"
        ),
        caption=caption
    )


# ==========================================
# AUTOMATIC TIMER
# ==========================================

async def contest_timer(bot, cid):

    while True:

        contest = query(
            "SELECT * FROM contests WHERE id=?",
            (cid,), fetch=True
        )

        if not contest or contest["status"] != "active":
            return

        remaining = contest["ends_at"] - time.time()

        if remaining <= 0:
            await finish_contest(bot, cid)
            return

        await asyncio.sleep(
            min(remaining, 30)
        )


# ==========================================
# STARTUP
# ==========================================

async def main():

    global main_bot

    if BOT_TOKEN == "YOUR_BOT_TOKEN":
        raise RuntimeError(
            "Configure BOT_TOKEN first."
        )

    if OWNER_ID <= 0:
        raise RuntimeError(
            "Configure OWNER_ID first."
        )

    init_db()

    main_bot = Bot(
        BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )

    # Restore unfinished contests
    contests = query(
        "SELECT id FROM contests WHERE status='active'",
        all_rows=True
    )

    for contest in contests:
        asyncio.create_task(
            contest_timer(main_bot, contest["id"])
        )

    logging.info("Vote Maker Bot started successfully.")

    await dp.start_polling(main_bot)


if __name__ == "__main__":
    asyncio.run(main())
