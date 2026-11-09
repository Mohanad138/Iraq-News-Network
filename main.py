# -*- coding: utf-8 -*-
import os, re, json, time, asyncio, subprocess, difflib
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage)
import requests

TG_API_ID = int(os.getenv("TG_API_ID"))
TG_API_HASH = os.getenv("TG_API_HASH")
TG_SESSION = os.getenv("TG_SESSION")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# إعدادات فيسبوك
FB_TOKEN = os.getenv("FB_TOKEN")
PAGE_ID_KIRKUK = os.getenv("PAGE_ID_KIRKUK")
PAGE_ID_STUDENT = os.getenv("PAGE_ID_STUDENT")
PAGE_ID_IRAQ = os.getenv("PAGE_ID_IRAQ")

GROQ_MODEL = "llama-3.3-70b-versatile"
GRAPH = "https://graph.facebook.com/v21.0"
STATE_FILE = "relay_state.json"
os.makedirs("downloads", exist_ok=True)

CHANNELS = {
    -1001565832101: {
        "name": "كركوك الآن",
        "target": "@KirkukNow24H",
        "mode": "rephrase_ar",
        "source_username": "@kirkukdiary",
        "add_link": True,
        "link": "https://t.me/KirkukNow24H",
        "fb_page_id": PAGE_ID_KIRKUK,
    },
    -1009001073232: {
        "name": "العراق الآن",
        "target": "@IQNowNews",
        "mode": "rephrase_ar", # تم التغيير إلى إعادة صياغة
        "source_username": "@RN24_IQ",
        "add_link": True,
        "link": "https://t.me/IQNowNews",
        "fb_page_id": PAGE_ID_IRAQ,
    },
    -1001530089992: {
        "name": "مكتبة الطالب العراقي",
        "target": "@TalibLib",
        "mode": "rephrase_ar", # تم التغيير إلى إعادة صياغة
        "source_username": "@v999v", # المصدر الجديد
        "add_link": True,
        "link": "https://t.me/TalibLib",
        "fb_page_id": PAGE_ID_STUDENT,
    },
}

client = TelegramClient(StringSession(TG_SESSION), TG_API_ID, TG_API_HASH)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: return {}
    return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f: json.dump(state, f, ensure_ascii=False, indent=2)

state = load_state()

def is_duplicate_content(source_id, text):
    if not text or not text.strip(): return False
    key = str(source_id)
    recent = state.setdefault("recent_texts", {}).get(key, [])
    norm = re.sub(r'\s+', ' ', text).strip().lower()
    for prev in recent:
        if difflib.SequenceMatcher(None, norm, prev).ratio() >= 0.85: return True
    return False

def record_published_text(source_id, text):
    if not text or not text.strip(): return
    key = str(source_id)
    norm = re.sub(r'\s+', ' ', text).strip().lower()
    bucket = state.setdefault("recent_texts", {}).setdefault(key, [])
    bucket.append(norm)
    state["recent_texts"][key] = bucket[-15:]
    save_state(state)

def commit_state():
    try:
        subprocess.run(["git", "add", STATE_FILE], check=False)
        subprocess.run(["git", "commit", "-m", "update state"], check=False, capture_output=True)
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=False, capture_output=True)
        subprocess.run(["git", "push"], check=False, capture_output=True)
    except Exception: pass

def call_groq(prompt, max_retries=3):
    if not GROQ_API_KEY: raise RuntimeError("no groq key")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    body = {"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3}
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=30)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                time.sleep(2 ** attempt); continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if attempt == max_retries: raise e
            time.sleep(2 ** attempt)

def call_rephrase(text):
    prompt = f"""أنت محرر صحفي عراقي. أعد صياغة النص بأسلوب صحفي احترافي.
تجاهل الروابط. إذا كان إعلاناً تجارياً اجعل should_post=false، عدا ذلك انشره (true).
النص:
{text}
أعد النتيجة بصيغة JSON فقط: {{"should_post": true/false, "text": "النص المعاد صياغته"}}"""
    try:
        res_raw = call_groq(prompt)
        if res_raw.startswith("```"):
            res_raw = res_raw.strip("`").strip()
            if res_raw.lower().startswith("json"): res_raw = res_raw[4:].strip()
        res = json.loads(res_raw)
        return {"should_post": bool(res.get("should_post", True)), "text": (res.get("text") or text).strip()}
    except Exception as e:
        print(f"❌ خطأ الصياغة: {e}")
        return {"should_post": False, "text": ""}

