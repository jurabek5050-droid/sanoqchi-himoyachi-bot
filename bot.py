
import asyncio
import base64
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
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
DB_PATH = os.getenv("DB_PATH", "sanoqchi.db")

DELETE_SERVICE_MESSAGES = os.getenv("DELETE_SERVICE_MESSAGES", "1") == "1"
CHECK_SUBSCRIPTION_IN_GROUPS = os.getenv("CHECK_SUBSCRIPTION_IN_GROUPS", "1") == "1"
DELETE_LINKS = os.getenv("DELETE_LINKS", "1") == "1"
DELETE_BAD_WORDS = os.getenv("DELETE_BAD_WORDS", "1") == "1"
DELETE_ADULT_MEDIA = os.getenv("DELETE_ADULT_MEDIA", "1") == "1"
BLOCK_ARABIC_BOTS = os.getenv("BLOCK_ARABIC_BOTS", "1") == "1"
BLOCK_NEW_BOTS = os.getenv("BLOCK_NEW_BOTS", "0") == "1"
AUTO_BAN_ENABLED = os.getenv("AUTO_BAN_ENABLED", "1") == "1"
WARN_LIMIT = int(os.getenv("WARN_LIMIT", "3") or "3")
MUTE_MINUTES = int(os.getenv("MUTE_MINUTES", "60") or "60")

# Google Cloud Vision SafeSearch API key kerak. Bo'sh bo'lsa AI media filter ishlamaydi.
GOOGLE_VISION_API_KEY = os.getenv("GOOGLE_VISION_API_KEY", "").strip()
SAFESEARCH_BLOCK_LEVEL = int(os.getenv("SAFESEARCH_BLOCK_LEVEL", "3") or "3")
MAX_AI_FILE_MB = int(os.getenv("MAX_AI_FILE_MB", "8") or "8")

BAD_WORDS = [w.strip().lower() for w in os.getenv("BAD_WORDS", "").split(",") if w.strip()]
ADULT_WORDS = [w.strip().lower() for w in os.getenv("ADULT_WORDS", "").split(",") if w.strip()]

