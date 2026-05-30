import re
import os
import tempfile
import asyncio
import httpx
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import yt_dlp

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8888017850:AAGVphq-pYKa4w_OR1TNaUGJX5H5ARpEefg")

# Паттерны для всех поддерживаемых платформ
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


async def download_tiktok(url: str) -> bytes | None:
    """Скачивает TikTok видео без водяного знака в максимальном качестве."""
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
    """Скачивает видео через yt-dlp, возвращает путь к файлу."""
    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": os.path.join(tmp_dir, "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "noplaylist": True,
        # Лимит 50MB чтобы не превысить лимит Telegram
        "max_filesize": 50 * 1024 * 1024,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        # На случай если расширение поменялось после merge
        if not os.path.exists(filename):
            filename = filename.rsplit(".", 1)[0] + ".mp4"
        return filename if os.path.exists(filename) else None


# Накапливаем ссылки от пользователя
pending: dict[int, list[str]] = {}
pending_tasks: dict[int, asyncio.Task] = {}


async def process_urls(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Обрабатывает все накопленные ссылки пользователя."""
    full_urls = pending.pop(user_id, [])
    pending_tasks.pop(user_id, None)

    if not full_urls:
        return

    count = len(full_urls)
    status_msg = await update.message.reply_text(f"⏳ Скачиваю 0 из {count} видео...")

    success = 0
    for i, url in enumerate(full_urls, 1):
        try:
            video_path = None

            if TIKTOK_PATTERN.match(url):
                # TikTok — через специальный API без водяного знака
                video_data = await download_tiktok(url)
                if video_data:
                    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                        f.write(video_data)
                        video_path = f.name
            else:
                # YouTube, Instagram, Twitter и всё остальное — через yt-dlp
                tmp_dir = tempfile.mkdtemp()
                loop = asyncio.get_event_loop()
                video_path = await loop.run_in_executor(
                    None, download_with_ytdlp, url, tmp_dir
                )

            if not video_path:
                await update.message.reply_text(f"❌ Не удалось скачать видео {i}")
                continue

            try:
                with open(video_path, "rb") as f:
                    await update.message.reply_video(
                        video=f,
                        supports_streaming=True,
                        write_timeout=120,
                        read_timeout=120,
                    )
                success += 1
                await status_msg.edit_text(f"⏳ Скачиваю {success} из {count} видео...")
            finally:
                os.unlink(video_path)

        except Exception as e:
            err = str(e)
            if "File too large" in err or "max_filesize" in err:
                await update.message.reply_text(f"❌ Видео {i} слишком большое (лимит Telegram — 50 МБ)")
            elif "Private" in err or "login" in err.lower():
                await update.message.reply_text(f"❌ Видео {i} приватное или требует авторизации")
            else:
                await update.message.reply_text(f"❌ Ошибка видео {i}: {err[:200]}")

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
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
