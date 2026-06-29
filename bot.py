import json
import os
import re
import requests
from bs4 import BeautifulSoup, NavigableString
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ["BOT_TOKEN"]
CHAT_ID    = int(os.environ["CHAT_ID"])
STATE_FILE = "channels.json"
API_URL    = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_WEB     = "https://t.me/s"
HEADERS    = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}
CAPTION_LIMIT = 1024

HELP_TEXT = (
    "👋 <b>NewsBot</b> — агрегатор Telegram каналов\n\n"
    "/add @channel — добавить канал\n"
    "/remove @channel — удалить канал\n"
    "/list — список каналов"
)


# ── State ─────────────────────────────────────────────────────────────────────
def load_state() -> dict:
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


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
    tg("sendMessage", chat_id=CHAT_ID, text=text, parse_mode="HTML")


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


# ── Parsing ───────────────────────────────────────────────────────────────────
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


# ── Sending posts ─────────────────────────────────────────────────────────────
def send_post(post: dict, channel: str) -> bool:
    footer = f'\n\n<a href="{post["url"]}">© @{channel} | открыть пост</a>'
    text   = post["text"] + footer
    images = post["images"]

    # Только текст
    if not images:
        return tg("sendMessage", chat_id=CHAT_ID, text=text, parse_mode="HTML") is not None

    # Одна картинка
    if len(images) == 1:
        if len(text) <= CAPTION_LIMIT:
            return tg("sendPhoto", chat_id=CHAT_ID, photo=images[0],
                      caption=text, parse_mode="HTML") is not None
        # Текст длиннее лимита — отправляем раздельно
        tg("sendPhoto", chat_id=CHAT_ID, photo=images[0])
        return tg("sendMessage", chat_id=CHAT_ID, text=text, parse_mode="HTML") is not None

    # Несколько картинок — медиагруппа
    caption = text if len(text) <= CAPTION_LIMIT else footer
    media = [{"type": "photo", "media": url} for url in images]
    media[0]["caption"]    = caption
    media[0]["parse_mode"] = "HTML"
    ok = tg("sendMediaGroup", chat_id=CHAT_ID, media=media) is not None

    # Если текст не влез в caption — шлём отдельным сообщением
    if len(text) > CAPTION_LIMIT:
        tg("sendMessage", chat_id=CHAT_ID, text=text, parse_mode="HTML")

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
    state["channels"][channel] = posts[-1]["id"]
    send_message(f"✅ Канал @{channel} добавлен")


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
    items = "\n".join(f"• @{ch}" for ch in channels)
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
    for channel, last_seen in list(state["channels"].items()):
        posts     = fetch_posts(channel)
        new_posts = [p for p in posts if p["id"] > last_seen]

        if not new_posts:
            continue

        print(f"[{channel}] новых постов: {len(new_posts)}")

        for post in new_posts:
            if send_post(post, channel):
                state["channels"][channel] = post["id"]
            else:
                print(f"[SEND ERROR] {channel}/{post['id']}")


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    state = load_state()
    process_commands(state)
    process_channels(state)
    save_state(state)


if __name__ == "__main__":
    main()
