import asyncio
import os
import re
import logging
import sqlite3
import tempfile
import uuid
from pathlib import Path

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web
import yt_dlp

ADMIN_ID = int(os.getenv("ADMIN_ID", "8609012191"))
AUTHOR = "@vvpse"
BOT_USERNAME = "@yt1savebot"
# Премиум эмодзи в подписи
CAPTION = '<tg-emoji emoji-id="5368324170141244696">🔥</tg-emoji> скачано с помощью @yt1savebot'

PENDING_URLS = {}
URL_RE = re.compile(r"https?://\S+")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = "users.db"
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT)")
    conn.commit()
    conn.close()

def add_user(uid, uname):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR IGNORE INTO users VALUES (?,?)", (uid, uname))
        conn.commit()
        conn.close()
    except: pass

def get_users_count():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        return c
    except: return 0

def is_admin(uid): return uid == ADMIN_ID

# --- ФИКС ДЛЯ YOUTUBE ---
def get_base_opts():
    return {
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'geo_bypass': True,
        'noplaylist': False,
        'allow_unplayable_formats': False,
        'extractor_args': {
            'youtube': {
                'player_client': ['android_music', 'android', 'ios'],
                'formats': ['missing_pot'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }
    }

def get_info_sync(url: str):
    opts = get_base_opts()
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

def get_ydl_opts_for_quality(quality: str, temp_dir: str):
    opts = get_base_opts()
    if quality == "audio":
        opts.update({
            'format': 'bestaudio/best',
            'outtmpl': f'{temp_dir}/%(title).80s.%(ext)s',
            'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}]
        })
        return opts

    if quality == "best":
        fmt = 'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4] / bv*+ba/b'
    else:
        try:
            h = int(quality)
            fmt = f'bv*[height<={h}][ext=mp4]+ba[ext=m4a]/b[ext=mp4] / b[height<={h}] / b'
        except:
            fmt = 'bv*+ba/b'

    opts.update({
        'format': fmt,
        'outtmpl': f'{temp_dir}/%(title).80s.%(ext)s',
        'merge_output_format': 'mp4',
        'concurrent_fragment_downloads': 5,
    })
    return opts

def download_with_quality_sync(url: str, quality: str, temp_dir: str):
    # пробуем выбранное качество, если не вышло - пробуем best
    for q in [quality, "best"]:
        try:
            opts = get_ydl_opts_for_quality(q, temp_dir)
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            files = [p for p in Path(temp_dir).glob("*") if p.is_file() and p.stat().st_size > 1000]
            if files:
                return files
        except Exception as e:
            logger.warning(f"fail quality {q} for {url}: {e}")
            # чистим перед ретраем
            for p in Path(temp_dir).glob("*"):
                try: p.unlink()
                except: pass
            if q == "best":
                raise
    return []

def build_quality_keyboard(url_id: str, info: dict):
    builder = InlineKeyboardBuilder()
    heights = sorted(set(f.get('height') for f in info.get('formats', []) if f.get('height')), reverse=True)
    uniq = []
    for h in heights:
        if h >= 360 and (not uniq or abs(h - uniq[-1]) > 80):
            uniq.append(h)
    uniq = uniq[:4]

    builder.button(text="Лучшее качество", callback_data=f"q:best:{url_id}")
    for h in uniq:
        builder.button(text=f"{h}p", callback_data=f"q:{h}:{url_id}")
    builder.button(text="Только аудио", callback_data=f"q:audio:{url_id}")
    builder.adjust(1, 2, 2)
    return builder.as_markup()

async def health_handler(request):
    return web.Response(text="Bot is running")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 10000)))
    await site.start()

