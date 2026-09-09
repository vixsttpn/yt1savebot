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

# --- НАСТРОЙКИ ---
ADMIN_ID = int(os.getenv("ADMIN_ID", "8609012191"))
AUTHOR = "@vvpse"
BOT_USERNAME = "@yt1savebot"
CAPTION = "скачано с помощью @yt1savebot"

PENDING_URLS = {}
URL_RE = re.compile(r"https?://\S+")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- БАЗА ---
DB_PATH = "users.db"
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT)")
    conn.commit()
    conn.close()

def add_user(user_id, username):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR IGNORE INTO users VALUES (?,?)", (user_id, username))
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

def get_all_users():
    try:
        conn = sqlite3.connect(DB_PATH)
        users = conn.execute("SELECT user_id FROM users").fetchall()
        conn.close()
        return [u[0] for u in users]
    except: return []

def is_admin(uid): return uid == ADMIN_ID

# --- YT-DLP НАСТРОЙКИ (фикс для YouTube на Render) ---
def get_base_opts():
    return {
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'geo_bypass': True,
        'noplaylist': False,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web'],
                'player_skip': ['webpage', 'configs'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        }
    }

def get_info_sync(url: str):
    opts = get_base_opts()
    opts.update({'extract_flat': False})
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
        fmt = 'bv*[ext=mp4][height<=2160]+ba[ext=m4a]/b[ext=mp4] / bv*+ba/b'
    else:
        try:
            h = int(quality)
            fmt = f'bv*[height<={h}][ext=mp4]+ba[ext=m4a]/b[ext=mp4] / bv*[height<={h}]+ba/b[height<={h}] / b'
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
    opts = get_ydl_opts_for_quality(quality, temp_dir)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = [p for p in Path(temp_dir).glob("*") if p.is_file()]
    return files

def build_quality_keyboard(url_id: str, info: dict):
    builder = InlineKeyboardBuilder()
    heights = sorted(set(f.get('height') for f in info.get('formats', []) if f.get('height')), reverse=True)
    uniq = []
    for h in heights:
        if h >= 360 and (not uniq or abs(h - uniq[-1]) > 100):
            uniq.append(h)
    uniq = uniq[:4]

    builder.button(text="Лучшее качество", callback_data=f"q:best:{url_id}")
    for h in uniq:
        builder.button(text=f"{h}p", callback_data=f"q:{h}:{url_id}")
    builder.button(text="Только аудио", callback_data=f"q:audio:{url_id}")
    builder.adjust(1, 2, 2)
    return builder.as_markup()

# --- ВЕБ СЕРВЕР ДЛЯ RENDER ---
async def health_handler(request):
    return web.Response(text="Bot is running")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web server started on port {port}")

# --- БОТ ---
async def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN не установлен")

    init_db()
    bot = Bot(token=token)
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def cmd_start(m: types.Message):
        add_user(m.from_user.id, m.from_user.username)
        await m.answer(
            "Здравствуйте.\n\n"
            "Отправьте любую ссылку на видео, фото, пост, сторис, рилс.\n\n"
            "Поддерживаю 1800+ сайтов: TikTok, Instagram, YouTube, Twitter, Facebook, Reddit, Pinterest, Twitch и другие.\n\n"
            f"Автор: {AUTHOR}\n"
            f"Бот: {BOT_USERNAME}"
        )

    @dp.message(Command("stats"))
    async def cmd_stats(m: types.Message):
        if not is_admin(m.from_user.id): return
        await m.answer(f"Статистика {BOT_USERNAME}:\nПользователей: {get_users_count()}")

    @dp.message(Command("broadcast"))
    async def cmd_broadcast(m: types.Message):
        if not is_admin(m.from_user.id): return
        text = m.text.replace("/broadcast", "").strip()
        if not text:
            await m.answer("Использование: /broadcast Текст рассылки")
            return
        users = get_all_users()
        sent = 0
        for uid in users:
            try:
                await bot.send_message(uid, text)
                sent += 1
                await asyncio.sleep(0.05)
            except: continue
        await m.answer(f"Рассылка завершена. Отправлено: {sent}/{len(users)}")

    @dp.message(F.text & F.text.regexp(URL_RE))
    async def handle_link(message: types.Message):
        add_user(message.from_user.id, message.from_user.username)
        url = URL_RE.search(message.text).group(0)

        wait = await message.answer("Принято. Проверяю ссылку, пожалуйста, подождите.")

        try:
            info = await asyncio.to_thread(get_info_sync, url)
        except Exception as e:
            logger.error(f"info fail {url}: {e}")
            info = {'title': 'медиа', 'formats': [{'height': 1080}, {'height': 720}, {'height': 480}, {'height': 360}]}

        url_id = uuid.uuid4().hex[:8]
        PENDING_URLS[url_id] = url

        title = info.get('title', 'медиа')
        if 'entries' in info:
            try:
                count = len(list(info['entries']))
                text = f"Найдено: {title}\nФайлов: {count}\n\nВыберите качество для видео. Для фото будет скачано в оригинале."
            except:
                text = f"Найдено: {title}\n\nВыберите качество:"
        else:
            text = f"Найдено: {title}\n\nВыберите качество, в котором скачать:"

        kb = build_quality_keyboard(url_id, info)
        await wait.edit_text(text, reply_markup=kb)

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

        await call.message.edit_text(f"Скачиваю в качестве {quality}. Пожалуйста, подождите.")

        tmpdir = tempfile.mkdtemp()
        try:
            files = await asyncio.to_thread(download_with_quality_sync, url, quality, tmpdir)
            if not files:
                await call.message.edit_text("Не удалось скачать файл. Проверьте, что ссылка открытая.")
                return

            for f in sorted(files, key=lambda x: x.stat().st_size):
                ext = f.suffix.lower()
                if ext in ['.jpg', '.jpeg', '.png', '.webp']:
                    await call.message.bot.send_photo(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION)
                elif ext in ['.mp3', '.m4a', '.opus']:
                    await call.message.bot.send_audio(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION)
                else:
                    if f.stat().st_size < 50 * 1024 * 1024:
                        await call.message.bot.send_video(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION, supports_streaming=True)
                    else:
                        await call.message.bot.send_document(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION)

            await call.message.delete()

        except Exception as e:
            logger.error(f"Download error {e}")
            await call.message.edit_text("Не удалось скачать. Возможно видео приватное или сервис ограничил доступ.")
        finally:
            for p in Path(tmpdir).glob("*"):
                try: p.unlink()
                except: pass
            try: Path(tmpdir).rmdir()
            except: pass
            PENDING_URLS.pop(url_id, None)

    @dp.message()
    async def handle_other(message: types.Message):
        await message.answer("Пожалуйста, отправьте ссылку на видео или фото.")

    asyncio.create_task(start_web_server())
    logger.info(f"Бот {BOT_USERNAME} запущен. Админ {ADMIN_ID}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