ARABIC_RE = re.compile(r"[\u0600-\u06FF]")
LINK_RE = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/|@\w{5,}|bit\.ly/|tinyurl\.com/|instagram\.com/|youtube\.com/|youtu\.be/)", re.IGNORECASE)

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
            CREATE TABLE IF NOT EXISTS settings (
                chat_id INTEGER PRIMARY KEY,
                delete_links INTEGER DEFAULT 1,
                delete_bad_words INTEGER DEFAULT 1,
                delete_service INTEGER DEFAULT 1,
                check_sub INTEGER DEFAULT 1,
                delete_adult_media INTEGER DEFAULT 1,
                block_arabic_bots INTEGER DEFAULT 1,
                auto_ban INTEGER DEFAULT 1,
                ai_media_filter INTEGER DEFAULT 1
            )
        """)
        for col, default in [("delete_adult_media",1),("block_arabic_bots",1),("auto_ban",1),("ai_media_filter",1)]:
            try: conn.execute(f"ALTER TABLE settings ADD COLUMN {col} INTEGER DEFAULT {default}")
            except sqlite3.OperationalError: pass
        conn.commit()


def ensure_settings(chat_id: int):
    with closing(db()) as conn:
        conn.execute("""
            INSERT OR IGNORE INTO settings(chat_id, delete_links, delete_bad_words, delete_service, check_sub, delete_adult_media, block_arabic_bots, auto_ban, ai_media_filter)
            VALUES (?, 1, 1, 1, 1, 1, 1, 1, 1)
        """, (chat_id,))
        conn.commit()


def get_settings(chat_id: int):
    ensure_settings(chat_id)
    with closing(db()) as conn:
        return dict(conn.execute("SELECT * FROM settings WHERE chat_id=?", (chat_id,)).fetchone())


def set_setting(chat_id: int, key: str, value: int):
    allowed = {"delete_links","delete_bad_words","delete_service","check_sub","delete_adult_media","block_arabic_bots","auto_ban","ai_media_filter"}
    if key not in allowed: return
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
        return conn.execute("SELECT user_id, full_name, username, count FROM add_stats WHERE chat_id=? ORDER BY count DESC LIMIT ?", (chat_id, limit)).fetchall()


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


async def is_admin(chat_id: int, user_id: int) -> bool:
    if user_id in ADMIN_IDS: return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}
    except Exception:
        return False


async def is_subscribed(user_id: int) -> bool:
    if not CHANNEL_USERNAME: return True
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER}
    except Exception:
        return False


def sub_keyboard():
    url = f"https://t.me/{CHANNEL_USERNAME.replace('@','')}" if CHANNEL_USERNAME else "https://t.me/"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Kanalga obuna bo‘lish", url=url)],
        [InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")]
    ])


def word_in_text(text: str, words: list[str]) -> bool:
    if not text: return False
    low = text.lower()
    for word in words:
        pattern = r"(^|[\s.,!?;:()\[\]{}\"'`~<>/\\|+=_*#-])" + re.escape(word) + r"($|[\s.,!?;:()\[\]{}\"'`~<>/\\|+=_*#-])"
        if re.search(pattern, low, flags=re.IGNORECASE): return True
        if ARABIC_RE.search(word) and word in low: return True
    return False


def contains_bad_word(text: str) -> bool: return word_in_text(text, BAD_WORDS)
def contains_adult_word(text: str) -> bool: return word_in_text(text, ADULT_WORDS)
def has_arabic_name(user) -> bool: return bool(ARABIC_RE.search(f"{user.full_name or ''} {user.username or ''}"))


async def temporary_notice(chat_id: int, text: str, seconds: int = 8):
    try:
        msg = await bot.send_message(chat_id, text)
        await asyncio.sleep(seconds)
        await msg.delete()
    except Exception:
        pass


async def punish_if_needed(message: Message, warn_count: int):
    s = get_settings(message.chat.id)
    if not AUTO_BAN_ENABLED or not s.get("auto_ban", 1) or warn_count < WARN_LIMIT: return
    try:
        until_date = datetime.now(timezone.utc) + timedelta(minutes=MUTE_MINUTES)
        await bot.restrict_chat_member(
            chat_id=message.chat.id, user_id=message.from_user.id,
            permissions={
                "can_send_messages": False, "can_send_audios": False, "can_send_documents": False,
                "can_send_photos": False, "can_send_videos": False, "can_send_video_notes": False,
                "can_send_voice_notes": False, "can_send_polls": False, "can_send_other_messages": False,
                "can_add_web_page_previews": False, "can_change_info": False, "can_invite_users": False,
                "can_pin_messages": False, "can_manage_topics": False,
            }, until_date=until_date
        )
        await temporary_notice(message.chat.id, f"🔇 <b>{message.from_user.full_name}</b> {MUTE_MINUTES} daqiqaga jim qilindi.", 10)
        reset_warnings(message.chat.id, message.from_user.id)
    except Exception:
        pass


# =========================
# AI 18+ MEDIA FILTER
# =========================
LIKELIHOOD_SCORE = {"UNKNOWN":0,"VERY_UNLIKELY":1,"UNLIKELY":2,"POSSIBLE":3,"LIKELY":4,"VERY_LIKELY":5}


async def google_safesearch_check(image_bytes: bytes) -> tuple[bool, dict]:
    if not GOOGLE_VISION_API_KEY:
        return False, {"error": "GOOGLE_VISION_API_KEY yo'q"}
    content = base64.b64encode(image_bytes).decode("utf-8")
    url = f"https://vision.googleapis.com/v1/images:annotate?key={GOOGLE_VISION_API_KEY}"
    payload = {"requests":[{"image":{"content":content},"features":[{"type":"SAFE_SEARCH_DETECTION"}]}]}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
    except Exception as e:
        return False, {"error": str(e)}
    try:
        safe = data["responses"][0]["safeSearchAnnotation"]
    except Exception:
        return False, {"error": "SafeSearch javobi kelmadi", "raw": data}
    adult = LIKELIHOOD_SCORE.get(safe.get("adult", "UNKNOWN"), 0)
    racy = LIKELIHOOD_SCORE.get(safe.get("racy", "UNKNOWN"), 0)
    unsafe = adult >= SAFESEARCH_BLOCK_LEVEL or racy >= SAFESEARCH_BLOCK_LEVEL
    return unsafe, safe


async def download_file_bytes(file_id: str, max_mb: int = MAX_AI_FILE_MB) -> bytes | None:
    try:
        tg_file = await bot.get_file(file_id)
        if tg_file.file_size and tg_file.file_size > max_mb * 1024 * 1024:
            return None
        bio = BytesIO()
        await bot.download_file(tg_file.file_path, destination=bio)
        return bio.getvalue()
    except Exception:
        return None


def get_ai_check_file_id(message: Message) -> str | None:
    if message.photo: return message.photo[-1].file_id
    if message.video and message.video.thumbnail: return message.video.thumbnail.file_id
    if message.animation and message.animation.thumbnail: return message.animation.thumbnail.file_id
    if message.document:
        mime = message.document.mime_type or ""
        if mime.startswith("image/"): return message.document.file_id
        if message.document.thumbnail: return message.document.thumbnail.file_id
    if message.sticker and message.sticker.thumbnail: return message.sticker.thumbnail.file_id
    return None


async def is_media_unsafe_by_ai(message: Message) -> tuple[bool, dict]:
    file_id = get_ai_check_file_id(message)
    if not file_id: return False, {"error": "Tekshiradigan rasm/thumbnail topilmadi"}
    image_bytes = await download_file_bytes(file_id)
    if not image_bytes: return False, {"error": "Fayl yuklab olinmadi yoki hajmi katta"}
    return await google_safesearch_check(image_bytes)


@dp.message(CommandStart())
async def start(message: Message):
    if message.chat.type == ChatType.PRIVATE:
        text = (
            "🛡 <b>Sanoqchi Himoyachi</b>\n\n"
            "Men guruhni himoya qilaman:\n"
            "• link/reklamani o‘chiraman\n"
            "• yomon so‘zlarni o‘chiraman\n"
            "• AI orqali 18+ rasm va video thumbnail tekshiraman\n"
            "• kirdi-chiqdi xabarlarini tozalayman\n"
            "• kim odam qo‘shganini sanayman\n\n"
            "Botni guruhga admin qilib qo‘ying."
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
    if message.chat.type == ChatType.PRIVATE: return
    count = get_user_count(message.chat.id, message.from_user.id)
    await message.reply(f"📊 <b>{message.from_user.full_name}</b>, siz <b>{count}</b> ta odam qo‘shgansiz.")


@dp.message(Command("top"))
async def top_cmd(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    rows = top_users(message.chat.id, 10)
    if not rows:
        await message.reply("📊 Hali hech kim odam qo‘shmagan.")
        return
    text = "🏆 <b>TOP odam qo‘shganlar</b>\n\n"
    for i, row in enumerate(rows, 1):
        name = row["full_name"] or f'ID {row["user_id"]}'
        username = f' (@{row["username"]})' if row["username"] else ""
        text += f"{i}. <b>{name}</b>{username} — <b>{row['count']}</b> ta\n"
    await message.reply(text)


@dp.message(Command("reset_top"))
async def reset_top_cmd(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    if not await is_admin(message.chat.id, message.from_user.id):
        await message.reply("⛔ Faqat admin uchun."); return
    reset_top(message.chat.id)
    await message.reply("✅ TOP tozalandi.")


@dp.message(Command("sozlama"))
async def settings_cmd(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    if not await is_admin(message.chat.id, message.from_user.id):
        await message.reply("⛔ Faqat admin uchun."); return
    s = get_settings(message.chat.id)
    text = (
        "⚙️ <b>Guruh sozlamalari</b>\n\n"
        f"Link/reklama: {'✅' if s['delete_links'] else '❌'}\n"
        f"Yomon so‘z: {'✅' if s['delete_bad_words'] else '❌'}\n"
        f"18+ caption/media: {'✅' if s['delete_adult_media'] else '❌'}\n"
        f"AI media filter: {'✅' if s.get('ai_media_filter', 1) else '❌'}\n"
        f"Kirdi-chiqdi tozalash: {'✅' if s['delete_service'] else '❌'}\n"
        f"Obuna tekshirish: {'✅' if s['check_sub'] else '❌'}\n"
        f"Arabcha/shubhali bot: {'✅' if s['block_arabic_bots'] else '❌'}\n"
        f"Avto mute: {'✅' if s['auto_ban'] else '❌'}\n\n"
        "Buyruqlar:\n"
        "/link_on /link_off\n/bad_on /bad_off\n/media_on /media_off\n/ai18_on /ai18_off\n"
        "/service_on /service_off\n/sub_on /sub_off\n/arabbot_on /arabbot_off\n/autoban_on /autoban_off"
    )
    await message.reply(text)


async def toggle_setting(message: Message, key: str, value: int, label: str):
    if message.chat.type == ChatType.PRIVATE: return
    if not await is_admin(message.chat.id, message.from_user.id):
        await message.reply("⛔ Faqat admin uchun."); return
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
async def media_on(message: Message): await toggle_setting(message, "delete_adult_media", 1, "18+ caption/media")
@dp.message(Command("media_off"))
async def media_off(message: Message): await toggle_setting(message, "delete_adult_media", 0, "18+ caption/media")
@dp.message(Command("ai18_on"))
async def ai18_on(message: Message): await toggle_setting(message, "ai_media_filter", 1, "AI 18+ media filter")
@dp.message(Command("ai18_off"))
async def ai18_off(message: Message): await toggle_setting(message, "ai_media_filter", 0, "AI 18+ media filter")
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


@dp.message(Command("post"))
async def post_cmd(message: Message):
    if not await is_admin(message.chat.id, message.from_user.id):
        await message.reply("⛔ Faqat admin uchun."); return
    if not CHANNEL_USERNAME:
        await message.reply("❌ CHANNEL_USERNAME kiritilmagan."); return
    text = message.text.replace("/post", "", 1).strip()
    if not text:
        await message.reply("Namuna: <code>/post Reklama matni</code>"); return
    try:
        await bot.send_message(CHANNEL_USERNAME, text)
        await message.reply("✅ Post kanalga yuborildi.")
    except Exception as e:
        await message.reply(f"❌ Post yuborilmadi: <code>{e}</code>")


@dp.message(F.new_chat_members)
async def new_members(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    ensure_settings(message.chat.id)
    s = get_settings(message.chat.id)
    inviter = message.from_user
    me = await bot.me()
    for member in message.new_chat_members:
        if member.id == me.id:
            await message.answer("🛡 Bot qo‘shildi. To‘liq ishlashi uchun admin qiling: Delete messages, Ban users, Manage chat.")
            continue
        if member.is_bot and s["block_arabic_bots"] and BLOCK_ARABIC_BOTS:
            if BLOCK_NEW_BOTS or has_arabic_name(member):
                try:
                    await bot.ban_chat_member(message.chat.id, member.id)
                    await temporary_notice(message.chat.id, "🤖 Shubhali bot guruhdan chiqarildi.", 7)
                except Exception: pass
                continue
        if inviter and inviter.id != member.id and not inviter.is_bot:
            add_count(message.chat.id, inviter)
    if s["delete_service"] and DELETE_SERVICE_MESSAGES:
        try: await message.delete()
        except Exception: pass


@dp.message(F.left_chat_member)
async def left_member(message: Message):
    if message.chat.type == ChatType.PRIVATE: return
    s = get_settings(message.chat.id)
    if s["delete_service"] and DELETE_SERVICE_MESSAGES:
        try: await message.delete()
        except Exception: pass


@dp.message()
async def moderation(message: Message):
    if message.chat.type == ChatType.PRIVATE or not message.from_user: return
    ensure_settings(message.chat.id)
    s = get_settings(message.chat.id)
    if await is_admin(message.chat.id, message.from_user.id): return
    text = message.text or message.caption or ""

    if CHECK_SUBSCRIPTION_IN_GROUPS and s["check_sub"]:
        if not await is_subscribed(message.from_user.id):
            try: await message.delete()
            except Exception: pass
            await temporary_notice(message.chat.id, f"⚠️ <b>{message.from_user.full_name}</b>, avval kanalga obuna bo‘ling.", 8)
            return

    if DELETE_LINKS and s["delete_links"] and text and LINK_RE.search(text):
        try: await message.delete()
        except Exception: pass
        warns = add_warning(message.chat.id, message.from_user.id)
        await temporary_notice(message.chat.id, f"🚫 Link/reklama mumkin emas. Ogohlantirish: {warns}/{WARN_LIMIT}", 8)
        await punish_if_needed(message, warns)
        return

    if DELETE_BAD_WORDS and s["delete_bad_words"] and text and contains_bad_word(text):
        try: await message.delete()
        except Exception: pass
        warns = add_warning(message.chat.id, message.from_user.id)
        await temporary_notice(message.chat.id, f"🚫 Odob saqlang. Ogohlantirish: {warns}/{WARN_LIMIT}", 8)
        await punish_if_needed(message, warns)
        return

    has_media = bool(message.photo or message.video or message.animation or message.document or message.sticker)

    if DELETE_ADULT_MEDIA and s["delete_adult_media"] and text and contains_adult_word(text):
        try: await message.delete()
        except Exception: pass
        warns = add_warning(message.chat.id, message.from_user.id)
        await temporary_notice(message.chat.id, f"🚫 Bunday kontent mumkin emas. Ogohlantirish: {warns}/{WARN_LIMIT}", 8)
        await punish_if_needed(message, warns)
        return

    if DELETE_ADULT_MEDIA and s["delete_adult_media"] and s.get("ai_media_filter", 1) and has_media and GOOGLE_VISION_API_KEY:
        unsafe, result = await is_media_unsafe_by_ai(message)
        if unsafe:
            try: await message.delete()
            except Exception: pass
            warns = add_warning(message.chat.id, message.from_user.id)
            await temporary_notice(message.chat.id, f"🚫 AI filter: 18+ media o‘chirildi. Ogohlantirish: {warns}/{WARN_LIMIT}", 8)
            await punish_if_needed(message, warns)
            return


async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    me = await bot.get_me()
    logging.info("Bot started: @%s", me.username)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
