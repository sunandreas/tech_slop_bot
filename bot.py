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
LABELS_FILE   = "labels.json"
CACHE_FILE    = "msg_cache.json"

API_URL       = f"https://api.telegram.org/bot{BOT_TOKEN}"
TG_WEB        = "https://t.me/s"
MIMO_API_URL  = "https://api.xiaomimimo.com/v1/chat/completions"
MIMO_MODEL    = "mimo-v2.5-pro"
HEADERS       = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}

CAPTION_LIMIT   = 1024
CACHE_MAX       = 2000
SENTENCE_ENDERS = (".", "!", "?", "…")

ALLOWED_HASHTAGS = {
    "ии_модели_и_агенты", "ии_инфраструктура", "ии_внедрение",
    "ии_рынок", "ии_регулирование",
    "платформы_экономика_платформ", "платформы_экосистемы",
    "платформы_агентная_коммерция", "платформы_регулирование",
    "ит_сектор", "телеком", "финтех", "кибербез", "кванты",
    "другое",
}
DEFAULT_HASHTAG = "другое"

CUSTOM_EMOJI_MAP = {
    "5433757954476091117": "🔴",
    "5434100306319256071": "🟢",
    "5434047113149296288": "🟡",
}



CHANNEL_CLEANUPS = {
    "ict_moscow": "trailing_link_paragraph",
}

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


# ── Persistence ───────────────────────────────────────────────────────────────
def load_state() -> dict:
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
    # Миграция плоского формата {channel: int} → {channel: {name, last_seen}}
    state["channels"] = {
        ch: (v if isinstance(v, dict) else {"name": f"@{ch}", "last_seen": v})
        for ch, v in state.get("channels", {}).items()
    }
    return state


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_labels() -> list:  return _load_json(LABELS_FILE, [])
def load_cache()  -> dict:  return _load_json(CACHE_FILE,  {})


def save_labels(labels: list) -> None:
    with open(LABELS_FILE, "w", encoding="utf-8") as f:
        json.dump(labels, f, indent=2, ensure_ascii=False)


def save_cache(cache: dict) -> None:
    if len(cache) > CACHE_MAX:
        for k in sorted(cache, key=lambda k: int(k) if k.isdigit() else 0)[:len(cache) - CACHE_MAX]:
            del cache[k]
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


with open(PROMPT_FILE, encoding="utf-8") as _f:
    STANDARDIZATION_PROMPT = _f.read()


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
    return tg("getUpdates", offset=offset, timeout=0,
              allowed_updates=["message", "callback_query", "message_reaction"]) or []


def send_message(text: str) -> None:
    tg("sendMessage", chat_id=CHAT_ID, text=text,
       parse_mode="HTML", link_preview_options={"is_disabled": True})


def send_text_news(text: str, cache: dict) -> bool:
    result = tg("sendMessage", chat_id=CHAT_ID, text=text,
                parse_mode="HTML", link_preview_options={"is_disabled": True})
    if result and isinstance(result, dict) and (mid := str(result.get("message_id", ""))):
        cache[mid] = text
    return result is not None


def send_photo_file(photo_bytes: bytes, caption: Optional[str]) -> bool:
    try:
        data = {"chat_id": CHAT_ID}
        if caption:
            data.update({"caption": caption, "parse_mode": "HTML"})
        r = requests.post(f"{API_URL}/sendPhoto", data=data,
                          files={"photo": ("photo.jpg", photo_bytes, "image/jpeg")}, timeout=30)
        resp = r.json()
        if resp.get("ok"):
            return True
        print(f"[TG ERROR] sendPhoto: {resp.get('description')}")
    except Exception as e:
        print(f"[REQUEST ERROR] sendPhoto: {e}")
    return False


def send_media_group_files(images_bytes: list[bytes], caption: Optional[str]) -> bool:
    try:
        files, media = {}, []
        for i, img in enumerate(images_bytes):
            files[f"attach{i}"] = (f"photo{i}.jpg", img, "image/jpeg")
            item = {"type": "photo", "media": f"attach://attach{i}"}
            if i == 0 and caption:
                item.update({"caption": caption, "parse_mode": "HTML"})
            media.append(item)
        r = requests.post(f"{API_URL}/sendMediaGroup",
                          data={"chat_id": CHAT_ID, "media": json.dumps(media)},
                          files=files, timeout=60)
        resp = r.json()
        if resp.get("ok"):
            return True
        print(f"[TG ERROR] sendMediaGroup: {resp.get('description')}")
    except Exception as e:
        print(f"[REQUEST ERROR] sendMediaGroup: {e}")
    return False


# ── HTML parsing ──────────────────────────────────────────────────────────────
def extract_html_text(element) -> str:
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
            result += f'<a href="{child.get("href", "")}">{extract_html_text(child)}</a>'
        else:
            result += extract_html_text(child)
    return result


def extract_image_url(style: str) -> Optional[str]:
    m = re.search(r"background-image:url\('(.+?)'\)", style)
    return m.group(1) if m else None


