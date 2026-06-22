import asyncio
import base64
import json
import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from io import BytesIO

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
DB_PATH = os.getenv("DB_PATH", "sanoqchi.db")

DELETE_SERVICE_MESSAGES = os.getenv("DELETE_SERVICE_MESSAGES", "1") == "1"
CHECK_SUBSCRIPTION_IN_GROUPS = os.getenv("CHECK_SUBSCRIPTION_IN_GROUPS", "0") == "1"
DELETE_LINKS = os.getenv("DELETE_LINKS", "1") == "1"
DELETE_BAD_WORDS = os.getenv("DELETE_BAD_WORDS", "1") == "1"
DELETE_ADULT_MEDIA = os.getenv("DELETE_ADULT_MEDIA", "1") == "1"
BLOCK_ARABIC_BOTS = os.getenv("BLOCK_ARABIC_BOTS", "1") == "1"
BLOCK_NEW_BOTS = os.getenv("BLOCK_NEW_BOTS", "0") == "1"
AUTO_BAN_ENABLED = os.getenv("AUTO_BAN_ENABLED", "1") == "1"
ADMIN_ALERTS = os.getenv("ADMIN_ALERTS", "1") == "1"

WARN_LIMIT = int(os.getenv("WARN_LIMIT", "3") or "3")
MUTE_MINUTES = int(os.getenv("MUTE_MINUTES", "60") or "60")
MEN_DAILY_LIMIT = int(os.getenv("MEN_DAILY_LIMIT", "2") or "2")

NEW_MEMBER_LINK_LOCK_MINUTES = int(os.getenv("NEW_MEMBER_LINK_LOCK_MINUTES", "10") or "10")
SPAM_MUTE_MINUTES = int(os.getenv("SPAM_MUTE_MINUTES", "60") or "60")
SPAM_KEYWORDS = [
    w.strip().lower()
    for w in os.getenv(
        "SPAM_KEYWORDS",
        "telegram premium,premium,sovg'a,sovga,ovoz bering,bonus,pul ishlash,kirib oling,yutuq,bepul,aksiya,royxatdan oting"
    ).split(",")
    if w.strip()
]

BAD_WORDS = [w.strip().lower() for w in os.getenv("BAD_WORDS", "").split(",") if w.strip()]
ADULT_WORDS = [w.strip().lower() for w in os.getenv("ADULT_WORDS", "").split(",") if w.strip()]

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip()
AI_MEDIA_FILTER = os.getenv("AI_MEDIA_FILTER", "1") == "1"
GEMINI_MAX_DAILY_CHECKS = int(os.getenv("GEMINI_MAX_DAILY_CHECKS", "100") or "100")
MAX_AI_FILE_MB = int(os.getenv("MAX_AI_FILE_MB", "5") or "5")

ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
LINK_RE = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.me/|@\w{5,}|bit\.ly/|tinyurl\.com/|instagram\.com/|youtube\.com|youtu\.be|wa\.me|whatsapp\.com)",
    re.IGNORECASE,
)

