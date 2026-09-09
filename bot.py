import asyncio, os, re, logging, sqlite3, tempfile, uuid
from pathlib import Path
import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web
import yt_dlp

ADMIN_ID = int(os.getenv("ADMIN_ID", "8609012191"))

# ID именно из https://t.me/addemoji/NewsEmoji
EMO = {
    "wave": "5447649783980555943", # 👋 из NewsEmoji
    "fire": "5447681194564420455", # 🔥 из NewsEmoji
    "rocket": "5447649188827747733", # 🚀 из NewsEmoji
    "clock": "5447650563584818113", # ⏳ из NewsEmoji
    "spark": "5447649972439735187", # ✨ из NewsEmoji
    "dl": "5447649922290789914", # 📥 из NewsEmoji
    "news": "5447649783980555943", # 📰 из NewsEmoji
}
CAPTION = f'<tg-emoji emoji-id="{EMO["fire"]}">🔥</tg-emoji> скачано с помощью @yt1savebot'

PENDING={}
URL_RE=re.compile(r"https?://\S+")
logging.basicConfig(level=logging.INFO)
logger=logging.getLogger(__name__)

def init_db():
    conn=sqlite3.connect("users.db"); conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT)"); conn.commit(); conn.close()
def add_user(uid,uname):
    try:
        c=sqlite3.connect("users.db"); c.execute("INSERT OR IGNORE INTO users VALUES (?,?)",(uid,uname)); c.commit(); c.close()
    except: pass

def get_base():
    return {'quiet':True,'no_warnings':True,'nocheckcertificate':True,'geo_bypass':True,'noplaylist':False,
            'extractor_args':{'youtube':{'player_client':['android_music','android','ios'],'formats':['missing_pot']}},
            'http_headers':{'User-Agent':'Mozilla/5.0'}}

def get_info(url):
    with yt_dlp.YoutubeDL(get_base()) as ydl: return ydl.extract_info(url, download=False)

def get_opts(q,tmp):
    o=get_base()
    if q=="audio":
        o.update({'format':'bestaudio/best','outtmpl':f'{tmp}/%(title).80s.%(ext)s','postprocessors':[{'key':'FFmpegExtractAudio','preferredcodec':'mp3','preferredquality':'192'}]})
    else:
        fmt='bv*[ext=mp4][height<=2160]+ba[ext=m4a]/b[ext=mp4] / bv*+ba/b' if q=="best" else f'bv*[height<={q}]+ba/b[height<={q}] / b'
        o.update({'format':fmt,'outtmpl':f'{tmp}/%(title).80s.%(ext)s','merge_output_format':'mp4'})
    return o

def dl_yt(url,q,tmp):
    for quality in [q,"best"]:
        try:
            with yt_dlp.YoutubeDL(get_opts(quality,tmp)) as ydl: ydl.download([url])
            files=[p for p in Path(tmp).glob("*") if p.is_file() and p.stat().st_size>1000]
            if files: return files
        except Exception as e:
            logger.warning(f"yt-dlp {quality} fail {e}")
            for p in Path(tmp).glob("*"):
                try: p.unlink()
                except: pass
            if quality=="best": raise
    return []

async def dl_cobalt(url,tmp):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post("https://api.cobalt.tools/api/json", json={"url":url,"vCodec":"h264","vQuality":"720"}, headers={"Accept":"application/json","Content-Type":"application/json"}, timeout=30) as r:
                data=await r.json()
                durl=data.get("url")
                if not durl: return None
                fp=Path(tmp)/"video.mp4"
                async with s.get(durl) as d:
                    with open(fp,'wb') as f:
                        async for ch in d.content.iter_chunked(8192): f.write(ch)
                return [fp]
    except Exception as e:
        logger.error(f"cobalt {e}"); return None

