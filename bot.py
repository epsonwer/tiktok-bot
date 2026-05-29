import re
import os
import tempfile
import httpx
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8888017850:AAGVphq-pYKa4w_OR1TNaUGJX5H5ARpEefg")


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

    # Приоритет: hdplay (оригинальное HD) → play (без вотермарки) → wmplay (с вотермаркой)
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


# Накапливаем ссылки от пользователя в течение короткого времени
pending: dict[int, list[str]] = {}
pending_tasks: dict[int, object] = {}


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
            video_data = await download_tiktok(url)
            if not video_data:
                await update.message.reply_text(f"❌ Не удалось скачать видео {i}")
                continue

            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
                f.write(video_data)
                tmp_path = f.name

            try:
                with open(tmp_path, "rb") as f:
                    await update.message.reply_video(
                        video=f,
                        supports_streaming=True,
                        write_timeout=120,
                        read_timeout=120,
                    )
                success += 1
                await status_msg.edit_text(f"⏳ Скачиваю {success} из {count} видео...")
            finally:
                os.unlink(tmp_path)

        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка при обработке видео {i}: {e}")

    await status_msg.edit_text(f"✅ Готово! Скачано {success} из {count} видео.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    found = re.findall(
        r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/\S+",
        text,
        re.IGNORECASE
    )

    if not found:
        await update.message.reply_text(
            "Отправь мне ссылку(и) на TikTok, и я скачаю видео без водяных знаков 🎬"
        )
        return

    user_id = update.effective_user.id

    # Накапливаем ссылки
    if user_id not in pending:
        pending[user_id] = []
    pending[user_id].extend(found)

    # Отменяем предыдущий отложенный запуск если он есть
    if user_id in pending_tasks:
        pending_tasks[user_id].cancel()

    # Запускаем обработку через 1.5 секунды — вдруг пользователь ещё скидывает ссылки
    loop = context.application.update_queue._loop if hasattr(context.application.update_queue, '_loop') else __import__('asyncio').get_event_loop()

    import asyncio

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
