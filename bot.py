import asyncio
import os
import re
import logging
import sqlite3
import tempfile
from pathlib import Path

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
import yt_dlp

# --- НАСТРОЙКИ ---
ADMIN_ID = int(os.getenv("ADMIN_ID", "8609012191"))
AUTHOR = "@vvpse"
BOT_USERNAME = "@yt1savebot"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- БАЗА ПОЛЬЗОВАТЕЛЕЙ ---
DB_PATH = "users.db"
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    conn.close()

def add_user(user_id, username):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?,?)", (user_id, username))
    conn.commit()
    conn.close()

def get_users_count():
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return count

def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    users = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()
    return [u[0] for u in users]

# --- ТЕКСТЫ (вежливые, без смайлов) ---
TEXTS = {
    "start": f"Здравствуйте.\n\nОтправьте ссылку на видео из TikTok, Instagram или YouTube, и я скачаю его для вас в максимальном качестве.\n\nРаботает быстро. Поддерживаются обычные видео, Reels, Shorts, Stories.\n\nАвтор бота: {AUTHOR}",
    "help": "Как пользоваться:\n1. Скопируйте ссылку на видео\n2. Отправьте ее сюда\n3. Получите файл\n\nПоддерживаемые сервисы: TikTok, Instagram, YouTube.\n\nЕсли видео приватное - скачать не получится.",
    "wait": "Принято. Обрабатываю видео, пожалуйста, подождите.",
    "done": "Готово. Ваше видео:",
    "error": "Не удалось скачать видео. Пожалуйста, проверьте, что ссылка корректна и видео находится в открытом доступе.",
    "too_big": "Видео слишком большое для отправки через Telegram. Попробуйте ссылку на более короткое видео.",
    "not_url": "Пожалуйста, отправьте корректную ссылку на TikTok, Instagram или YouTube.",
}

URL_RE = re.compile(r"https?://(www\.)?(tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com|instagram\.com|instagr\.am|youtube\.com|youtu\.be|youtube-nocookie\.com)/\S+")

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

# --- СКАЧИВАНИЕ (быстрый режим) ---
def get_ydl_opts(temp_dir: str):
    return {
        'format': 'bv*[ext=mp4][height<=1080]+ba[ext=m4a]/b[ext=mp4] / bv*+ba/b',
        'outtmpl': f'{temp_dir}/%(title).80s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'merge_output_format': 'mp4',
        'concurrent_fragment_downloads': 5,
        'http_headers': {'User-Agent': 'Mozilla/5.0'},
    }

def download_sync(url: str, temp_dir: str) -> Path:
    opts = get_ydl_opts(temp_dir)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # Находим скачанный файл
        files = list(Path(temp_dir).glob("*"))
        if not files:
            raise FileNotFoundError("Файл не найден после загрузки")
        # Берем самый большой файл
        file_path = max(files, key=lambda p: p.stat().st_size)
        return file_path

# --- БОТ ---
async def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN не установлен в переменных окружения Render")

    init_db()
    bot = Bot(token=token)
    dp = Dispatcher()

    @dp.message(Command("start", "help"))
    async def cmd_start(message: types.Message):
        add_user(message.from_user.id, message.from_user.username)
        await message.answer(TEXTS["start"])
        if message.from_user.id == ADMIN_ID:
            await message.answer(f"Вы вошли как главный администратор.\nID: {ADMIN_ID}\nКоманды: /stats, /broadcast")

    @dp.message(Command("stats"))
    async def cmd_stats(message: types.Message):
        if not is_admin(message.from_user.id):
            return
        count = get_users_count()
        await message.answer(f"Статистика бота {BOT_USERNAME}:\nПользователей: {count}\nАвтор: {AUTHOR}")

    @dp.message(Command("broadcast"))
    async def cmd_broadcast(message: types.Message):
        if not is_admin(message.from_user.id):
            return
        text = message.text.replace("/broadcast", "").strip()
        if not text:
            await message.answer("Использование: /broadcast Текст рассылки")
            return
        users = get_all_users()
        sent = 0
        for uid in users:
            try:
                await bot.send_message(uid, text)
                sent += 1
                await asyncio.sleep(0.05)
            except:
                continue
        await message.answer(f"Рассылка завершена. Отправлено: {sent}/{len(users)}")

    @dp.message(F.text & F.text.regexp(URL_RE))
    async def handle_link(message: types.Message):
        add_user(message.from_user.id, message.from_user.username)
        url = message.text.strip()

        status_msg = await message.answer(TEXTS["wait"])

        tmpdir = tempfile.mkdtemp()
        try:
            # Скачиваем в отдельном потоке чтобы не блокировать бота
            file_path: Path = await asyncio.to_thread(download_sync, url, tmpdir)

            if file_path.stat().st_size > 1900 * 1024 * 1024:
                await status_msg.edit_text(TEXTS["too_big"])
                return

            # Отправляем как видео, если меньше 50МБ, иначе как документ - быстрее доходит
            caption = TEXTS["done"]
            if file_path.stat().st_size < 50 * 1024 * 1024:
                await bot.send_video(
                    chat_id=message.chat.id,
                    video=types.FSInputFile(str(file_path)),
                    caption=caption,
                    supports_streaming=True
                )
            else:
                await bot.send_document(
                    chat_id=message.chat.id,
                    document=types.FSInputFile(str(file_path)),
                    caption=caption
                )
            await status_msg.delete()

        except Exception as e:
            logger.error(f"Ошибка загрузки {url}: {e}")
            await status_msg.edit_text(TEXTS["error"])
        finally:
            # Чистка
            for p in Path(tmpdir).glob("*"):
                try: p.unlink()
                except: pass
            try: Path(tmpdir).rmdir()
            except: pass

    @dp.message()
    async def handle_other(message: types.Message):
        await message.answer(TEXTS["not_url"])

    logger.info(f"Бот {BOT_USERNAME} запущен. Админ {ADMIN_ID}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