def kb(uid,info):
    b=InlineKeyboardBuilder()
    hs=sorted(set(f.get('height') for f in info.get('formats',[]) if f.get('height')), reverse=True)
    u=[]
    for h in hs:
        if h>=360 and (not u or abs(h-u[-1])>80): u.append(h)
    u=u[:4]
    b.button(text="Лучшее", callback_data=f"q:best:{uid}")
    for h in u: b.button(text=f"{h}p", callback_data=f"q:{h}:{uid}")
    b.button(text="Аудио", callback_data=f"q:audio:{uid}")
    b.adjust(1,2,2); return b.as_markup()

async def health(r): return web.Response(text="ok")
async def start_web():
    app=web.Application(); app.router.add_get("/", health)
    runner=web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner,"0.0.0.0",int(os.getenv("PORT",10000))).start()

async def main():
    bot=Bot(token=os.getenv("BOT_TOKEN")); dp=Dispatcher(); init_db()

    @dp.message(Command("start"))
    async def st(m: types.Message):
        add_user(m.from_user.id,m.from_user.username)
        txt=f'<tg-emoji emoji-id="{EMO["wave"]}">👋</tg-emoji> Здравствуйте.\n\nОтправьте любую ссылку на видео, фото, пост, сторис, рилс.\n\n<tg-emoji emoji-id="{EMO["rocket"]}">🚀</tg-emoji> Поддерживаю 1800+ сайтов: TikTok, Instagram, YouTube, Twitter, Facebook, Reddit и другие.\n\n<tg-emoji emoji-id="{EMO["news"]}">📰</tg-emoji> Автор: @vvpse\nБот: @yt1savebot'
        await m.answer(txt, parse_mode="HTML")

    @dp.message(F.text & F.text.regexp(URL_RE))
    async def link(m: types.Message):
        add_user(m.from_user.id,m.from_user.username)
        url=URL_RE.search(m.text).group(0)
        wait=await m.answer(f'<tg-emoji emoji-id="{EMO["clock"]}">⏳</tg-emoji> Принято. Проверяю ссылку, подождите.', parse_mode="HTML")
        try: info=await asyncio.to_thread(get_info, url)
        except: info={'title':'медиа','formats':[{'height':1080},{'height':720},{'height':480}]}
        uid=uuid.uuid4().hex[:8]; PENDING[uid]=url
        await wait.edit_text(f'<tg-emoji emoji-id="{EMO["spark"]}">✨</tg-emoji> Найдено: {info.get("title","медиа")[:70]}\n\nВыберите качество:', reply_markup=kb(uid,info), parse_mode="HTML")

    @dp.callback_query(F.data.startswith("q:"))
    async def cq(call: types.CallbackQuery):
        await call.answer(); _,q,uid=call.data.split(":",2); url=PENDING.get(uid)
        if not url: await call.message.edit_text("Ссылка устарела"); return
        await call.message.edit_text(f'<tg-emoji emoji-id="{EMO["dl"]}">📥</tg-emoji> Скачиваю {q}...', parse_mode="HTML")
        tmp=tempfile.mkdtemp()
        try:
            files=await asyncio.to_thread(dl_yt, url, q, tmp)
            if not files and "youtu" in url: files=await dl_cobalt(url,tmp) or []
            if not files: raise Exception("empty")
            for f in sorted(files, key=lambda x: x.stat().st_size):
                ext=f.suffix.lower()
                if ext in ['.jpg','.jpeg','.png','.webp']: await call.message.bot.send_photo(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION, parse_mode="HTML")
                elif ext in ['.mp3','.m4a']: await call.message.bot.send_audio(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION, parse_mode="HTML")
                else:
                    if f.stat().st_size<50*1024*1024: await call.message.bot.send_video(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION, parse_mode="HTML", supports_streaming=True)
                    else: await call.message.bot.send_document(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION, parse_mode="HTML")
            await call.message.delete()
        except Exception as e:
            logger.error(e); await call.message.edit_text("Не удалось скачать. Попробуйте через минуту.")
        finally:
            for p in Path(tmp).glob("*"):
                try: p.unlink()
                except: pass
            PENDING.pop(uid,None)

    asyncio.create_task(start_web()); await dp.start_polling(bot)

if __name__=="__main__": asyncio.run(main())