SHARE_PHRASE_RE = re.compile(r'شارك\s+(القناة|المنشور)\s+مع\s+[اأ]?صدقائ?ك?', re.IGNORECASE)
def insert_link_after_share_phrase(text, link_text):
    match = SHARE_PHRASE_RE.search(text)
    if not match: return None
    newline_idx = text.find('\n', match.end())
    if newline_idx == -1: return text.rstrip() + f"\n{link_text}"
    return text[:newline_idx] + f"\n{link_text}" + text[newline_idx:]

async def apply_processing(config, text):
    res = call_rephrase(text)
    return res["should_post"], res["text"]

def post_to_facebook(page_id, token, text, image_data=None):
    if not page_id or not token: return
    try:
        base_url = f"{GRAPH}/{page_id}"
        if image_data:
            res = requests.post(f"{base_url}/photos", data={"access_token": token, "message": text}, files={"source": ("image.jpg", image_data)}, timeout=30)
        else:
            res = requests.post(f"{base_url}/feed", data={"access_token": token, "message": text}, timeout=30)
        
        res_data = res.json()
        if "error" in res_data:
            print(f"❌ خطأ فيسبوك: {res_data['error']['message']}")
            return
            
        post_id = res_data.get("post_id") or res_data.get("id")
        print(f"✅ نُشر على فيسبوك ({page_id})")
        
        comment_text = ""
        if page_id == PAGE_ID_KIRKUK: comment_text = "تابعنا على التلكرام 👇\nhttps://t.me/KirkukNow24H"
        elif page_id == PAGE_ID_IRAQ: comment_text = "تابعنا على التلكرام 👇\nhttps://t.me/IQNowNews"
        elif page_id == PAGE_ID_STUDENT: comment_text = "تابعنا على التلكرام 👇\nhttps://t.me/TalibLib"
        
        if comment_text:
            requests.post(f"{GRAPH}/{post_id}/comments", data={"access_token": token, "message": comment_text}, timeout=30)
            print(f"✅ تمت إضافة تعليق على فيسبوك")
    except Exception as e:
        print(f"❌ فشل النشر على فيسبوك: {e}")

async def process_batch(source_id, messages):
    config = CHANNELS.get(source_id)
    if not config: return
    raw_text = next((m.message for m in messages if m.message), "")
    if not raw_text: return
    if is_duplicate_content(source_id, raw_text): return
    
    should_post, final_text = await apply_processing(config, raw_text)
    if not should_post or not final_text: return
    
    record_published_text(source_id, raw_text)
    
    if config.get("add_link") and config.get("link"):
        inserted = None
        if config.get("link_after_share_phrase"):
            inserted = insert_link_after_share_phrase(final_text, config["link"])
        if inserted is not None: final_text = inserted
        else: final_text = f"{final_text}\n\n{config['link']}"
        
    media_msgs = [m for m in messages if m.media and not m.video]
    media_data = None
    if media_msgs:
        try: media_data = await client.download_media(media_msgs[0], file=bytes)
        except Exception: pass

    try:
        if media_data:
            await client.send_file(config["target"], media_data, caption=final_text, parse_mode="html")
            print(f"✅ نُشر (وسائط) — {config['name']} [تيليجرام]")
        else:
            await client.send_message(config["target"], final_text, parse_mode="html", link_preview=False)
            print(f"✅ نُشر (نص) — {config['name']} [تيليجرام]")
    except Exception as e: print(f"❌ خطأ تيليجرام: {e}")

    if FB_TOKEN and config.get("fb_page_id"):
        post_to_facebook(config["fb_page_id"], FB_TOKEN, final_text, media_data)

async def catch_up_channel(source_id):
    config = CHANNELS.get(source_id, {})
    entity_ref = config.get("source_username") or source_id
    key = str(source_id)
    last_id = state.get(key, 0)
    if last_id == 0:
        latest = await client.get_messages(entity_ref, limit=1)
        if latest: last_id = max(latest[0].id - 5, 0)
        state[key] = last_id
        save_state(state)
    messages = await client.get_messages(entity_ref, min_id=last_id, limit=100)
    if not messages: return
    messages = list(reversed(messages))
    for msg in messages:
        if msg.grouped_id:
            group = [m for m in messages if m.grouped_id == msg.grouped_id]
            group.sort(key=lambda m: m.id)
            await process_batch(source_id, group)
            new_last = group[-1].id
        else:
            await process_batch(source_id, [msg])
            new_last = msg.id
        state[key] = new_last
        save_state(state)

async def main():
    await client.start()
    for source_id, cfg in CHANNELS.items():
        try: await catch_up_channel(source_id)
        except Exception as e: print(f"❌ فشل {cfg['name']}: {e}")
        commit_state()
    await client.disconnect()
    commit_state()

if __name__ == "__main__":
    asyncio.run(main())
