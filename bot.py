import asyncio, os, re, logging, sqlite3, tempfile, uuid
from pathlib import Path
import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web
import yt_dlp

ADMIN_ID = int(os.getenv("ADMIN_ID", "8609012191"))
# РАБОЧИЕ ID премиум эмодзи - проверены
EMO = {
    "hi": "5368324170141244696", # 👋
    "fire": "5200958574784047905", # 🔥
    "rocket": "5310132166338318303", # 🚀
    "clock": "5373141898414512120", # ⏳
    "spark": "5373106323313069845", # ✨
    "dl": "5321412223168284743", # 📥
    "check": "5360871210812722222", # ✅
}
CAPTION = f'<tg-emoji emoji-id="{EMO["fire"]}">🔥</tg-emoji> скачано с помощью @yt1savebot'

PENDING_URLS = {}
URL_RE = re.compile(r"https?://\S+")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    conn = sqlite3.connect("users.db")
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT)")
    conn.commit(); conn.close()
def add_user(uid, uname):
    try:
        conn = sqlite3.connect("users.db")
        conn.execute("INSERT OR IGNORE INTO users VALUES (?,?)", (uid, uname))
        conn.commit(); conn.close()
    except: pass

def get_base_opts():
    return {
        'quiet': True, 'no_warnings': True, 'nocheckcertificate': True,
        'geo_bypass': True, 'noplaylist': False,
        'extractor_args': {'youtube': {'player_client': ['android_music','android','ios'], 'formats': ['missing_pot']}},
        'http_headers': {'User-Agent': 'Mozilla/5.0'}
    }

def get_info_sync(url):
    with yt_dlp.YoutubeDL(get_base_opts()) as ydl:
        return ydl.extract_info(url, download=False)

def get_ydl_opts(q, tmp):
    opts = get_base_opts()
    if q == "audio":
        opts.update({'format': 'bestaudio/best', 'outtmpl': f'{tmp}/%(title).80s.%(ext)s',
                     'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality':'192'}]})
    else:
        fmt = 'bv*[ext=mp4][height<=2160]+ba[ext=m4a]/b[ext=mp4] / bv*+ba/b' if q=="best" else f'bv*[height<={q}]+ba/b[height<={q}] / b'
        opts.update({'format': fmt, 'outtmpl': f'{tmp}/%(title).80s.%(ext)s', 'merge_output_format':'mp4', 'concurrent_fragment_downloads':5})
    return opts

def download_yt_dlp(url, q, tmp):
    for quality in [q, "best"]:
        try:
            with yt_dlp.YoutubeDL(get_ydl_opts(quality, tmp)) as ydl:
                ydl.download([url])
            files = [p for p in Path(tmp).glob("*") if p.is_file() and p.stat().st_size>1000]
            if files: return files
        except Exception as e:
            logger.warning(f"yt-dlp fail {quality}: {e}")
            for p in Path(tmp).glob("*"):
                try: p.unlink()
                except: pass
            if quality=="best": raise
    return []

async def download_cobalt(url, tmp):
    # Фолбек для ютуба когда yt-dlp режут на Render
    try:
        async with aiohttp.ClientSession() as s:
            payload = {"url": url, "vCodec":"h264","vQuality":"720","aFormat":"best"}
            async with s.post("https://api.cobalt.tools/api/json", json=payload, headers={"Accept":"application/json","Content-Type":"application/json"}, timeout=30) as r:
                data = await r.json()
                logger.info(f"cobalt: {data}")
                dl_url = data.get("url")
                if not dl_url: return None
                fp = Path(tmp)/"video.mp4"
                async with s.get(dl_url) as dl:
                    with open(fp,'wb') as f:
                        async for chunk in dl.content.iter_chunked(8192):
                            f.write(chunk)
                return [fp] if fp.exists() else None
    except Exception as e:
        logger.error(f"cobalt fail {e}")
        return None

def build_kb(uid, info):
    b = InlineKeyboardBuilder()
    heights = sorted(set(f.get('height') for f in info.get('formats',[]) if f.get('height')), reverse=True)
    uniq=[]
    for h in heights:
        if h>=360 and (not uniq or abs(h-uniq[-1])>80): uniq.append(h)
    uniq=uniq[:4]
    b.button(text="Лучшее качество", callback_data=f"q:best:{uid}")
    for h in uniq: b.button(text=f"{h}p", callback_data=f"q:{h}:{uid}")
    b.button(text="Только аудио", callback_data=f"q:audio:{uid}")
    b.adjust(1,2,2)
    return b.as_markup()

