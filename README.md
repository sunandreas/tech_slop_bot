# 📡 Tech Slop Bot

Личный агрегатор новостей из Telegram каналов. Бот парсит публичные каналы и пересылает новые посты тебе в личку.

## Как это работает

```
cron-job.org (каждую минуту)
        ↓
GitHub Actions запускает bot.py
        ↓
Парсит t.me/s/channel для каждого канала
        ↓
Отправляет новые посты в Telegram
        ↓
Сохраняет состояние в channels.json
```

## Управление через бота

| Команда | Действие |
|---------|----------|
| `/add @channel` | Добавить канал |
| `/remove @channel` | Удалить канал |
| `/list` | Список каналов |
| `/help` | Помощь |

## Что пересылается

- Текст с форматированием (жирный, курсив, ссылки)
- Фото (одно или альбом)
- Посты с видео/документами — приходят с ссылкой на оригинал

## Ограничения

- Каналы должны быть **публичными** и иметь веб-превью (`t.me/s/channel`)
- Каналы с отключённым превью не поддерживаются
- Картинки приходят в сжатом качестве (ограничение веб-версии Telegram)
- Видео и документы не пересылаются — только уведомление со ссылкой

## Настройка

### 1. GitHub Secrets

| Секрет | Описание |
|--------|----------|
| `BOT_TOKEN` | Токен бота от @BotFather |
| `CHAT_ID` | Твой Telegram ID (узнать у @userinfobot) |
| `GH_PAT` | GitHub Fine-grained token с правом Contents: Read and Write |

### 2. cron-job.org

Запускает бота каждую минуту через GitHub API:

- **URL:** `https://api.github.com/repos/USERNAME/REPO/actions/workflows/bot.yml/dispatches`
- **Метод:** POST
- **Заголовки:**
  - `Authorization: Bearer ТОКЕН` (Fine-grained token с правом Actions: Write)
  - `Accept: application/vnd.github.v3+json`
  - `Content-Type: application/json`
- **Тело:** `{"ref":"main"}`

### 3. channels.json

Хранит список каналов и ID последнего виденного поста:

```json
{
  "offset": 0,
  "channels": {
    "bbcrussian": 1234,
    "meduzaio": 5678
  }
}
```

## Структура проекта

```
├── .github/
│   └── workflows/
│       └── bot.yml       # GitHub Actions workflow
├── bot.py                # Основной скрипт
├── channels.json         # Состояние (каналы + last_seen ID)
├── requirements.txt      # Зависимости
└── README.md
```

## Зависимости

```
requests
beautifulsoup4
```
