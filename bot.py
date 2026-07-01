import json
import os
import re
import time
import requests
from bs4 import BeautifulSoup, NavigableString
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────
BOT_TOKEN     = os.environ["BOT_TOKEN"]
CHAT_ID       = int(os.environ["CHAT_ID"])
MIMO_API_KEY  = os.environ["MI_API"]

STATE_FILE    = "channels.json"
PROMPT_FILE   = "standardization_prompt.txt"
API_URL       = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_WEB        = "https://t.me/s"
MIMO_API_URL  = "https://api.xiaomimimo.com/v1/chat/completions"
MIMO_MODEL    = "mimo-v2.5-pro"
HEADERS       = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}
CAPTION_LIMIT = 1024

ALLOWED_HASHTAGS = {
    "ии_модели_и_агенты", "ии_инфраструктура", "ии_внедрение",
    "ии_рынок", "ии_регулирование",
    "платформы_экономика_платформ", "платформы_экосистемы",
    "платформы_агентная_коммерция", "платформы_регулирование",
    "ит_сектор", "телеком", "финтех", "кибербез", "кванты",
    "другое",
}
DEFAULT_HASHTAG = "другое"

HELP_TEXT = (
    "📡 <b>Tech Slop Bot</b> — ИИ-агент для мониторинга ИТ-трендов\n\n"
    "<b>Что я умею:</b>\n"
    "1. Мониторю каналы, которые ты добавишь\n"
    "2. Переписываю новости в чистый формат\n"
    "3. Сортирую по тегам\n\n"
    "⏱ Задержка ответа — до 3 минут\n\n"
    "<b>Команды:</b>\n"
    "/add @channel\n"
    "/remove @channel\n"
    "/list\n\n"
    "<b>Код проекта:</b> https://github.com/sunandreas/tech_slop_bot"
)


# ── State ─────────────────────────────────────────────────────────────────────
def load_state() -> dict:
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)

    # Миграция старого формата (channel -> int) в новый
    # (channel -> {"name": str, "last_seen": int}), если нужно.
    channels = state.get("channels", {})
    migrated = {}
    for ch, value in channels.items():
        if isinstance(value, dict):
            migrated[ch] = value
        else:
            migrated[ch] = {"name": f"@{ch}", "last_seen": value}
    state["channels"] = migrated
    return state


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def load_standardization_prompt() -> str:
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        return f.read()


STANDARDIZATION_PROMPT = load_standardization_prompt()


# ── Telegram API ──────────────────────────────────────────────────────────────
def tg(method: str, **params) -> Optional[dict | list]:
    try:
        r = requests.post(f"{API_URL}/{method}", json=params, timeout=10)
        data = r.json()
        if data.get("ok"):
            return data["result"]
        print(f"[TG ERROR] {method}: {data.get('description')}")
    except Exception as e:
        print(f"[REQUEST ERROR] {method}: {e}")
    return None


def get_updates(offset: int) -> list:
    return tg("getUpdates", offset=offset, timeout=0) or []


def send_message(text: str) -> None:
    tg(
        "sendMessage",
        chat_id=CHAT_ID,
        text=text,
        parse_mode="HTML",
        link_preview_options={"is_disabled": True},
    )


def send_photo_file(photo_bytes: bytes, caption: Optional[str]) -> bool:
    """Отправляет одно фото как файл, с подписью если она задана."""
    try:
        data = {"chat_id": CHAT_ID}
        if caption:
            data["caption"]    = caption
            data["parse_mode"] = "HTML"
        r = requests.post(
            f"{API_URL}/sendPhoto",
            data=data,
            files={"photo": ("photo.jpg", photo_bytes, "image/jpeg")},
            timeout=30,
        )
        data_resp = r.json()
        if data_resp.get("ok"):
            return True
        print(f"[TG ERROR] sendPhoto: {data_resp.get('description')}")
    except Exception as e:
        print(f"[REQUEST ERROR] sendPhoto: {e}")
    return False


def send_media_group_files(images_bytes: list[bytes], caption: Optional[str]) -> bool:
    """Отправляет альбом фото как файлы, с подписью на первом если она задана."""
    try:
        files = {}
        media = []
        for i, img in enumerate(images_bytes):
            name = f"attach{i}"
            files[name] = (f"photo{i}.jpg", img, "image/jpeg")
            item = {"type": "photo", "media": f"attach://{name}"}
            if i == 0 and caption:
                item["caption"]    = caption
                item["parse_mode"] = "HTML"
            media.append(item)

        r = requests.post(
            f"{API_URL}/sendMediaGroup",
            data={"chat_id": CHAT_ID, "media": json.dumps(media)},
            files=files,
            timeout=60,
        )
        data = r.json()
        if data.get("ok"):
            return True
        print(f"[TG ERROR] sendMediaGroup: {data.get('description')}")
    except Exception as e:
        print(f"[REQUEST ERROR] sendMediaGroup: {e}")
    return False