ADMIN_IDS = set()
for x in ADMIN_IDS_RAW.split(","):
    x = x.strip()
    if x.isdigit():
        ADMIN_IDS.add(int(x))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi. Railway Variables ichiga BOT_TOKEN kiriting.")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(db()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS add_stats (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                full_name TEXT,
                username TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                warn_count INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS members_joined (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                joined_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_cache (
                file_unique_id TEXT PRIMARY KEY,
                unsafe INTEGER NOT NULL,
                checked_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_ai_usage (
                day TEXT PRIMARY KEY,
                count INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS command_usage (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                command TEXT NOT NULL,
                day TEXT NOT NULL,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, user_id, command, day)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                chat_id INTEGER PRIMARY KEY,
                delete_links INTEGER DEFAULT 1,
                delete_bad_words INTEGER DEFAULT 1,
                delete_service INTEGER DEFAULT 1,
                check_sub INTEGER DEFAULT 0,
                delete_adult_media INTEGER DEFAULT 1,
                block_arabic_bots INTEGER DEFAULT 1,
                auto_ban INTEGER DEFAULT 1,
                ai_media_filter INTEGER DEFAULT 1,
                admin_alerts INTEGER DEFAULT 1
            )
        """)
        for col, default in [
            ("delete_adult_media", 1),
            ("block_arabic_bots", 1),
            ("auto_ban", 1),
            ("ai_media_filter", 1),
            ("admin_alerts", 1),
        ]:
            try:
                conn.execute(f"ALTER TABLE settings ADD COLUMN {col} INTEGER DEFAULT {default}")
            except sqlite3.OperationalError:
                pass
        conn.commit()


def ensure_settings(chat_id: int):
    with closing(db()) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO settings(
                chat_id, delete_links, delete_bad_words, delete_service, check_sub,
                delete_adult_media, block_arabic_bots, auto_ban, ai_media_filter, admin_alerts
            )
            VALUES (?, 1, 1, 1, 0, 1, 1, 1, 1, 1)
        """, (chat_id,))
        conn.commit()


def get_settings(chat_id: int):
    ensure_settings(chat_id)
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM settings WHERE chat_id=?", (chat_id,)).fetchone()
        return dict(row)


def set_setting(chat_id: int, key: str, value: int):
    allowed = {
        "delete_links", "delete_bad_words", "delete_service", "check_sub",
        "delete_adult_media", "block_arabic_bots", "auto_ban", "ai_media_filter", "admin_alerts"
    }
    if key not in allowed:
        return
    ensure_settings(chat_id)
    with closing(db()) as conn:
        conn.execute(f"UPDATE settings SET {key}=? WHERE chat_id=?", (value, chat_id))
        conn.commit()


def add_count(chat_id: int, user):
    with closing(db()) as conn:
        conn.execute("""
            INSERT INTO add_stats(chat_id, user_id, full_name, username, count)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(chat_id, user_id)
            DO UPDATE SET count=count+1, full_name=excluded.full_name, username=excluded.username
        """, (chat_id, user.id, user.full_name or "", user.username or ""))
        conn.commit()


def get_user_count(chat_id: int, user_id: int):
    with closing(db()) as conn:
        row = conn.execute("SELECT count FROM add_stats WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
        return int(row["count"]) if row else 0


def top_users(chat_id: int, limit: int = 10):
    with closing(db()) as conn:
        return conn.execute("""
            SELECT user_id, full_name, username, count FROM add_stats
            WHERE chat_id=? ORDER BY count DESC LIMIT ?
        """, (chat_id, limit)).fetchall()


def reset_top(chat_id: int):
    with closing(db()) as conn:
        conn.execute("DELETE FROM add_stats WHERE chat_id=?", (chat_id,))
        conn.commit()


def add_warning(chat_id: int, user_id: int):
    with closing(db()) as conn:
        conn.execute("""
            INSERT INTO warnings(chat_id, user_id, warn_count)
            VALUES (?, ?, 1)
            ON CONFLICT(chat_id, user_id)
            DO UPDATE SET warn_count=warn_count+1
        """, (chat_id, user_id))
        conn.commit()
        row = conn.execute("SELECT warn_count FROM warnings WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
        return int(row["warn_count"]) if row else 1


def reset_warnings(chat_id: int, user_id: int):
    with closing(db()) as conn:
        conn.execute("DELETE FROM warnings WHERE chat_id=? AND user_id=?", (chat_id, user_id))
        conn.commit()


def set_member_joined(chat_id: int, user_id: int):
    with closing(db()) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO members_joined(chat_id, user_id, joined_at)
            VALUES (?, ?, ?)
        """, (chat_id, user_id, datetime.now(timezone.utc).isoformat()))
        conn.commit()


def is_new_member_locked(chat_id: int, user_id: int) -> bool:
    if NEW_MEMBER_LINK_LOCK_MINUTES <= 0:
        return False
    with closing(db()) as conn:
        row = conn.execute("SELECT joined_at FROM members_joined WHERE chat_id=? AND user_id=?", (chat_id, user_id)).fetchone()
        if not row:
            return False
        try:
            joined = datetime.fromisoformat(row["joined_at"])
            return datetime.now(timezone.utc) - joined < timedelta(minutes=NEW_MEMBER_LINK_LOCK_MINUTES)
        except Exception:
            return False


def get_ai_cache(file_unique_id: str):
    if not file_unique_id:
        return None
    with closing(db()) as conn:
        row = conn.execute("SELECT unsafe FROM ai_cache WHERE file_unique_id=?", (file_unique_id,)).fetchone()
        return bool(row["unsafe"]) if row else None


def set_ai_cache(file_unique_id: str, unsafe: bool):
    if not file_unique_id:
        return
    with closing(db()) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO ai_cache(file_unique_id, unsafe, checked_at)
            VALUES (?, ?, ?)
        """, (file_unique_id, int(unsafe), datetime.now(timezone.utc).isoformat()))
        conn.commit()


def can_use_ai_today() -> bool:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with closing(db()) as conn:
        conn.execute("INSERT OR IGNORE INTO daily_ai_usage(day, count) VALUES (?, 0)", (day,))
        row = conn.execute("SELECT count FROM daily_ai_usage WHERE day=?", (day,)).fetchone()
        return int(row["count"]) < GEMINI_MAX_DAILY_CHECKS


def increment_ai_usage():
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with closing(db()) as conn:
        conn.execute("INSERT OR IGNORE INTO daily_ai_usage(day, count) VALUES (?, 0)", (day,))
        conn.execute("UPDATE daily_ai_usage SET count=count+1 WHERE day=?", (day,))
        conn.commit()


def can_use_command_today(chat_id: int, user_id: int, command: str, limit: int) -> tuple[bool, int]:
    """
    Kunlik buyruq limitini tekshiradi va ishlatish sonini +1 qiladi.
    Return: (ruxsat, qolgan_soni)
    """
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with closing(db()) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO command_usage(chat_id, user_id, command, day, count)
            VALUES (?, ?, ?, ?, 0)
        """, (chat_id, user_id, command, day))
        row = conn.execute("""
            SELECT count FROM command_usage
            WHERE chat_id=? AND user_id=? AND command=? AND day=?
        """, (chat_id, user_id, command, day)).fetchone()
        used = int(row["count"]) if row else 0
        if used >= limit:
            return False, 0

        conn.execute("""
            UPDATE command_usage SET count=count+1
            WHERE chat_id=? AND user_id=? AND command=? AND day=?
        """, (chat_id, user_id, command, day))
        conn.commit()
        return True, max(0, limit - used - 1)


async def is_admin(chat_id: int, user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}
    except Exception:
        return False


async def is_subscribed(user_id: int) -> bool:
    if not CHANNEL_USERNAME:
        return True
    username = CHANNEL_USERNAME.strip()
    if username.startswith("https://t.me/"):
        username = "@" + username.split("https://t.me/", 1)[1].strip("/")
    if not username.startswith("@"):
        username = "@" + username
    try:
        member = await bot.get_chat_member(username, user_id)
        return member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER}
    except Exception:
        return False


def sub_keyboard():
    username = CHANNEL_USERNAME.strip()
    username = username.replace("https://t.me/", "").replace("@", "").strip("/")
    url = f"https://t.me/{username}" if username else "https://t.me/"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Kanalga obuna bo‘lish", url=url)],
        [InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")]
    ])


def word_in_text(text: str, words: list[str]) -> bool:
    if not text:
        return False
    low = text.lower()
    for word in words:
        if not word:
            continue
        pattern = r"(^|[\s.,!?;:()\[\]{}\"'`~<>/\\|+=_*#-])" + re.escape(word) + r"($|[\s.,!?;:()\[\]{}\"'`~<>/\\|+=_*#-])"
        if re.search(pattern, low, flags=re.IGNORECASE):
            return True
        if len(word) >= 4 and word in low:
            return True
    return False


def contains_bad_word(text: str) -> bool:
    return word_in_text(text, BAD_WORDS)


def contains_adult_word(text: str) -> bool:
    return word_in_text(text, ADULT_WORDS)


def contains_spam_keyword(text: str) -> bool:
    return word_in_text(text, SPAM_KEYWORDS)


def has_arabic_name(user) -> bool:
    return bool(ARABIC_RE.search(f"{user.full_name or ''} {user.username or ''}"))


async def temporary_notice(chat_id: int, text: str, seconds: int = 8):
    try:
        msg = await bot.send_message(chat_id, text)
        await asyncio.sleep(seconds)
        await msg.delete()
    except Exception:
        pass


async def admin_alert(chat_id: int, text: str):
    s = get_settings(chat_id)
    if not ADMIN_ALERTS or not s.get("admin_alerts", 1):
        return
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            pass


async def mute_user(chat_id: int, user_id: int, minutes: int):
    until_date = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    await bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=ChatPermissions(
            can_send_messages=False,
            can_send_audios=False,
            can_send_documents=False,
            can_send_photos=False,
            can_send_videos=False,
            can_send_video_notes=False,
            can_send_voice_notes=False,
            can_send_polls=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False,
            can_manage_topics=False,
        ),
        until_date=until_date
    )


async def punish_if_needed(message: Message, warn_count: int, minutes: int | None = None):
    s = get_settings(message.chat.id)
    if not AUTO_BAN_ENABLED or not s.get("auto_ban", 1):
        return
    if warn_count < WARN_LIMIT:
        return
    try:
        minutes = minutes or MUTE_MINUTES
        await mute_user(message.chat.id, message.from_user.id, minutes)
        await temporary_notice(message.chat.id, f"🔇 <b>{message.from_user.full_name}</b> {minutes} daqiqaga jim qilindi.", 10)
        reset_warnings(message.chat.id, message.from_user.id)
    except Exception:
        pass


def get_media_file_ids(message: Message):
    if message.photo:
        p = message.photo[-1]
        return p.file_id, p.file_unique_id, "image/jpeg"
    if message.video and message.video.thumbnail:
        t = message.video.thumbnail
        return t.file_id, t.file_unique_id, "image/jpeg"
    if message.animation and message.animation.thumbnail:
        t = message.animation.thumbnail
        return t.file_id, t.file_unique_id, "image/jpeg"
    if message.document:
        mime = message.document.mime_type or ""
        if mime.startswith("image/"):
            return message.document.file_id, message.document.file_unique_id, mime
        if message.document.thumbnail:
            t = message.document.thumbnail
            return t.file_id, t.file_unique_id, "image/jpeg"
    if message.sticker and message.sticker.thumbnail:
        t = message.sticker.thumbnail
        return t.file_id, t.file_unique_id, "image/jpeg"
    return None, None, "image/jpeg"


async def download_file_bytes(file_id: str):
    try:
        tg_file = await bot.get_file(file_id)
        size = getattr(tg_file, "file_size", None)
        if size and size > MAX_AI_FILE_MB * 1024 * 1024:
            return None
        bio = BytesIO()
        await bot.download_file(tg_file.file_path, destination=bio)
        return bio.getvalue()
    except Exception:
        return None


async def gemini_media_unsafe(image_bytes: bytes, mime_type: str) -> bool:
    if not GEMINI_API_KEY or not can_use_ai_today():
        return False

    data_b64 = base64.b64encode(image_bytes).decode("utf-8")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

    prompt = (
        "You are a strict Telegram group safety classifier. "
        "Classify the image only for adult sexual/nude pornographic content. "
        "Do not describe the image. Return only JSON like {\"unsafe\":true} or {\"unsafe\":false}. "
        "Use unsafe=true for clear adult sexual/nude/pornographic content. "
        "Use unsafe=false for normal people, clothing, family, ads, documents, memes, food, nature, or unclear images."
    )

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type or "image/jpeg", "data": data_b64}},
            ]
        }],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 20,
            "response_mime_type": "application/json",
        }
    }

    try:
        increment_ai_usage()
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(url, headers={"x-goog-api-key": GEMINI_API_KEY}, json=payload) as resp:
                raw = await resp.text()
                if resp.status >= 400:
                    logging.warning("Gemini error %s: %s", resp.status, raw[:300])
                    return False
                data = json.loads(raw)

        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        result = json.loads(text)
        return bool(result.get("unsafe", False))
    except Exception as e:
        logging.warning("Gemini check failed: %s", e)
        return False


async def is_media_unsafe_by_ai(message: Message) -> bool:
    file_id, unique_id, mime_type = get_media_file_ids(message)
    if not file_id:
        return False

    cached = get_ai_cache(unique_id or "")
    if cached is not None:
        return cached

    image_bytes = await download_file_bytes(file_id)
    if not image_bytes:
        return False

    unsafe = await gemini_media_unsafe(image_bytes, mime_type)
    set_ai_cache(unique_id or file_id, unsafe)
    return unsafe


@dp.message(CommandStart())
async def start(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        text = (
            "🛡 <b>Sanoqchi Himoyachi</b>\n\n"
            "Guruh himoyasi:\n"
            "✅ spam/linklarni o‘chiradi\n"
            "✅ yangi kirgan odam link tashlasa mute qiladi\n"
            "✅ virusga o‘xshash link matnlarini bloklaydi\n"
            "✅ yomon so‘zlarni o‘chiradi\n"
            "✅ Gemini orqali 18+ rasm/video thumbnail tekshiradi\n"
            "✅ kirdi-chiqdi xabarlarini tozalaydi\n"
            "✅ odam qo‘shganlarni sanaydi\n\n"
            "Botni guruhga admin qiling."
        )
        await message.answer(text, reply_markup=sub_keyboard())
    else:
        ensure_settings(message.chat.id)
        await message.answer("🛡 Sanoqchi Himoyachi ishga tushdi. /sozlama /top /men")


@dp.callback_query(F.data == "check_sub")
async def check_sub_cb(call: CallbackQuery):
    if await is_subscribed(call.from_user.id):
        await call.message.edit_text("✅ Obuna tasdiqlandi.")
    else:
        await call.answer("Hali obuna bo‘lmagansiz.", show_alert=True)


@dp.message(Command("men"))
async def my_count(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        return

    # /men hamma uchun, lekin guruh spam bo'lmasligi uchun kuniga 2 marta.
    # Adminlarga limit yo'q.
    if not await is_admin(message.chat.id, message.from_user.id):
        allowed, left = can_use_command_today(
            message.chat.id,
            message.from_user.id,
            "men",
            MEN_DAILY_LIMIT
        )
        if not allowed:
            try:
                await message.delete()
            except Exception:
                pass
            await temporary_notice(
                message.chat.id,
                f"⏳ /men buyrug‘i kuniga faqat {MEN_DAILY_LIMIT} marta. Ertaga yana urinib ko‘ring.",
                seconds=7
            )
            return

    count = get_user_count(message.chat.id, message.from_user.id)
    await message.reply(f"📊 <b>{message.from_user.full_name}</b>, siz <b>{count}</b> ta odam qo‘shgansiz.")


@dp.message(Command("top"))
async def top_cmd(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        return

    # /top faqat admin uchun. Hamma bosaversa guruhda ortiqcha xabar ko'payadi.
    if not await is_admin(message.chat.id, message.from_user.id):
        try:
            await message.delete()
        except Exception:
            pass
        await temporary_notice(
            message.chat.id,
            "⛔ /top buyrug‘i faqat adminlar uchun.",
            seconds=6
        )
        return

    rows = top_users(message.chat.id, 20)
    if not rows:
        await message.reply("📊 Hali hech kim odam qo‘shmagan.")
        return
    text = "🏆 <b>TOP 20 odam qo‘shganlar</b>\n\n"
    for i, row in enumerate(rows, 1):
        name = row["full_name"] or f'ID {row["user_id"]}'
        username = f' (@{row["username"]})' if row["username"] else ""
        text += f"{i}. <b>{name}</b>{username} — <b>{row['count']}</b> ta\n"
    await message.reply(text)


@dp.message(Command("reset_top"))
async def reset_top_cmd(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        await message.reply("⛔ Faqat admin uchun.")
        return
    reset_top(message.chat.id)
    await message.reply("✅ TOP tozalandi.")


@dp.message(Command("sozlama"))
async def settings_cmd(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        await message.reply("⛔ Faqat admin uchun.")
        return
    s = get_settings(message.chat.id)
    ai_status = "✅" if s.get("ai_media_filter", 1) and bool(GEMINI_API_KEY) else "❌"
    text = (
        "⚙️ <b>Guruh sozlamalari</b>\n\n"
        f"Link/reklama: {'✅' if s['delete_links'] else '❌'}\n"
        f"Yomon so‘z: {'✅' if s['delete_bad_words'] else '❌'}\n"
        f"18+ caption/media: {'✅' if s['delete_adult_media'] else '❌'}\n"
        f"Gemini 18+ media AI: {ai_status}\n"
        f"Kirdi-chiqdi tozalash: {'✅' if s['delete_service'] else '❌'}\n"
        f"Obuna tekshirish: {'✅' if s['check_sub'] else '❌'}\n"
        f"Arabcha/shubhali bot: {'✅' if s['block_arabic_bots'] else '❌'}\n"
        f"Avto mute: {'✅' if s['auto_ban'] else '❌'}\n"
        f"Admin alert: {'✅' if s.get('admin_alerts', 1) else '❌'}\n\n"
        "Buyruqlar:\n"
        "/link_on /link_off\n"
        "/bad_on /bad_off\n"
        "/media_on /media_off\n"
        "/ai18_on /ai18_off\n"
        "/service_on /service_off\n"
        "/sub_on /sub_off\n"
        "/arabbot_on /arabbot_off\n"
        "/autoban_on /autoban_off\n"
        "/alert_on /alert_off"
    )
    await message.reply(text)


async def toggle_setting(message: Message, key: str, value: int, label: str):
    if message.chat.type == ChatType.PRIVATE:
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        await message.reply("⛔ Faqat admin uchun.")
        return
    set_setting(message.chat.id, key, value)
    await message.reply(f"✅ {label}: {'yoqildi' if value else 'o‘chirildi'}")


@dp.message(Command("link_on"))
async def link_on(message: Message): await toggle_setting(message, "delete_links", 1, "Link/reklama")
@dp.message(Command("link_off"))
async def link_off(message: Message): await toggle_setting(message, "delete_links", 0, "Link/reklama")
@dp.message(Command("bad_on"))
async def bad_on(message: Message): await toggle_setting(message, "delete_bad_words", 1, "Yomon so‘z")
@dp.message(Command("bad_off"))
async def bad_off(message: Message): await toggle_setting(message, "delete_bad_words", 0, "Yomon so‘z")
@dp.message(Command("media_on"))
async def media_on(message: Message): await toggle_setting(message, "delete_adult_media", 1, "18+ media/caption")
@dp.message(Command("media_off"))
async def media_off(message: Message): await toggle_setting(message, "delete_adult_media", 0, "18+ media/caption")
@dp.message(Command("ai18_on"))
async def ai18_on(message: Message): await toggle_setting(message, "ai_media_filter", 1, "Gemini AI 18+ media")
@dp.message(Command("ai18_off"))
async def ai18_off(message: Message): await toggle_setting(message, "ai_media_filter", 0, "Gemini AI 18+ media")
@dp.message(Command("service_on"))
async def service_on(message: Message): await toggle_setting(message, "delete_service", 1, "Kirdi-chiqdi")
@dp.message(Command("service_off"))
async def service_off(message: Message): await toggle_setting(message, "delete_service", 0, "Kirdi-chiqdi")
@dp.message(Command("sub_on"))
async def sub_on(message: Message): await toggle_setting(message, "check_sub", 1, "Obuna tekshirish")
@dp.message(Command("sub_off"))
async def sub_off(message: Message): await toggle_setting(message, "check_sub", 0, "Obuna tekshirish")
@dp.message(Command("arabbot_on"))
async def arabbot_on(message: Message): await toggle_setting(message, "block_arabic_bots", 1, "Arabcha/shubhali bot")
@dp.message(Command("arabbot_off"))
async def arabbot_off(message: Message): await toggle_setting(message, "block_arabic_bots", 0, "Arabcha/shubhali bot")
@dp.message(Command("autoban_on"))
async def autoban_on(message: Message): await toggle_setting(message, "auto_ban", 1, "Avto mute")
@dp.message(Command("autoban_off"))
async def autoban_off(message: Message): await toggle_setting(message, "auto_ban", 0, "Avto mute")
@dp.message(Command("alert_on"))
async def alert_on(message: Message): await toggle_setting(message, "admin_alerts", 1, "Admin alert")
@dp.message(Command("alert_off"))
async def alert_off(message: Message): await toggle_setting(message, "admin_alerts", 0, "Admin alert")


@dp.message(Command("post"))
async def post_cmd(message: Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        await message.reply("⛔ Faqat admin uchun.")
        return
    if not CHANNEL_USERNAME:
        await message.reply("❌ CHANNEL_USERNAME kiritilmagan.")
        return
    text = message.text.replace("/post", "", 1).strip()
    if not text:
        await message.reply("Namuna: <code>/post Reklama matni</code>")
        return
    try:
        channel = CHANNEL_USERNAME
        if channel.startswith("https://t.me/"):
            channel = "@" + channel.split("https://t.me/", 1)[1].strip("/")
        if not channel.startswith("@"):
            channel = "@" + channel
        await bot.send_message(channel, text)
        await message.reply("✅ Post kanalga yuborildi.")
    except Exception as e:
        await message.reply(f"❌ Post yuborilmadi: <code>{e}</code>")


@dp.message(F.new_chat_members)
async def new_members(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        return

    ensure_settings(message.chat.id)
    s = get_settings(message.chat.id)
    inviter = message.from_user
    me = await bot.get_me()

    for member in message.new_chat_members:
        if member.id == me.id:
            await message.answer("🛡 Bot qo‘shildi. Admin qiling: Delete messages, Ban users, Manage chat.")
            continue

        set_member_joined(message.chat.id, member.id)

        if member.is_bot and s["block_arabic_bots"] and BLOCK_ARABIC_BOTS:
            should_ban = BLOCK_NEW_BOTS or has_arabic_name(member)
            if should_ban:
                try:
                    await bot.ban_chat_member(message.chat.id, member.id)
                    await temporary_notice(message.chat.id, "🤖 Shubhali bot guruhdan chiqarildi.", 7)
                    await admin_alert(message.chat.id, f"🤖 Shubhali bot chiqarildi:\nGuruh: {message.chat.title}\nBot: {member.full_name}")
                except Exception:
                    pass
                continue

        if inviter and inviter.id != member.id and not inviter.is_bot:
            add_count(message.chat.id, inviter)

    if s["delete_service"] and DELETE_SERVICE_MESSAGES:
        try:
            await message.delete()
        except Exception:
            pass


@dp.message(F.left_chat_member)
async def left_member(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        return
    s = get_settings(message.chat.id)
    if s["delete_service"] and DELETE_SERVICE_MESSAGES:
        try:
            await message.delete()
        except Exception:
            pass


async def delete_and_warn(message: Message, reason: str, minutes: int | None = None):
    try:
        await message.delete()
    except Exception:
        pass
    warns = add_warning(message.chat.id, message.from_user.id)
    await temporary_notice(message.chat.id, f"🚫 {reason}. Ogohlantirish: {warns}/{WARN_LIMIT}", 8)
    await admin_alert(
        message.chat.id,
        f"⚠️ Himoya ishladi\nGuruh: {message.chat.title}\nFoydalanuvchi: {message.from_user.full_name} ({message.from_user.id})\nSabab: {reason}"
    )
    await punish_if_needed(message, warns, minutes=minutes)


@dp.message()
async def moderation(message: Message):
    if message.chat.type == ChatType.PRIVATE or not message.from_user:
        return

    ensure_settings(message.chat.id)
    s = get_settings(message.chat.id)

    if await is_admin(message.chat.id, message.from_user.id):
        return

    text = message.text or message.caption or ""

    if CHECK_SUBSCRIPTION_IN_GROUPS and s["check_sub"]:
        if not await is_subscribed(message.from_user.id):
            try:
                await message.delete()
            except Exception:
                pass
            await temporary_notice(message.chat.id, f"⚠️ <b>{message.from_user.full_name}</b>, avval kanalga obuna bo‘ling.", 8)
            return

    has_link = bool(text and LINK_RE.search(text))
    spam_keyword = bool(text and contains_spam_keyword(text))
    new_locked = is_new_member_locked(message.chat.id, message.from_user.id)

    if s["delete_links"] and has_link and new_locked:
        await delete_and_warn(message, "Yangi a’zo link tashladi, virus/spam xavfi", minutes=SPAM_MUTE_MINUTES)
        return

    if s["delete_links"] and has_link and spam_keyword:
        await delete_and_warn(message, "Shubhali virus/spam link", minutes=SPAM_MUTE_MINUTES)
        return

    if DELETE_LINKS and s["delete_links"] and has_link:
        await delete_and_warn(message, "Guruhda link/reklama mumkin emas")
        return

    if DELETE_BAD_WORDS and s["delete_bad_words"] and text and contains_bad_word(text):
        await delete_and_warn(message, "Odob saqlang")
        return

    has_media = bool(message.photo or message.video or message.animation or message.document or message.sticker)

    if DELETE_ADULT_MEDIA and s["delete_adult_media"] and text and contains_adult_word(text):
        await delete_and_warn(message, "Bunday kontent mumkin emas")
        return

    if (
        DELETE_ADULT_MEDIA and AI_MEDIA_FILTER and s["delete_adult_media"] and s.get("ai_media_filter", 1)
        and has_media and GEMINI_API_KEY
    ):
        unsafe = await is_media_unsafe_by_ai(message)
        if unsafe:
            await delete_and_warn(message, "Gemini AI 18+ media aniqladi", minutes=SPAM_MUTE_MINUTES)
            return


async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    me = await bot.get_me()
    logging.info("Bot started: @%s", me.username)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