def download_image(url: str) -> Optional[bytes]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.content
    except Exception as e:
        print(f"[DOWNLOAD ERROR] {url}: {e}")
    return None


# ── Channel-specific cleanup ──────────────────────────────────────────────────
def clean_post_text(text: str, channel: str) -> str:
    if CHANNEL_CLEANUPS.get(channel.lower()) == "trailing_link_paragraph":
        paras = text.split("\n\n")
        while paras and paras[-1].strip().startswith("🔗"):
            paras.pop()
        text = "\n\n".join(paras).strip()
    return text


# ── Parsing ───────────────────────────────────────────────────────────────────
def fetch_channel_name(channel: str) -> str:
    try:
        r = requests.get(f"{TG_WEB}/{channel}", headers=HEADERS, timeout=10)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            div = soup.find("div", class_="tgme_channel_info_header_title")
            if div and (name := div.get_text(strip=True)):
                return name
    except Exception as e:
        print(f"[CHANNEL NAME ERROR] {channel}: {e}")
    return f"@{channel}"


def fetch_posts(channel: str) -> list[dict]:
    try:
        r = requests.get(f"{TG_WEB}/{channel}", headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return []
        posts: dict[int, dict] = {}
        for msg in BeautifulSoup(r.text, "html.parser").find_all("div", class_="tgme_widget_message"):
            link = msg.find("a", class_="tgme_widget_message_date")
            if not link:
                continue
            part = link.get("href", "").rstrip("/").split("/")[-1]
            if not part.isdigit():
                continue
            pid = int(part)
            if pid not in posts:
                posts[pid] = {"id": pid, "text": "", "images": [],
                              "url": f"https://t.me/{channel}/{pid}"}
            text_div = msg.find("div", class_="tgme_widget_message_text")
            if text_div and not posts[pid]["text"]:
                posts[pid]["text"] = extract_html_text(text_div).strip()
            for photo in msg.find_all("a", class_="tgme_widget_message_photo_wrap"):
                if url := extract_image_url(photo.get("style", "")):
                    if url not in posts[pid]["images"]:
                        posts[pid]["images"].append(url)
        return sorted(posts.values(), key=lambda p: p["id"])
    except Exception as e:
        print(f"[PARSE ERROR] {channel}: {e}")
        return []


# ── Standardization (MiMo) ───────────────────────────────────────────────────
def standardize_post(text: str, state: dict) -> list[dict]:
    if not text.strip():
        return []

    fallback = [{"headline": text, "bullets": [], "hashtag": DEFAULT_HASHTAG}]

    def report(reason: str) -> None:
        print(f"[MIMO ERROR] {reason}")
        if not state.get("mimo_alert_sent"):
            send_message(f"⚠️ Стандартизация не работает: {reason}\n"
                         "Посты приходят без обработки.")
            state["mimo_alert_sent"] = True

    for attempt in range(2):
        try:
            r = requests.post(
                MIMO_API_URL,
                headers={"Authorization": f"Bearer {MIMO_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": MIMO_MODEL, "messages": [
                    {"role": "system", "content": STANDARDIZATION_PROMPT},
                    {"role": "user",   "content": text},
                ]},
                timeout=180,
            )
            try:
                data = r.json()
            except ValueError:
                if attempt == 0:
                    time.sleep(3)
                    continue
                report(f"невалидный JSON (HTTP {r.status_code}): {r.text[:200]!r}")
                return fallback

            if "error" in data:
                report(str(data["error"]))
                return fallback

            raw = data["choices"][0]["message"]["content"].strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```json?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
            items = json.loads(raw)

            if not isinstance(items, list):
                report("некорректный формат ответа")
                return fallback

            for item in items:
                if "headline" not in item:
                    report("элемент без headline")
                    return fallback
                item.setdefault("bullets", [])
                tag = str(item.get("hashtag", "")).strip().lstrip("#")
                if tag not in ALLOWED_HASHTAGS:
                    print(f"[MIMO WARNING] hashtag '{tag}' → '{DEFAULT_HASHTAG}'")
                    tag = DEFAULT_HASHTAG
                item["hashtag"] = tag

            state["mimo_alert_sent"] = False
            return items

        except Exception as e:
            if attempt == 0:
                time.sleep(3)
                continue
            report(f"ошибка запроса ({e})")
            return fallback

    return fallback


# ── Formatting ────────────────────────────────────────────────────────────────
def ensure_period(text: str) -> str:
    text = text.rstrip()
    return text + "." if text and not text.endswith(SENTENCE_ENDERS) else text


def format_item(item: dict) -> str:
    headline = ensure_period(item["headline"])
    bullets  = [ensure_period(b) for b in item["bullets"]]
    if len(bullets) == 1:
        return f"<b>{headline}</b> {bullets[0]}"
    text = f"<b>{headline}</b>"
    if bullets:
        text += "\n" + "\n".join(f"- {b}" for b in bullets)
    return text


# ── Sending posts ─────────────────────────────────────────────────────────────
def send_post(post: dict, channel: str, channel_name: str, state: dict, cache: dict) -> bool:
    footer = f'\n\n🔗 <a href="{post["url"]}">публикация {channel_name}</a>'

    if not post["text"] and not post["images"]:
        return tg("sendMessage", chat_id=CHAT_ID,
                  text=f"📎 Медиафайл (видео/документ){footer}",
                  parse_mode="HTML", link_preview_options={"is_disabled": True}) is not None

    raw_text = clean_post_text(post["text"], channel) if post["text"] else ""
    items = standardize_post(raw_text, state) if raw_text else [
        {"headline": "", "bullets": [], "hashtag": DEFAULT_HASHTAG}
    ]
    images = [b for url in post["images"] if (b := download_image(url)) is not None]

    ok = True
    for i, item in enumerate(items):
        full_text = format_item(item) + footer + f"\n\n#{item.get('hashtag', DEFAULT_HASHTAG)}"

        if i == 0 and images:
            fits = len(full_text) <= CAPTION_LIMIT
            caption = full_text if fits else None
            ok &= (send_photo_file(images[0], caption) if len(images) == 1
                   else send_media_group_files(images, caption))
            if not fits:
                ok &= send_text_news(full_text, cache)
        else:
            ok &= send_text_news(full_text, cache)

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
    send_message("📋 <b>Твои каналы:</b>\n\n" +
                 "\n".join(f"• {info['name']} (@{ch})" for ch, info in channels.items()))


def handle_reaction(update: dict, cache: dict) -> None:
    try:
        mr = update.get("message_reaction", {})
        new_reactions = mr.get("new_reaction", [])
        if not new_reactions:
            return

        msg_id   = str(mr.get("message_id", ""))
        reaction = new_reactions[0]
        rtype    = reaction.get("type", "")

        if rtype == "emoji":
            emoji = reaction.get("emoji", "")
        elif rtype == "custom_emoji":
            raw_id = reaction.get("custom_emoji_id", "")
            emoji  = CUSTOM_EMOJI_MAP.get(raw_id, raw_id)
        else:
            return

        labels = load_labels()
        now    = int(time.time())

        # Ищем существующую запись по msg_id — обновляем если нашли.
        # Это корректно обрабатывает смену реакции: 🟡 → 🟢 не даст дубля в базе.
        for entry in labels:
            if entry.get("msg_id") == msg_id:
                entry["reaction"]  = emoji
                entry["timestamp"] = now
                save_labels(labels)
                print(f"[REACTION] обновлена → {emoji!r} на msg_id={msg_id}")
                return

        labels.append({"text": cache.get(msg_id, ""), "reaction": emoji,
                        "msg_id": msg_id, "timestamp": now})
        save_labels(labels)
        print(f"[REACTION] {emoji!r} на msg_id={msg_id}, всего: {len(labels)}")

    except Exception as e:
        print(f"[REACTION ERROR] {e}")


def process_commands(state: dict, cache: dict) -> None:
    for update in get_updates(state.get("offset", 0)):
        state["offset"] = update["update_id"] + 1

        if "message_reaction" in update:
            handle_reaction(update, cache)
            continue

        msg = update.get("message", {})
        if msg.get("chat", {}).get("id") != CHAT_ID:
            continue
        text = msg.get("text", "").strip()
        if not text.startswith("/"):
            continue

        parts = text.split()
        cmd   = parts[0].lower()
        arg   = parts[1].lstrip("@").lower() if len(parts) > 1 else ""

        if cmd in ("/start", "/help"):
            send_message(HELP_TEXT)
        elif cmd == "/add":
            cmd_add(arg, state) if arg else send_message("Использование: /add @channel")
        elif cmd == "/remove":
            cmd_remove(arg, state) if arg else send_message("Использование: /remove @channel")
        elif cmd == "/list":
            cmd_list(state)


# ── Forwarding ────────────────────────────────────────────────────────────────
def process_channels(state: dict, cache: dict) -> None:
    for channel, info in list(state["channels"].items()):
        if info["name"] == f"@{channel}":
            if (real := fetch_channel_name(channel)) != f"@{channel}":
                info["name"] = real

        new_posts = [p for p in fetch_posts(channel) if p["id"] > info["last_seen"]]
        if not new_posts:
            continue

        print(f"[{channel}] новых постов: {len(new_posts)}")
        for post in new_posts:
            if send_post(post, channel, info["name"], state, cache):
                info["last_seen"] = post["id"]
            else:
                print(f"[SEND ERROR] {channel}/{post['id']}")

        save_state(state)  # после каждого канала — чтобы не переобработать при падении


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    state = load_state()

    now = int(time.time())
    if now - state.get("last_run", 0) < 60:
        print(f"[SKIP] Предыдущий прогон завершился {now - state.get('last_run', 0)}с назад")
        return

    cache = load_cache()
    process_commands(state, cache)
    process_channels(state, cache)
    save_cache(cache)

    state["last_run"] = int(time.time())
    save_state(state)


if __name__ == "__main__":
    main()