async def health(request): return web.Response(text="ok")
async def start_web():
    app=web.Application(); app.router.add_get("/", health)
    runner=web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner,"0.0.0.0",int(os.getenv("PORT",10000))).start()

async def main():
    token=os.getenv("BOT_TOKEN")
    init_db()
    bot=Bot(token=token); dp=Dispatcher()

    @dp.message(Command("start"))
    async def start(m: types.Message):
        add_user(m.from_user.id, m.from_user.username)
        txt = (
            f'<tg-emoji emoji-id="{EMO["hi"]}">👋</tg-emoji> Здравствуйте.\n\n'
            f'Отправьте любую ссылку на видео, фото, пост, сторис, рилс.\n\n'
            f'<tg-emoji emoji-id="{EMO["rocket"]}">🚀</tg-emoji> Поддерживаю 1800+ сайтов: TikTok, Instagram, YouTube, Twitter, Facebook, Reddit и другие.\n\n'
            f'Автор: @vvpse\nБот: @yt1savebot'
        )
        await m.answer(txt, parse_mode="HTML")

    @dp.message(F.text & F.text.regexp(URL_RE))
    async def link(m: types.Message):
        add_user(m.from_user.id, m.from_user.username)
        url=URL_RE.search(m.text).group(0)
        wait=await m.answer(f'<tg-emoji emoji-id="{EMO["clock"]}">⏳</tg-emoji> Принято. Проверяю ссылку, пожалуйста, подождите.', parse_mode="HTML")
        try:
            info=await asyncio.to_thread(get_info_sync, url)
        except:
            info={'title':'медиа','formats':[{'height':1080},{'height':720},{'height':480}]}
        uid=uuid.uuid4().hex[:8]; PENDING_URLS[uid]=url
        txt=f'<tg-emoji emoji-id="{EMO["spark"]}">✨</tg-emoji> Найдено: {info.get("title","медиа")[:80]}\n\nВыберите качество:'
        await wait.edit_text(txt, reply_markup=build_kb(uid, info), parse_mode="HTML")

    @dp.callback_query(F.data.startswith("q:"))
    async def q(call: types.CallbackQuery):
        await call.answer()
        _, quality, uid = call.data.split(":",2)
        url=PENDING_URLS.get(uid)
        if not url:
            await call.message.edit_text("Ссылка устарела, отправьте заново."); return
        await call.message.edit_text(f'<tg-emoji emoji-id="{EMO["dl"]}">📥</tg-emoji> Скачиваю в качестве {quality}. Пожалуйста, подождите.', parse_mode="HTML")
        tmp=tempfile.mkdtemp()
        try:
            files=await asyncio.to_thread(download_yt_dlp, url, quality, tmp)
            if not files and "youtu" in url:
                logger.info("try cobalt fallback")
                files=await download_cobalt(url, tmp) or []

            if not files: raise Exception("empty")

            for f in sorted(files, key=lambda x: x.stat().st_size):
                ext=f.suffix.lower()
                if ext in ['.jpg','.jpeg','.png','.webp']:
                    await call.message.bot.send_photo(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION, parse_mode="HTML")
                elif ext in ['.mp3','.m4a']:
                    await call.message.bot.send_audio(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION, parse_mode="HTML")
                else:
                    if f.stat().st_size<50*1024*1024:
                        await call.message.bot.send_video(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION, parse_mode="HTML", supports_streaming=True)
                    else:
                        await call.message.bot.send_document(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION, parse_mode="HTML")
            await call.message.delete()
        except Exception as e:
            logger.error(f"final fail {e}")
            await call.message.edit_text("Не удалось скачать. Попробуйте еще раз через минуту. YouTube иногда ограничивает датацентр IP.", parse_mode="HTML")
        finally:
            for p in Path(tmp).glob("*"):
                try: p.unlink()
                except: pass
            try: Path(tmp).rmdir()
            except: pass
            PENDING_URLS.pop(uid,None)

    asyncio.create_task(start_web())
    await dp.start_polling(bot)

if __name__=="__main__": asyncio.run(main())
