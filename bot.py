import asyncio, os, re, logging, sqlite3, tempfile, uuid
from pathlib import Path
import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web
import yt_dlp

EMO = {
    "wave": "5440309614089650198",
    "fire": "5440381431488130833",
    "rocket": "5440381431488130834",
    "clock": "5440704566153857237",
    "spark": "5440381431488130835",
    "dl": "5440381431488130836",
}
CAPTION = f'<tg-emoji emoji-id="{EMO["fire"]}">🔥</tg-emoji> скачано с помощью @yt1savebot'
PENDING={}; URL_RE=re.compile(r"https?://\S+")
logging.basicConfig(level=logging.INFO); logger=logging.getLogger(__name__)

def init_db():
    c=sqlite3.connect("users.db"); c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT)"); c.commit(); c.close()
def add_user(uid,uname):
    try: c=sqlite3.connect("users.db"); c.execute("INSERT OR IGNORE INTO users VALUES (?,?)",(uid,uname)); c.commit(); c.close()
    except: pass

def get_base():
    opts={'quiet':False,'no_warnings':False,'nocheckcertificate':True,'geo_bypass':True,'noplaylist':False,'concurrent_fragment_downloads':5,'extractor_retries':3,'extractor_args':{'youtube':{'player_client':['android','ios']}}}
    if Path("cookies.txt").exists(): opts['cookiefile']='cookies.txt'
    return opts

def get_info(url):
    with yt_dlp.YoutubeDL(get_base()) as ydl: return ydl.extract_info(url, download=False)

def get_opts(q,tmp):
    o=get_base()
    fmt='bv*[ext=mp4][height<=2160]+ba[ext=m4a]/b[ext=mp4] / bv*+ba/b' if q=="best" else f'bv*[height<={q}][ext=mp4]+ba[ext=m4a]/b[height<={q}] / b'
    o.update({'format':fmt,'outtmpl':f'{tmp}/%(title).80s.%(ext)s','merge_output_format':'mp4'})
    return o

def dl_yt(url,q,tmp):
    with yt_dlp.YoutubeDL(get_opts(q,tmp)) as ydl: ydl.download([url])
    return [p for p in Path(tmp).glob("*") if p.is_file() and p.stat().st_size>1000]

async def dl_cobalt(url,tmp):
    for api in ["https://api.cobalt.tools/api/json","https://co.wuk.sh/api/json"]:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(api, json={"url":url,"vCodec":"h264","vQuality":"720"}, headers={"Accept":"application/json","Content-Type":"application/json"}, timeout=30) as r:
                    data=await r.json(); durl=data.get("url")
                    if not durl: continue
                    fp=Path(tmp)/"video.mp4"
                    async with s.get(durl) as d:
                        with open(fp,'wb') as f:
                            async for ch in d.content.iter_chunked(8192): f.write(ch)
                    if fp.exists(): return [fp]
        except Exception as e: logger.warning(f"cobalt {api} {e}"); continue
    return None

def kb(uid,info):
    b=InlineKeyboardBuilder()
    hs=sorted(set(f.get('height') for f in info.get('formats',[]) if f.get('height')), reverse=True)
    u=[]
    for h in hs:
        if h>=360 and (not u or abs(h-u[-1])>80): u.append(h)
    u=u[:5]
    b.button(text="Лучшее качество", callback_data=f"q:best:{uid}")
    for h in u: b.button(text=f"{h}p", callback_data=f"q:{h}:{uid}")
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
        txt=(
            f'<tg-emoji emoji-id="{EMO["wave"]}">👋</tg-emoji> Здравствуйте.\n\n'
            f'Отправьте любую ссылку на видео, фото, пост, сторис, рилс.\n\n'
            f'<tg-emoji emoji-id="{EMO["rocket"]}">🚀</tg-emoji> Поддерживаю 1800+ сайтов: TikTok, Instagram, YouTube, Twitter, Facebook, Reddit, Pinterest, Twitch и другие.\n\n'
            f'Автор: @vvpse\nБот: @yt1savebot'
        )
        await m.answer(txt, parse_mode="HTML")

    @dp.message(F.text & F.text.regexp(URL_RE))
    async def link(m: types.Message):
        add_user(m.from_user.id,m.from_user.username); url=URL_RE.search(m.text).group(0)
        wait=await m.answer(f'<tg-emoji emoji-id="{EMO["clock"]}">⏳</tg-emoji> Принято. Проверяю ссылку, пожалуйста, подождите.', parse_mode="HTML")
        try: info=await asyncio.to_thread(get_info, url)
        except Exception as e: logger.error(f"info {e}"); info={'title':'медиа','formats':[{'height':1080},{'height':720},{'height':480}]}
        uid=uuid.uuid4().hex[:8]; PENDING[uid]=url
        await wait.edit_text(f'<tg-emoji emoji-id="{EMO["spark"]}">✨</tg-emoji> Найдено: {info.get("title","медиа")[:70]}\n\nВыберите качество:', reply_markup=kb(uid,info), parse_mode="HTML")

    # Этот хендлер только для премиум эмодзи, ссылки он не трогает
    @dp.message(lambda m: m.entities and any(e.type=="custom_emoji" for e in m.entities))
    async def get_emoji_id(m: types.Message):
        for ent in m.entities:
            if ent.type=="custom_emoji":
                await m.answer(f"ID: <code>{ent.custom_emoji_id}</code>", parse_mode="HTML")

    @dp.callback_query(F.data.startswith("q:"))
    async def cq(call: types.CallbackQuery):
        await call.answer(); _,q,uid=call.data.split(":",2); url=PENDING.get(uid)
        if not url: await call.message.edit_text("Ссылка устарела"); return
        await call.message.edit_text(f'<tg-emoji emoji-id="{EMO["dl"]}">📥</tg-emoji> Скачиваю в качестве {q}. Пожалуйста, подождите.', parse_mode="HTML")
        tmp=tempfile.mkdtemp()
        try:
            files=await asyncio.to_thread(dl_yt, url, q, tmp)
            if not files and "youtu" in url: files=await dl_cobalt(url,tmp)
            if not files: raise Exception("empty")
            for f in sorted(files, key=lambda x: x.stat().st_size):
                if f.stat().st_size<50*1024*1024:
                    await call.message.bot.send_video(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION, parse_mode="HTML", supports_streaming=True)
                else:
                    await call.message.bot.send_document(call.message.chat.id, types.FSInputFile(str(f)), caption=CAPTION, parse_mode="HTML")
            await call.message.delete()
        except Exception as e:
            logger.error(f"FINAL {e}", exc_info=True)
            await call.message.edit_text(f"Не удалось скачать: {e}")
        finally:
            for p in Path(tmp).glob("*"):
                try: p.unlink()
                except: pass
            PENDING.pop(uid,None)

    asyncio.create_task(start_web()); await dp.start_polling(bot)

if __name__=="__main__": asyncio.run(main())