# ── HTML parsing ──────────────────────────────────────────────────────────────
def extract_html_text(element) -> str:
    """Рекурсивно извлекает текст с Telegram-совместимым HTML форматированием."""
    result = ""
    for child in element.children:
        if isinstance(child, NavigableString):
            result += str(child)
        elif child.name == "br":
            result += "\n"
        elif child.name in ("b", "strong"):
            result += f"<b>{extract_html_text(child)}</b>"
        elif child.name in ("i", "em"):
            result += f"<i>{extract_html_text(child)}</i>"
        elif child.name == "u":
            result += f"<u>{extract_html_text(child)}</u>"
        elif child.name in ("s", "strike"):
            result += f"<s>{extract_html_text(child)}</s>"
        elif child.name == "code":
            result += f"<code>{extract_html_text(child)}</code>"
        elif child.name == "pre":
            result += f"<pre>{extract_html_text(child)}</pre>"
        elif child.name == "a":
            href = child.get("href", "")
            result += f'<a href="{href}">{extract_html_text(child)}</a>'
        else:
            result += extract_html_text(child)
    return result


def extract_image_url(style: str) -> Optional[str]:
    """Извлекает URL картинки из CSS background-image."""
    match = re.search(r"background-image:url\('(.+?)'\)", style)
    return match.group(1) if match else None


