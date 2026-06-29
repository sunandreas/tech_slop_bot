import json
import os
import requests
from bs4 import BeautifulSoup
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────
BOT_TOKEN  = os.environ["BOT_TOKEN"]
CHAT_ID    = int(os.environ["CHAT_ID"])
STATE_FILE = "channels.json"
API_URL    = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_WEB     = "https://t.me/s"
HEADERS    = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}

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
        r = requests.get(f"{API_URL}/{method}", params=params, timeout=10)
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


def forward_message(channel: str, message_id: int) -> bool:
    result = tg(
        "forwardMessage",
        chat_id=CHAT_ID,
        from_chat_id=f"@{channel}",
        message_id=message_id,
    )
    return result is not None


# ── Parsing ───────────────────────────────────────────────────────────────────
def fetch_post_ids(channel: str) -> list[int]:
    """Парсит t.me/s/channel и возвращает отсортированный список ID постов."""
    try:
        r = requests.get(f"{TG_WEB}/{channel}", headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        ids = []
        for a in soup.find_all("a", class_="tgme_widget_message_date"):
            href = a.get("href", "")
            part = href.rstrip("/").split("/")[-1]
            if part.isdigit():
                ids.append(int(part))
        return sorted(set(ids))
    except Exception as e:
        print(f"[PARSE ERROR] {channel}: {e}")
        return []


def channel_exists(channel: str) -> bool:
    """Проверяет что канал существует и публичный."""
    try:
        r = requests.get(f"{TG_WEB}/{channel}", headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return False
        soup = BeautifulSoup(r.text, "html.parser")
        return soup.find("div", class_="tgme_channel_info") is not None
    except Exception:
        return False


# ── Commands ──────────────────────────────────────────────────────────────────
def cmd_add(channel: str, state: dict) -> None:
    if channel in state["channels"]:
        send_message(f"Канал @{channel} уже добавлен")
        return
    if not channel_exists(channel):
        send_message(f"❌ Канал @{channel} не найден или недоступен")
        return
    ids = fetch_post_ids(channel)
    state["channels"][channel] = max(ids) if ids else 0
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
        cmd  = parts[0].lower()
        arg  = parts[1].lstrip("@").lower() if len(parts) > 1 else ""

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
    """Для каждого канала находит новые посты и пересылает их."""
    for channel, last_seen in list(state["channels"].items()):
        ids     = fetch_post_ids(channel)
        new_ids = [pid for pid in ids if pid > last_seen]

        if not new_ids:
            continue

        print(f"[{channel}] новых постов: {len(new_ids)}")

        for pid in new_ids:
            if forward_message(channel, pid):
                state["channels"][channel] = pid
            else:
                print(f"[FORWARD ERROR] {channel}/{pid}")


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    state = load_state()
    process_commands(state)
    process_channels(state)
    save_state(state)


if __name__ == "__main__":
    main()
