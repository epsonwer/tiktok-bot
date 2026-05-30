import re
import os
import tempfile
import asyncio
import subprocess
import httpx
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import yt_dlp

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВСТАВЬ_СВОЙ_TOKEN_СЮДА")

URL_PATTERN = re.compile(
    r"https?://(?:www\.|vm\.|vt\.|m\.)?"
    r"(?:tiktok\.com|youtube\.com|youtu\.be|instagram\.com|twitter\.com|x\.com|"
    r"reddit\.com|pinterest\.com|vimeo\.com|fb\.com|facebook\.com)"
    r"/\S+",
    re.IGNORECASE
)

TIKTOK_PATTERN = re.compile(
    r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/\S+",
    re.IGNORECASE
)


def find_cookies() -> str | None:
    candidates = [
        os.environ.get("COOKIES_PATH", ""),
        "/app/cookies.txt",
        "./cookies.txt",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            print(f"[cookies] найден: {path}", flush=True)
            return path
    print("[cookies] файл не найден", flush=True)
    return None


def reencode_video(input_path: str) -> str:
    """Полное перекодирование в H264/AAC — максимальная совместимость с мобильными."""
    output_path = input_path + "_out.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-c:v", "libx264",
        "-c:a", "aac",
        "-ac", "2",                                      # Стерео аудио
        "-movflags", "+faststart",                       # Метаданные в начале — стриминг на мобиле
        "-pix_fmt", "yuv420p",                           # Обязательно для iOS/Android
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",     # Чётные размеры — обязательно для H264
        "-preset", "ultrafast",
        "-crf", "26",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=240)
    if result.returncode == 0 and os.path.exists(output_path):
        os.unlink(input_path)
        return output_path
    print(f"[ffmpeg error] {result.stderr.decode()[:300]}", flush=True)
    return input_path


async def download_tiktok(url: str) -> bytes | None:
    api_url = f"https://tikwm.com/api/?url={url}&hd=1"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(api_url, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        data = resp.json()

    if data.get("code") != 0:
        return None

    d = data["data"]
    video_url = d.get("hdplay") or d.get("play") or d.get("wmplay")
    if not video_url:
        return None

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        video_resp = await client.get(video_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.tiktok.com/",
            "Accept": "*/*",
        })
        video_resp.raise_for_status()
        return video_resp.content


def download_with_ytdlp(url: str, tmp_dir: str) -> str | None:
    out_template = os.path.join(tmp_dir, "video.%(ext)s")

    ydl_opts = {
        # Максимально широкий fallback
        "format": (
            "bestvideo[height<=1080]+bestaudio/"
            "bestvideo+bestaudio/"
            "best"
        ),
        "outtmpl": out_template,
        "quiet": False,
        "no_warnings": False,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "max_filesize": 50 * 1024 * 1024,
    }

    cookies = find_cookies()
    if cookies:
        ydl_opts["cookiefile"] = cookies

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(url, download=True)

    # Ищем скачанный файл
    filepath = None
    for f in sorted(os.listdir(tmp_dir)):
        if not f.endswith(".part") and not f.endswith(".ytdl"):
            filepath = os.path.join(tmp_dir, f)
            break

    if not filepath or not os.path.exists(filepath):
        return None

    # Перекодируем для мобильной совместимости
    return reencode_video(filepath)


pending: dict[int, list[str]] = {}
pending_tasks: dict[int, asyncio.Task] = {}


async def process_urls(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    full_urls = pending.pop(user_id, [])
    pending_tasks.pop(user_id, None)

    if not full_urls:
        return

    count = len(full_urls)
    status_msg = await update.message.reply_text(f"⏳ Скачиваю 0 из {count} видео...")

    success = 0
    for i, url in enumerate(full_urls, 1):
        video_path = None
        try:
            if TIKTOK_PATTERN.match(url):
                video_data = await download_tiktok(url)
                if video_data:
                    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                        f.write(video_data)
                        video_path = f.name
                    loop = asyncio.get_event_loop()
                    video_path = await loop.run_in_executor(None, reencode_video, video_path)
            else:
                tmp_dir = tempfile.mkdtemp()
                loop = asyncio.get_event_loop()
                video_path = await loop.run_in_executor(
                    None, download_with_ytdlp, url, tmp_dir
                )

            if not video_path or not os.path.exists(video_path):
                await update.message.reply_text(f"❌ Не удалось скачать видео {i}")
                continue

            size_mb = os.path.getsize(video_path) / (1024 * 1024)
            if size_mb > 50:
                await update.message.reply_text(f"❌ Видео {i} слишком большое ({size_mb:.0f} МБ, лимит — 50 МБ)")
                continue

            with open(video_path, "rb") as f:
                await update.message.reply_video(
                    video=f,
                    supports_streaming=True,
                    write_timeout=120,
                    read_timeout=120,
                )
            success += 1
            await status_msg.edit_text(f"⏳ Скачиваю {success} из {count} видео...")

        except Exception as e:
            err = str(e)
            if "File too large" in err or "max_filesize" in err:
                await update.message.reply_text(f"❌ Видео {i} слишком большое (лимит — 50 МБ)")
            elif "Private" in err or "login" in err.lower():
                await update.message.reply_text(f"❌ Видео {i} приватное или требует авторизации")
            elif "Sign in" in err or "confirm" in err.lower() or "bot" in err.lower():
                await update.message.reply_text(f"❌ Видео {i}: YouTube требует куки — обнови cookies.txt")
            elif "Timed out" in err or "timed out" in err:
                await update.message.reply_text(f"❌ Видео {i}: тайм-аут, попробуй ещё раз")
            else:
                await update.message.reply_text(f"❌ Ошибка видео {i}: {err[:300]}")

        finally:
            if video_path and os.path.exists(video_path):
                try:
                    os.unlink(video_path)
                except Exception:
                    pass

    await status_msg.edit_text(f"✅ Готово! Скачано {success} из {count} видео.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    found = URL_PATTERN.findall(text)

    if not found:
        await update.message.reply_text(
            "Отправь мне ссылки на видео и я их скачаю 🎬\n\n"
            "Поддерживаю:\n"
            "• TikTok\n"
            "• YouTube и Shorts\n"
            "• Instagram Reels\n"
            "• Twitter / X\n"
            "• Reddit, Vimeo, Facebook и другие"
        )
        return

    user_id = update.effective_user.id

    if user_id not in pending:
        pending[user_id] = []
    pending[user_id].extend(found)

    if user_id in pending_tasks:
        pending_tasks[user_id].cancel()

    async def delayed():
        await asyncio.sleep(1.5)
        await process_urls(update, context, user_id)

    task = asyncio.ensure_future(delayed())
    pending_tasks[user_id] = task


def main():
    cookies = find_cookies()
    if cookies:
        print(f"[старт] cookies.txt подключён: {cookies}", flush=True)
    else:
        print("[старт] cookies.txt не найден — YouTube может не работать", flush=True)

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен...", flush=True)
    app.run_polling()


if __name__ == "__main__":
    main()