async def main():
    token = os.getenv("BOT_TOKEN")
    if not token: raise ValueError("BOT_TOKEN нет")
    init_db()
    bot = Bot(token=token)
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def cmd_start(m: types.Message):
        add_user(m.from_user.id, m.from_user.username)
        text = (
            '<tg-emoji emoji-id="5360871210812722222">👋</tg-emoji> Здравствуйте.\n\n'
            'Отправьте любую ссылку на видео, фото, пост, сторис, рилс.\n\n'
            '<tg-emoji emoji-id="5310127438583196520">🚀</tg-emoji> Поддерживаю 1800+ сайтов: TikTok, Instagram, YouTube, Twitter, Facebook, Reddit, Pinterest, Twitch и другие.\n\n'
            f'Автор: {AUTHOR}\n'
            f'Бот: {BOT_USERNAME}'
        )
        await m.answer(text, parse_mode="HTML")

    @dp.message(Command("stats"))
    async def cmd_stats(m: types.Message):
        if not is_admin(m.from_user.id): return
        await m.answer(f"Юзеров: {get_users_count()}")

    @dp.message(F.text & F.text.regexp(URL_RE))
    async def handle_link(message: types.Message):
        add_user(message.from_user.id, message.from_user.username)
        url = URL_RE.search(message.text).group(0)
        wait = await message.answer('<tg-emoji emoji-id="5373141898414512120">⏳</tg-emoji> Принято. Проверяю ссылку, пожалуйста, подождите.', parse_mode="HTML")
        try:
            info = await asyncio.to_thread(get_info_sync, url)
        except Exception as e:
            logger.error(f"info fail {e}")
            info = {'title': 'медиа', 'formats': [{'height': 1080}, {'height': 720}, {'height': 480}]}

        url_id = uuid.uuid4().hex[:8]
        PENDING_URLS[url_id] = url
        title = info.get('title', 'медиа')[:80]

        text = f'<tg-emoji emoji-id="5373106323313069845">✨</tg-emoji> Найдено: {title}\n\nВыберите качество:'
        kb = build_quality_keyboard(url_id, info)
        await wait.edit_text(text, reply_markup=kb, parse_mode="HTML")

    @dp.callback_query(F.data.startswith("q:"))
    async def handle_quality(call: types.CallbackQuery):
        await call.answer()
        try:
            _, quality, url_id = call.data.split(":", 2)
        except: return
        url = PENDING_URLS.get(url_id)
        if not url:
            await call.message.edit_text("Ссылка устарела, отправьте заново.")
            return

        await call.message.edit_text(f'<tg-emoji emoji-id="5321422196338757601">📥</tg-emoji> Скачиваю в качестве {quality}. Пожалуйста, подождите.', parse_mode="HTML")
        tmpdir = tempfile.mkdtemp()
        try:
            files = await asyncio.to_thread(download_with_quality_sync, url, quality, tmpdir)
            if not files:
                raise FileNotFoundError("empty")

            for f in sorted(files, key=lambda x: x.stat().st_size):
                ext = f.suffix.lower()
                if ext in ['.jpg','.jpeg','.png','.webp']:
                    await call.message.bot.send_photo(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION, parse_mode="HTML")
                elif ext in ['.mp3','.m4a']:
                    await call.message.bot.send_audio(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION, parse_mode="HTML")
                else:
                    if f.stat().st_size < 50*1024*1024:
                        await call.message.bot.send_video(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION, parse_mode="HTML", supports_streaming=True)
                    else:
                        await call.message.bot.send_document(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION, parse_mode="HTML")
            await call.message.delete()
        except Exception as e:
            logger.error(f"Download error {url} {e}")
            await call.message.edit_text("Не удалось скачать. Попробуйте еще раз или отправьте другую ссылку. Если это YouTube - попробуйте через минуту, YouTube иногда ограничивает.", parse_mode="HTML")
        finally:
            for p in Path(tmpdir).glob("*"):
                try: p.unlink()
                except: pass
            try: Path(tmpdir).rmdir()
            except: pass
            PENDING_URLS.pop(url_id, None)

    asyncio.create_task(start_web_server())
    logger.info(f"Bot {BOT_USERNAME} started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
