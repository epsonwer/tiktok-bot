# TikTok Downloader Bot 🎬

Telegram-бот, который скачивает TikTok видео без водяных знаков.

## Установка

### 1. Установи зависимости

```bash
pip install -r requirements.txt
```

### 2. Получи токен бота

1. Открой Telegram и найди [@BotFather](https://t.me/BotFather)
2. Напиши `/newbot` и следуй инструкциям
3. Скопируй полученный токен (выглядит как `1234567890:ABCdef...`)

### 3. Вставь токен

Открой `bot.py` и замени в строке:
```python
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВСТАВЬ_СВОЙ_TOKEN_СЮДА")
```
...или задай переменную окружения:
```bash
export BOT_TOKEN="твой_токен_здесь"
```

### 4. Запусти бота

```bash
python bot.py
```

## Использование

Просто отправь боту одну или несколько ссылок на TikTok — можно всё в одном сообщении:

```
https://www.tiktok.com/@user/video/123456789
https://vm.tiktok.com/ABC123/
```

Бот скачает каждое видео и отправит без водяного знака.

## Запуск 24/7 (опционально)

Чтобы бот работал постоянно, можно запустить его на сервере через `screen` или `systemd`:

```bash
screen -S tiktok_bot
python bot.py
# Ctrl+A, затем D — чтобы свернуть
```

Или бесплатно через [Railway.app](https://railway.app) / [Render.com](https://render.com).