def download_image(url: str) -> Optional[bytes]:
    """Скачивает картинку и возвращает байты."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.content
    except Exception as e:
        print(f"[DOWNLOAD ERROR] {url}: {e}")
    return None


# ── Parsing ───────────────────────────────────────────────────────────────────
def fetch_channel_name(channel: str) -> str:
    """
    Извлекает отображаемое имя канала со страницы t.me/s/channel.
    Если не удалось — возвращает @username как fallback.
    """
    try:
        r = requests.get(f"{TG_WEB}/{channel}", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            title_div = soup.find("div", class_="tgme_channel_info_header_title")
            if title_div:
                name = title_div.get_text(strip=True)
                if name:
                    return name
    except Exception as e:
        print(f"[CHANNEL NAME ERROR] {channel}: {e}")
    return f"@{channel}"


def fetch_posts(channel: str) -> list[dict]:
    """Парсит t.me/s/channel и возвращает список постов с текстом и картинками."""
    try:
        r = requests.get(f"{TG_WEB}/{channel}", headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        posts: dict[int, dict] = {}

        for msg in soup.find_all("div", class_="tgme_widget_message"):
            date_link = msg.find("a", class_="tgme_widget_message_date")
            if not date_link:
                continue
            href = date_link.get("href", "")
            part = href.rstrip("/").split("/")[-1]
            if not part.isdigit():
                continue
            post_id = int(part)

            if post_id not in posts:
                posts[post_id] = {
                    "id":     post_id,
                    "text":   "",
                    "images": [],
                    "url":    f"https://t.me/{channel}/{post_id}",
                }

            # Текст поста
            text_div = msg.find("div", class_="tgme_widget_message_text")
            if text_div and not posts[post_id]["text"]:
                posts[post_id]["text"] = extract_html_text(text_div).strip()

            # Картинки
            for photo in msg.find_all("a", class_="tgme_widget_message_photo_wrap"):
                url = extract_image_url(photo.get("style", ""))
                if url and url not in posts[post_id]["images"]:
                    posts[post_id]["images"].append(url)

        return sorted(posts.values(), key=lambda p: p["id"])
    except Exception as e:
        print(f"[PARSE ERROR] {channel}: {e}")
        return []


# ── Channel-specific text cleanup ────────────────────────────────────────────
CHANNEL_CLEANUPS = {
    "ict_moscow": "trailing_link_paragraph",
}


def clean_post_text(text: str, channel: str) -> str:
    """Применяет channel-специфичную очистку текста перед стандартизацией."""
    rule = CHANNEL_CLEANUPS.get(channel.lower())

    if rule == "trailing_link_paragraph":
        # @ict_moscow всегда заканчивает посты абзацем со старой связанной новостью,
        # помеченным эмодзи 🔗. Убираем последний абзац если он начинается с 🔗.
        paragraphs = text.split("\n\n")
        while paragraphs:
            last = paragraphs[-1].strip()
            if last.startswith("🔗"):
                paragraphs.pop()
            else:
                break
        text = "\n\n".join(paragraphs).strip()

    return text



def strip_json_fence(content: str) -> str:
    """Убирает обёртку ```json ... ``` если модель её добавила."""
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```json?\s*|\s*```$", "", content, flags=re.MULTILINE).strip()
    return content


def standardize_post(text: str, state: dict) -> list[dict]:
    """
    Прогоняет сырой текст поста через MiMo и возвращает список
    стандартизированных новостей вида {"headline": str, "bullets": [str]}.
    При временных сбоях (сеть, невалидный ответ) делает одну повторную
    попытку с паузой. При окончательной ошибке — fallback на исходный
    текст без обработки, чтобы не терять контент. При первой ошибке за
    сессию шлёт разовое предупреждение пользователю.
    """
    if not text.strip():
        return []

    fallback = [{"headline": text, "bullets": [], "hashtag": DEFAULT_HASHTAG}]

    def report_failure(reason: str) -> None:
        print(f"[MIMO ERROR] {reason}")
        if not state.get("mimo_alert_sent"):
            send_message(
                f"⚠️ Стандартизация новостей не работает: {reason}\n"
                f"Посты приходят без обработки, пока проблема не устранена."
            )
            state["mimo_alert_sent"] = True

    for attempt in range(2):
        is_last_attempt = attempt == 1
        try:
            r = requests.post(
                MIMO_API_URL,
                headers={
                    "Authorization": f"Bearer {MIMO_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MIMO_MODEL,
                    "messages": [
                        {"role": "system", "content": STANDARDIZATION_PROMPT},
                        {"role": "user", "content": text},
                    ],
                },
                timeout=180,
            )

            try:
                data = r.json()
            except ValueError:
                if not is_last_attempt:
                    time.sleep(3)
                    continue
                snippet = r.text[:200].replace("\n", " ")
                report_failure(f"невалидный JSON в ответе (HTTP {r.status_code}): {snippet!r}")
                return fallback

            if "error" in data:
                # Ошибка от самого API (баланс, ключ и т.п.) — повтор не поможет
                report_failure(str(data["error"]))
                return fallback

            content = data["choices"][0]["message"]["content"]
            content = strip_json_fence(content)
            items   = json.loads(content)

            if not isinstance(items, list):
                report_failure("некорректный формат ответа")
                return fallback

            # Валидация структуры каждого элемента
            for item in items:
                if "headline" not in item:
                    report_failure("элемент без headline в ответе")
                    return fallback
                item.setdefault("bullets", [])

                tag = str(item.get("hashtag", "")).strip().lstrip("#")
                if tag not in ALLOWED_HASHTAGS:
                    print(f"[MIMO WARNING] Неизвестный/отсутствующий hashtag '{tag}', заменён на '{DEFAULT_HASHTAG}'")
                    tag = DEFAULT_HASHTAG
                item["hashtag"] = tag

            # Успешный вызов — сбрасываем флаг, чтобы при повторном сбое
            # пользователь снова получил уведомление
            state["mimo_alert_sent"] = False
            return items

        except Exception as e:
            if not is_last_attempt:
                time.sleep(3)
                continue
            report_failure(f"ошибка запроса ({e})")
            return fallback

    return fallback


SENTENCE_ENDERS = (".", "!", "?", "…")


def ensure_period(text: str) -> str:
    """Гарантирует точку (или другой завершающий знак) в конце текста."""
    text = text.rstrip()
    if text and not text.endswith(SENTENCE_ENDERS):
        text += "."
    return text


def format_standardized_item(item: dict) -> str:
    """
    Собирает headline (жирным) + bullets в готовый текст.
    Headline и каждый bullet гарантированно заканчиваются точкой
    (страховка на случай если модель её не поставила).
    Если bullets ровно один — это не самостоятельный список, дописываем
    его как продолжение headline одним предложением. Список (дефисом)
    рисуем только при 2+ буллитах. Hashtag и ссылка добавляются отдельно
    в send_post, после этого текста.
    """
    headline = ensure_period(item["headline"])
    bullets  = [ensure_period(b) for b in item["bullets"]]

    if len(bullets) == 1:
        return f"<b>{headline}</b> {bullets[0]}"

    text = f"<b>{headline}</b>"
    if bullets:
        text += "\n" + "\n".join(f"- {b}" for b in bullets)
    return text


# ── Sending posts ─────────────────────────────────────────────────────────────
def send_post(post: dict, channel: str, channel_name: str, state: dict) -> bool:
    footer = f'\n\n🔗 <a href="{post["url"]}">публикация {channel_name}</a>'

    # Нет ни текста ни картинок — видео или документ
    if not post["text"] and not post["images"]:
        return tg(
            "sendMessage",
            chat_id=CHAT_ID,
            text=f"📎 Медиафайл (видео/документ){footer}",
            parse_mode="HTML",
            link_preview_options={"is_disabled": True},
        ) is not None

    items = standardize_post(clean_post_text(post["text"], channel), state) if post["text"] else [{"headline": "", "bullets": [], "hashtag": DEFAULT_HASHTAG}]
    images_bytes = [b for url in post["images"] if (b := download_image(url)) is not None]

    ok = True
    for i, item in enumerate(items):
        hashtag = item.get("hashtag", DEFAULT_HASHTAG)
        body    = format_standardized_item(item)
        full_text = body + footer + f"\n\n#{hashtag}"

        # Картинки прикладываем только к первому сообщению из пакета
        if i == 0 and images_bytes:
            fits_caption = len(full_text) <= CAPTION_LIMIT
            caption = full_text if fits_caption else None

            if len(images_bytes) == 1:
                sent = send_photo_file(images_bytes[0], caption)
            else:
                sent = send_media_group_files(images_bytes, caption)
            ok &= sent

            # Текст не влез в подпись — фото уходит без подписи,
            # весь текст с футером и хэштегом отправляем отдельным
            # сообщением (без дублирования футера в подписи).
            if not fits_caption:
                ok &= tg(
                    "sendMessage",
                    chat_id=CHAT_ID,
                    text=full_text,
                    parse_mode="HTML",
                    link_preview_options={"is_disabled": True},
                ) is not None
        else:
            ok &= tg(
                "sendMessage",
                chat_id=CHAT_ID,
                text=full_text,
                parse_mode="HTML",
                link_preview_options={"is_disabled": True},
            ) is not None

    return ok


# ── Commands ──────────────────────────────────────────────────────────────────
def cmd_add(channel: str, state: dict) -> None:
    if channel in state["channels"]:
        send_message(f"Канал @{channel} уже добавлен")
        return
    posts = fetch_posts(channel)
    if not posts:
        send_message(f"❌ Канал @{channel} не найден или недоступен")
        return
    name = fetch_channel_name(channel)
    state["channels"][channel] = {"name": name, "last_seen": posts[-1]["id"]}
    send_message(f"✅ Канал «{name}» (@{channel}) добавлен")


def cmd_remove(channel: str, state: dict) -> None:
    if channel not in state["channels"]:
        send_message(f"Канал @{channel} не в списке")
        return
    del state["channels"][channel]
    send_message(f"🗑 Канал @{channel} удалён")


def cmd_list(state: dict) -> None:
    channels = state.get("channels", {})
    if not channels:
        send_message("Список пуст. Добавь канал через /add @channel")
        return
    items = "\n".join(f"• {info['name']} (@{ch})" for ch, info in channels.items())
    send_message(f"📋 <b>Твои каналы:</b>\n\n{items}")


def process_commands(state: dict) -> None:
    """Читает команды от пользователя и обрабатывает их."""
    updates = get_updates(state.get("offset", 0))
    for update in updates:
        state["offset"] = update["update_id"] + 1
        msg = update.get("message", {})
        if msg.get("chat", {}).get("id") != CHAT_ID:
            continue
        text = msg.get("text", "").strip()
        if not text.startswith("/"):
            continue
        parts = text.split()
        cmd = parts[0].lower()
        arg = parts[1].lstrip("@").lower() if len(parts) > 1 else ""

        if cmd in ("/start", "/help"):
            send_message(HELP_TEXT)
        elif cmd == "/add":
            if arg:
                cmd_add(arg, state)
            else:
                send_message("Использование: /add @channel")
        elif cmd == "/remove":
            if arg:
                cmd_remove(arg, state)
            else:
                send_message("Использование: /remove @channel")
        elif cmd == "/list":
            cmd_list(state)


# ── Forwarding ────────────────────────────────────────────────────────────────
def process_channels(state: dict) -> None:
    """Для каждого канала находит новые посты и отправляет их."""
    for channel, info in list(state["channels"].items()):
        # Бэкфилл имени для каналов, мигрированных со старого формата
        if info["name"] == f"@{channel}":
            real_name = fetch_channel_name(channel)
            if real_name != f"@{channel}":
                info["name"] = real_name

        last_seen = info["last_seen"]
        posts     = fetch_posts(channel)
        new_posts = [p for p in posts if p["id"] > last_seen]

        if not new_posts:
            continue

        print(f"[{channel}] новых постов: {len(new_posts)}")

        for post in new_posts:
            if send_post(post, channel, info["name"], state):
                info["last_seen"] = post["id"]
            else:
                print(f"[SEND ERROR] {channel}/{post['id']}")


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    state = load_state()

    # Защита от двойных запусков — если предыдущий прогон завершился
    # менее 60 секунд назад, пропускаем этот запуск.
    # Это страховка на случай если cron-job.org прислал двойной триггер
    # или concurrency в bot.yml не сработал идеально.
    now = int(time.time())
    last_run = state.get("last_run", 0)
    if now - last_run < 60:
        print(f"[SKIP] Предыдущий прогон завершился {now - last_run}с назад, пропускаем")
        return

    process_commands(state)
    process_channels(state)

    state["last_run"] = int(time.time())
    save_state(state)


if __name__ == "__main__":
    main()
