
import asyncio
import sqlite3
import logging
import time
import json
import html

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


# =====================================================
# CONFIGURATION
# =====================================================

BOT_TOKEN = "9137560686:AAEJIabZSgvEnEkj4kj-j8k4a5gBfT8T9c4"
OWNER_ID = 9290242587

DB_PATH = "voter_bot.db"

MAX_CLONES_PER_USER = 5
MAX_CANDIDATES = 20
MAX_CONTESTS_PER_BOT = 100

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("VOTER_BOT")

running_bots = {}
bot_tasks = {}
main_bot_id = None


# =====================================================
# FONT SYSTEM - ONLY ONE FONT
# =====================================================

FONT_SOURCE = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
)

FONT_TARGET = (
    "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"
    "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"
)

FONT_MAP = str.maketrans(
    FONT_SOURCE,
    FONT_TARGET
)


def sc(text):
    return str(text).translate(FONT_MAP)


# =====================================================
# BUTTON SYSTEM
# =====================================================

def button(text, data, style="primary"):

    if style not in ("primary", "success", "danger"):
        style = "primary"

    return InlineKeyboardButton(
        text=sc(text),
        callback_data=data,
        style=style
    )


def keyboard(rows):

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


# =====================================================
# DATABASE
# =====================================================

def connect():

    con = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    con.row_factory = sqlite3.Row

    return con


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
        message_id INTEGER,
        PRIMARY KEY(bot_id,user_id,chat_id,message_id)
    );

    CREATE INDEX IF NOT EXISTS votes_contest
    ON votes(bot_id,contest_id);

    CREATE INDEX IF NOT EXISTS contests_bot
    ON contests(bot_id,status);

    """)

    con.commit()
    con.close()


# =====================================================
# USER AND ADMIN SYSTEM
# =====================================================

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


# =====================================================
# REPLY KEYBOARD
# =====================================================

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

    if bid == main_bot_id and uid == OWNER_ID:

        rows.append([
            KeyboardButton(text="👑 " + sc("Main Admin"))
        ])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True
    )


# =====================================================
# MESSAGE CLEANUP
# =====================================================

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


async def track_ui(bot, bid, uid, chat_id, message_id):

    query(
        "INSERT OR IGNORE INTO ui_messages VALUES(?,?,?,?)",
        (bid, uid, chat_id, message_id)
    )


async def send_ui(message, text, markup=None):

    bot = message.bot
    bid = bot.id
    uid = message.from_user.id
    chat_id = message.chat.id

    # Only previous bot UI messages are deleted.
    # User messages are not deleted.

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

    await track_ui(
        bot,
        bid,
        uid,
        chat_id,
        sent.message_id
    )

    return sent


async def edit_ui(call, text, markup=None):

    # IMPORTANT:
    # Do not delete the callback message.
    # Do not call cleanup here.
    # The existing message and keyboard remain visible.

    try:

        await call.message.edit_text(
            text,
            reply_markup=(
                markup
                if markup is not None
                else call.message.reply_markup
            )
        )

    except TelegramAPIError as e:

        log.warning("EDIT UI: %s", e)

        await call.answer(
            "Unable to update this message.",
            show_alert=True
        )

        return

    await call.answer()


# =====================================================
# FORCE JOIN SYSTEM
# =====================================================

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

            log.exception(
                "Force join verification failed: %s",
                channel["chat_id"]
            )

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
                url=ch["invite"],
                style="primary"
            )
        ])

    rows.append([
        button(
            "Verify Join",
            "force:verify",
            "success"
        )
    ])

    await send_ui(
        message,

        "🔒 <b>CHANNEL VERIFICATION</b>\n\n"
        "Join all required channels and press Verify Join.",

        keyboard(rows)
    )


# =====================================================
# CONTEST DATA
# =====================================================

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


# =====================================================
# PUBLISH CONTEST
# =====================================================

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

        "Vote for your favourite candidate.\n\n"

        f"⏳ Ends: <code>{end}</code>\n\n"

        "👇 Select your candidate."

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

    query("""
        UPDATE contests
        SET message_id=?
        WHERE id=? AND bot_id=?

    """, (sent.message_id, cid, bot.id))


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

        log.exception(
            "Contest keyboard update failed: %s",
            cid
        )


# =====================================================
# FINISH CONTEST
# =====================================================

async def finish_contest(bot, cid):

    contest, candidates, counts = contest_data(
        bot.id,
        cid
    )

    if not contest or contest["status"] != "active":
        return

    if time.time() < contest["ends"]:
        return

    query("""
        UPDATE contests
        SET status='finished'
        WHERE bot_id=? AND id=?

    """, (bot.id, cid))

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

        "🏆 <b>VOTING RESULT</b>\n\n"

        f"👑 Winner: <b>{html.escape(winner['name'])}</b>\n\n"

        f"🗳 Winner votes: <b>{counts.get(winner['id'], 0)}</b>\n"

        f"📊 Total votes: <b>{total}</b>\n\n"

        "🎉 Congratulations!"

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
                log.exception("Contest finish failed: %s", cid)

            return

        await asyncio.sleep(
            min(remaining, 30)
        )


# =====================================================
# STATISTICS
# =====================================================

def get_stats(bid):

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

    active = query("""
        SELECT COUNT(*) AS n
        FROM contests
        WHERE bot_id=? AND status='active'

    """, (bid,), "one")["n"]

    return users, contests, votes, active


# =====================================================
# ADMIN PANEL
# =====================================================

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


# =====================================================
# CLONE BOT CREATION
# =====================================================

async def create_clone(message):

    bid = message.bot.id
    uid = message.from_user.id

    if bid != main_bot_id:

        await send_ui(
            message,
            "Clone creation is available from the main bot only."
        )
        return

    if message.chat.type != "private":

        await send_ui(
            message,
            "Please use private chat."
        )
        return

    count = query("""
        SELECT COUNT(*) AS n
        FROM instances
        WHERE owner_id=?

    """, (uid,), "one")["n"]

    if count >= MAX_CLONES_PER_USER and uid != OWNER_ID:

        await send_ui(
            message,
            "❌ You reached your clone limit."
        )
        return

    set_state(
        bid,
        uid,
        "clone_token"
    )

    await send_ui(
        message,

        "🤖 <b>CREATE CLONE BOT</b>\n\n"
        "1. Open @BotFather.\n"
        "2. Create your bot.\n"
        "3. Copy its API token.\n"
        "4. Send the token here.\n\n"
        "⚠️ Only submit a token you own."
    )


# =====================================================
# ROUTER
# =====================================================

def create_router():

    router = Router()

    # -------------------------------------------------
    # START
    # -------------------------------------------------

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

            f"👋 Hello, <b>{name}</b>!\n\n"
            "WELCOME TO VOTE MAKER.\n\n"
            "CREATE CONTESTS, PUBLISH VOTING AND VIEW RESULTS.\n\n"
            "👇 SELECT AN OPTION.",

            reply_menu(bid, uid)
        )

    # -------------------------------------------------
    # FORCE JOIN VERIFY
    # -------------------------------------------------

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

        else:

            await call.answer(
                "Join all required channels first.",
                show_alert=True
            )

    # -------------------------------------------------
    # NOOP
    # -------------------------------------------------

    @router.callback_query(F.data == "noop")
    async def noop(call: CallbackQuery):

        await call.answer(
            "Voting closed.",
            show_alert=True
        )

    # -------------------------------------------------
    # VOTE
    # -------------------------------------------------

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

            await call.answer(
                "Invalid vote.",
                show_alert=True
            )
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
            c["id"] == candidate_id
            for c in candidates
        ):

            await call.answer(
                "Candidate not found.",
                show_alert=True
            )
            return

        previous = query("""
            SELECT candidate_id
            FROM votes
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
            if previous
            else "Vote submitted successfully."
        )

    # -------------------------------------------------
    # DURATION CALLBACK
    # -------------------------------------------------

    @router.callback_query(F.data.startswith("duration:"))
    async def duration(call: CallbackQuery):

        bid = call.bot.id
        uid = call.from_user.id

        state, data = get_state(bid, uid)

        if state != "contest_duration":

            await call.answer(
                "Start a new contest first.",
                show_alert=True
            )
            return

        value = call.data.split(":")[1]

        if value == "custom":

            set_state(
                bid,
                uid,
                "custom_duration",
                data
            )

            await edit_ui(
                call,
                "⏳ <b>ENTER CUSTOM DURATION</b>\n\n"
                "Enter hours between 1 and 720."
            )
            return

        try:

            hours = int(value)

            if hours not in (5, 10, 24):
                raise ValueError

        except ValueError:

            await call.answer(
                "Invalid duration.",
                show_alert=True
            )
            return

        set_state(
            bid,
            uid,
            "candidates",
            {
                "title": data["title"],
                "seconds": hours * 3600
            }
        )

        await edit_ui(
            call,
            "📝 <b>SEND CANDIDATE NAMES</b>\n\n"
            "Send one candidate per line."
        )

    # -------------------------------------------------
    # ADMIN CALLBACKS
    # -------------------------------------------------

    @router.callback_query(F.data.startswith("panel:"))
    async def panel(call: CallbackQuery):

        bid = call.bot.id
        uid = call.from_user.id

        if not is_admin(bid, uid):

            await call.answer(
                "Admin only.",
                show_alert=True
            )
            return

        action = call.data.split(":")[1]

        if action == "stats":

            users, contests, votes, active = get_stats(bid)

            text = (
                "📊 <b>BOT STATISTICS</b>\n\n"
                f"Users: <b>{users}</b>\n"
                f"Contests: <b>{contests}</b>\n"
                f"Active contests: <b>{active}</b>\n"
                f"Votes: <b>{votes}</b>"
            )

        elif action == "users":

            rows = query("""
                SELECT user_id,name
                FROM users
                WHERE bot_id=?
                ORDER BY joined DESC
                LIMIT 30

            """, (bid,), "all")

            text = "👥 <b>BOT USERS</b>\n\n"

            for r in rows:

                text += (
                    f"• {html.escape(r['name'])} "
                    f"<code>{r['user_id']}</code>\n"
                )

            if not rows:
                text += "No users found."

        elif action == "clones":

            if bid != main_bot_id or uid != OWNER_ID:

                await call.answer(
                    "Main owner only.",
                    show_alert=True
                )
                return

            rows = query(
                "SELECT bot_id,username,owner_id FROM instances",
                mode="all"
            )

            text = "🤖 <b>REGISTERED CLONES</b>\n\n"

            for r in rows:

                text += (
                    f"@{html.escape(r['username'] or 'unknown')} "
                    f"<code>{r['bot_id']}</code>\n"
                )

            if not rows:
                text += "No clones registered."

        elif action == "broadcast":

            set_state(
                bid,
                uid,
                "broadcast"
            )

            await edit_ui(
                call,
                "📢 <b>SEND YOUR BROADCAST MESSAGE</b>"
            )
            return

        else:

            text = "Unknown action."

        await edit_ui(
            call,
            text,
            admin_panel_markup()
        )

    # -------------------------------------------------
    # TEXT HANDLER
    # -------------------------------------------------

    @router.message(F.text)
    async def text_handler(message: Message):

        bot = message.bot
        bid = bot.id
        uid = message.from_user.id

        text = message.text.strip()

        register_user(
            bid,
            message.from_user
        )

        state, data = get_state(
            bid,
            uid
        )

        # ---------------------------------------------
        # CLONE TOKEN
        # ---------------------------------------------

        if state == "clone_token":

            if bid != main_bot_id:
                return

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
                    raise ValueError("Cannot register main bot as clone.")

                exists = query(
                    "SELECT 1 FROM instances WHERE bot_id=?",
                    (me.id,),
                    "one"
                )

                if exists:
                    raise ValueError("Clone already registered.")

                # Save clone registration.

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

                add_admin(
                    me.id,
                    uid
                )

                clear_state(
                    bid,
                    uid
                )

                # Start clone immediately.

                running_bots[me.id] = child

                task = asyncio.create_task(
                    start_bot(child)
                )

                bot_tasks[me.id] = task

                await send_ui(
                    message,

                    "✅ <b>CLONE BOT REGISTERED</b>\n\n"
                    f"🤖 @{html.escape(me.username or 'unknown')}\n"
                    "🟢 Startup initiated.\n\n"
                    "Your clone has its own voting system.",

                    reply_menu(bid, uid)
                )

            except Exception as e:

                log.exception("Clone creation failed")

                if child and child.id not in running_bots:

                    try:
                        await child.session.close()
                    except Exception:
                        pass

                clear_state(
                    bid,
                    uid
                )

                await send_ui(
                    message,

                    "❌ <b>CLONE CREATION FAILED</b>\n\n"
                    f"<code>{html.escape(str(e))[:300]}</code>",

                    reply_menu(bid, uid)
                )

            return

        # ---------------------------------------------
        # NORMAL USER FORCE JOIN
        # ---------------------------------------------

        if not is_admin(bid, uid):

            if not await check_force_join(bot, uid):

                await force_join_ui(message)
                return

        # ---------------------------------------------
        # BROADCAST
        # ---------------------------------------------

        if state == "broadcast" and is_admin(bid, uid):

            clear_state(
                bid,
                uid
            )

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

                f"📢 <b>BROADCAST FINISHED</b>\n\n"
                f"Sent: <b>{sent}</b>\n"
                f"Failed: <b>{failed}</b>",

                reply_menu(bid, uid)
            )

            return

        # ---------------------------------------------
        # ADD FORCE JOIN CHANNEL
        # ---------------------------------------------

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
                    ChatMemberStatus.CREATOR
                ):

                    await send_ui(
                        message,

                        "❌ <b>BOT IS NOT ADMIN</b>\n\n"
                        "Make the bot an administrator in this channel first."
                    )
                    return

                if chat.username:

                    invite = "https://t.me/" + chat.username

                elif len(parts) == 2 and parts[1].strip():

                    invite = parts[1].strip()

                else:

                    invite_obj = await bot.create_chat_invite_link(
                        chat_id=chat.id
                    )

                    invite = invite_obj.invite_link

                if not invite.startswith("https://t.me/"):

                    raise ValueError("Invalid invite URL.")

                query("""
                    INSERT INTO channels(
                        bot_id,chat_id,title,invite
                    )
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

                clear_state(
                    bid,
                    uid
                )

                await send_ui(
                    message,

                    "✅ <b>CHANNEL ADDED SUCCESSFULLY</b>\n\n"
                    f"📢 Channel: <b>{html.escape(chat.title or 'Channel')}</b>\n"
                    f"🆔 ID: <code>{chat.id}</code>",

                    reply_menu(bid, uid)
                )

            except Exception as e:

                log.exception("Channel add error")

                await send_ui(
                    message,

                    "❌ <b>CHANNEL COULD NOT BE ADDED</b>\n\n"
                    f"Error: <code>{html.escape(str(e))[:300]}</code>\n\n"
                    "Check channel ID and administrator permissions."
                )

            return

        # ---------------------------------------------
        # VOTING CHANNEL
        # ---------------------------------------------

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
                    ChatMemberStatus.CREATOR
                ):

                    raise ValueError(
                        "Bot must be a channel administrator."
                    )

                query("""
                    INSERT INTO settings VALUES(?,?)

                    ON CONFLICT(bot_id)
                    DO UPDATE SET vote_channel=excluded.vote_channel

                """, (
                    bid,
                    str(chat.id)
                ))

                clear_state(
                    bid,
                    uid
                )

                await send_ui(
                    message,

                    "✅ <b>VOTING CHANNEL SET SUCCESSFULLY</b>\n\n"
                    f"📢 {html.escape(chat.title or 'Channel')}",

                    reply_menu(bid, uid)
                )

            except Exception as e:

                log.exception("Voting channel error")

                await send_ui(
                    message,

                    "❌ <b>COULD NOT SET VOTING CHANNEL</b>\n\n"
                    f"<code>{html.escape(str(e))[:300]}</code>"
                )

            return

        # ---------------------------------------------
        # REMOVE FORCE JOIN CHANNEL
        # ---------------------------------------------

        if state == "remove_channel":

            if not is_admin(bid, uid):
                clear_state(bid, uid)
                return

            cur = query("""
                DELETE FROM channels
                WHERE bot_id=?
                AND (chat_id=? OR title=?)

            """, (
                bid,
                text,
                text
            ))

            clear_state(
                bid,
                uid
            )

            await send_ui(
                message,

                "✅ <b>CHANNEL REMOVAL REQUEST PROCESSED</b>",

                reply_menu(bid, uid)
            )

            return

        # ---------------------------------------------
        # CONTEST TITLE
        # ---------------------------------------------

        if state == "contest_title":

            title = text[:100]

            if not title:

                await send_ui(
                    message,
                    "Enter a valid contest title."
                )
                return

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

        # ---------------------------------------------
        # CUSTOM DURATION
        # ---------------------------------------------

        if state == "custom_duration":

            try:

                hours = float(text)

                if not 1 <= hours <= 720:
                    raise ValueError

                set_state(
                    bid,
                    uid,
                    "candidates",
                    {
                        "title": data["title"],
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
                    "Enter a number between 1 and 720 hours."
                )

            return

        # ---------------------------------------------
        # CANDIDATES
        # ---------------------------------------------

        if state == "candidates":

            names = list(dict.fromkeys(
                line.strip()[:50]
                for line in text.splitlines()
                if line.strip()
            ))

            if len(names) < 2:

                await send_ui(
                    message,
                    "At least 2 candidates are required."
                )
                return

            if len(names) > MAX_CANDIDATES:

                await send_ui(
                    message,
                    f"Maximum {MAX_CANDIDATES} candidates allowed."
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

                    "❌ <b>VOTING CHANNEL NOT SET</b>\n\n"
                    "Ask the Bot Admin to configure a voting channel."
                )
                return

            count = query("""
                SELECT COUNT(*) AS n
                FROM contests
                WHERE bot_id=?

            """, (bid,), "one")["n"]

            if count >= MAX_CONTESTS_PER_BOT:

                await send_ui(
                    message,
                    "Contest limit reached."
                )
                return

            cid = None

            try:

                title = data["title"]
                seconds = data["seconds"]

                cid = query("""
                    INSERT INTO contests(
                        bot_id,
                        creator,
                        title,
                        channel,
                        created,
                        ends,
                        status
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

                await publish_contest(
                    bot,
                    cid
                )

                clear_state(
                    bid,
                    uid
                )

                asyncio.create_task(
                    contest_timer(bot, cid)
                )

                await send_ui(
                    message,

                    "✅ <b>CONTEST PUBLISHED SUCCESSFULLY</b>\n\n"
                    f"Contest ID: <code>{cid}</code>\n"
                    f"Candidates: <b>{len(names)}</b>",

                    reply_menu(bid, uid)
                )

            except Exception:

                log.exception("Contest publishing error")

                if cid:

                    query(
                        "DELETE FROM votes WHERE bot_id=? AND contest_id=?",
                        (bid, cid)
                    )

                    query(
                        "DELETE FROM candidates WHERE contest_id=?",
                        (cid,)
                    )

                    query(
                        "DELETE FROM contests WHERE bot_id=? AND id=?",
                        (bid, cid)
                    )

                await send_ui(
                    message,

                    "❌ <b>CONTEST PUBLISH FAILED</b>\n\n"
                    "Check channel permissions and try again."
                )

            return

        # =================================================
        # MAIN MENU
        # =================================================

        if text == "🤖 " + sc("Clone Bot"):

            await create_clone(message)
            return

        if text == "🆕 " + sc("New Contest"):

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

        if text == "📊 " + sc("My Contests"):

            rows = query("""
                SELECT id,title,status
                FROM contests
                WHERE bot_id=? AND creator=?
                ORDER BY id DESC
                LIMIT 30

            """, (
                bid,
                uid
            ), "all")

            if not rows:

                await send_ui(
                    message,
                    "You have not created any contests.",
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

        if text == "📈 " + sc("Statistics"):

            users, contests, votes, active = get_stats(bid)

            await send_ui(
                message,

                "📈 <b>STATISTICS</b>\n\n"
                f"Users: <b>{users}</b>\n"
                f"Contests: <b>{contests}</b>\n"
                f"Active contests: <b>{active}</b>\n"
                f"Votes: <b>{votes}</b>",

                reply_menu(bid, uid)
            )

            return

        if text == "⚙️ " + sc("Admin Panel"):

            if not is_admin(bid, uid):
                return

            await send_ui(
                message,
                "⚙️ <b>ADMIN PANEL</b>",
                admin_panel_markup()
            )

            return

        if text == "👑 " + sc("Main Admin"):

            if bid != main_bot_id or uid != OWNER_ID:
                return

            await send_ui(
                message,
                "👑 <b>MAIN ADMIN PANEL</b>",
                admin_panel_markup()
            )

            return

        if text == "➕ " + sc("Add Channel"):

            if not is_admin(bid, uid):
                return

            set_state(
                bid,
                uid,
                "add_channel"
            )

            await send_ui(
                message,

                "➕ <b>ADD FORCE JOIN CHANNEL</b>\n\n"
                "Public channel:\n"
                "<code>@channelname</code>\n\n"
                "Private channel:\n"
                "<code>-100123456789</code>\n\n"
                "Optional private invite:\n"
                "<code>-100123456789|https://t.me/+invite</code>\n\n"
                "The bot must be an administrator."
            )

            return

        if text == "🗑 " + sc("Remove Channel"):

            if not is_admin(bid, uid):
                return

            channels = query(
                "SELECT * FROM channels WHERE bot_id=?",
                (bid,),
                "all"
            )

            if not channels:

                await send_ui(
                    message,
                    "No force join channels configured.",
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

        if text == "📢 " + sc("Vote Channel"):

            if not is_admin(bid, uid):
                return

            set_state(
                bid,
                uid,
                "vote_channel"
            )

            await send_ui(
                message,

                "📢 <b>SET VOTING CHANNEL</b>\n\n"
                "Send channel username or ID.\n"
                "The bot must be an administrator."
            )

            return

        if text == "❓ " + sc("Help"):

            await send_ui(
                message,

                "❓ <b>HELP</b>\n\n"
                "1. Create Contest\n"
                "2. Select Duration\n"
                "3. Add Candidates\n"
                "4. Publish Contest\n"
                "5. Users can vote.\n\n"
                "Normal users can create contests.\n"
                "Only Admins can configure channels.",

                reply_menu(bid, uid)
            )

            return

        await send_ui(
            message,
            "Select an option from the menu.",
            reply_menu(bid, uid)
        )

    return router


# =====================================================
# START INDIVIDUAL BOT
# =====================================================

async def start_bot(bot):

    dp = Dispatcher()

    dp.include_router(
        create_router()
    )

    log.info(
        "Starting bot ID: %s",
        bot.id
    )

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
        handle_signals=False
    )


# =====================================================
# RESTORE REGISTERED CLONES
# =====================================================

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


# =====================================================
# RESTORE ACTIVE CONTEST TIMERS
# =====================================================

async def restore_timers():

    for bid, bot in list(running_bots.items()):

        rows = query("""
            SELECT id
            FROM contests
            WHERE bot_id=? AND status='active'

        """, (bid,), "all")

        for row in rows:

            asyncio.create_task(
                contest_timer(
                    bot,
                    row["id"]
                )
            )


# =====================================================
# MAIN
# =====================================================

async def main():

    global main_bot_id

    if BOT_TOKEN == "PASTE_MAIN_BOT_TOKEN":
        raise RuntimeError("Please set BOT_TOKEN.")

    if OWNER_ID <= 0:
        raise RuntimeError("Please set OWNER_ID.")

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
        "Main bot started: @%s",
        me.username
    )

    # Restore clones.

    await restore_clones()

    # Restore contest timers.

    await restore_timers()

    main_task = asyncio.create_task(
        start_bot(main_bot)
    )

    try:

        while True:

            await asyncio.sleep(10)

            for bid, task in list(bot_tasks.items()):

                if task.done():

                    error = task.exception() if not task.cancelled() else None

                    log.error(
                        "Clone stopped: %s | %s",
                        bid,
                        error
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


# =====================================================
# RUN
# =====================================================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        print("Bot stopped.")

    except Exception:

        log.exception("Fatal error")
