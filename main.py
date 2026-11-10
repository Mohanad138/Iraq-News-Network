# -*- coding: utf-8 -*-
import os, re, json, time, asyncio, subprocess, difflib
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (MessageEntityTextUrl, MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage)
import requests

TG_API_ID = int(os.getenv("TG_API_ID"))
TG_API_HASH = os.getenv("TG_API_HASH")
TG_SESSION = os.getenv("TG_SESSION")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

GROQ_MODEL = "openai/gpt-oss-120b"
GRAPH = "https://graph.facebook.com/v21.0"
STATE_FILE = "relay_state.json"
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ⚠️ وضع الفحص العميق (True) سيتخطى الذكاء الاصطناعي وينشر آخر 5 منشورات فوراً
TEST_MODE = True 

FB_TOKEN_KIRKUK = os.getenv("FB_TOKEN_KIRKUK")
FB_TOKEN_IRAQ = os.getenv("FB_TOKEN_IRAQ")
FB_TOKEN_STUDENT = os.getenv("FB_TOKEN_STUDENT")

PAGE_ID_KIRKUK = "1471628069360553"
PAGE_ID_STUDENT = "1361819777022332"
PAGE_ID_IRAQ = "1278874611967737"

CHANNELS = {
    -1001565832101: {
        "name": "كركوك الآن", "target": None,
        "source_username": "@kirkukdiary",
        "fb_page_id": PAGE_ID_KIRKUK, 
        "fb_token": FB_TOKEN_KIRKUK,
    },
    -1009001073232: {
        "name": "العراق الآن", "target": None,
        "source_username": "@RN24_IQ",
        "fb_page_id": PAGE_ID_IRAQ, 
        "fb_token": FB_TOKEN_IRAQ,
    },
    -1001530089992: {
        "name": "مكتبة الطالب العراقي", "target": None,
        "source_username": "@eduiq23",
        "fb_page_id": PAGE_ID_STUDENT, 
        "fb_token": FB_TOKEN_STUDENT,
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

# في وضع التجربة، نبدأ بملف حالة فارغ (تجاهل الملف المحفوظ)
state = {} if TEST_MODE else load_state()

def _is_small_thumbnail_photo(photo):
    try:
        dims = [(getattr(s, "w", 0), getattr(s, "h", 0)) for s in photo.sizes if hasattr(s, "w")]
        if not dims: return False
        max_w = max(w for w, _ in dims)
        max_h = max(h for _, h in dims)
        if max_w < 250 and max_h < 250: return True
        if max_w > 0 and max_h > 0:
            ratio = max_w / max_h
            if 0.85 <= ratio <= 1.18 and max(max_w, max_h) <= 800: return True
        return False
    except Exception: return False

def _is_usable_media_msg(m):
    media = m.media
    if isinstance(media, (MessageMediaPhoto, MessageMediaDocument)): return True
    if isinstance(media, MessageMediaWebPage):
        wp = getattr(media, "webpage", None)
        photo = getattr(wp, "photo", None)
        if photo and not _is_small_thumbnail_photo(photo): return True
    return False

def clean_text(text, source_username=None):
    if not text: return ""
    cleaned = text.replace('📊', '')
    if source_username: cleaned = re.sub(re.escape(source_username), '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'(https?://)?t\.me/\S+', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'https?://t\.co/\S+', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    return cleaned

def post_to_facebook(page_id, token, text, media_data=None, is_video=False):
    if not page_id:
        print(f"❌ خطأ: معرف الصفحة (page_id) مفقود!")
        return
    if not token:
        print(f"❌ خطأ: التوكن (Token) مفقود لهذه الصفحة! تأكد من إضافته في Secrets.")
        return
    
    print(f"🔑 التوكن موجود (طوله {len(token)} حرف). جاري محاولة النشر...")
    
    try:
        base_url = f"{GRAPH}/{page_id}"
        if media_data:
            if is_video:
                print("📤 جاري رفع فيديو...")
                res = requests.post(f"{base_url}/videos", data={"access_token": token, "description": text}, files={"source": ("video.mp4", media_data)}, timeout=120)
            else:
                print("📤 جاري رفع صورة...")
                res = requests.post(f"{base_url}/photos", data={"access_token": token, "message": text}, files={"source": ("image.jpg", media_data)}, timeout=60)
        else:
            print("📤 جاري رفع نص فقط...")
            res = requests.post(f"{base_url}/feed", data={"access_token": token, "message": text}, timeout=30)
        
        res_data = res.json()
        if "error" in res_data:
            print(f"❌ رفض فيسبوك النشر: {res_data['error']}")
            return
            
        post_id = res_data.get("post_id") or res_data.get("id")
        print(f"✅✅✅ نجح النشر على فيسبوك! (رقم المنشور: {post_id})")
        
        comment_text = ""
        if page_id == PAGE_ID_KIRKUK: comment_text = "تابعنا على التلكرام 👇\nhttps://t.me/KirkukNow24H"
        elif page_id == PAGE_ID_IRAQ: comment_text = "تابعنا على التلكرام 👇\nhttps://t.me/IQNowNews"
        elif page_id == PAGE_ID_STUDENT: comment_text = "تابعنا على التلكرام 👇\nhttps://t.me/TalibLib"
        
        if comment_text:
            requests.post(f"{GRAPH}/{post_id}/comments", data={"access_token": token, "message": comment_text}, timeout=30)
            print(f"✅ تمت إضافة تعليق بنجاح.")
    except Exception as e:
        print(f"❌ حدث خطأ غير متوقع أثناء الاتصال بفيسبوك: {e}")

async def process_batch(source_id, messages):
    config = CHANNELS.get(source_id)
    if not config: return
    
    print(f"--- 🔍 فحص منشور من {config['name']} ---")
    
    raw_text = next((m.message for m in messages if m.message), "")
    cleaned = clean_text(raw_text, config.get("source_username"))
    
    media_msgs = [m for m in messages if _is_usable_media_msg(m)]
    media_data = None
    is_video = False
    
    if media_msgs:
        first_m = media_msgs[0]
        if isinstance(first_m.media, MessageMediaDocument) and first_m.video:
            is_video = True
        try: 
            media_data = await client.download_media(first_m, file=bytes)
            print(f"📥 تم تحميل الميديا ({'فيديو' if is_video else 'صورة'}).")
        except Exception as e: 
            print(f"⚠️ فشل تحميل الميديا: {e}")

    if not cleaned.strip() and not media_data:
        print(f"⏭️ تجاهل: المنشور فارغ (لا نص ولا صورة).")
        return

    print(f"📝 النص الجاهز للنشر: {cleaned[:50]}...")

    # في وضع التجربة، ننشر مباشرة إلى فيسبوك
    post_to_facebook(config["fb_page_id"], config["fb_token"], cleaned, media_data, is_video)

async def catch_up_channel(source_id):
    config = CHANNELS.get(source_id, {})
    entity_ref = config.get("source_username") or source_id
    
    print(f"📡 جلب آخر 5 منشورات من {config['name']}...")
    messages = await client.get_messages(entity_ref, limit=5)
        
    if not messages: 
        print(f"⚠️ لا توجد منشورات في {config['name']}.")
        return
    
    messages = list(reversed(messages))
    processed_ids = set()
    
    for msg in messages:
        if msg.id in processed_ids: continue
        if msg.grouped_id:
            group = [m for m in messages if m.grouped_id == msg.grouped_id and m.id not in processed_ids]
            group.sort(key=lambda m: m.id)
            await process_batch(source_id, group)
            processed_ids.update(m.id for m in group)
        else:
            await process_batch(source_id, [msg])
            processed_ids.add(msg.id)

async def main():
    print("🚀 بدء التشغيل...")
    await client.start()
    
    tasks = []
    for source_id in CHANNELS:
        tasks.append(catch_up_channel(source_id))
        
    await asyncio.gather(*tasks)
        
    await client.disconnect()
    print("✅ انتهت التشغيلة.")

if __name__ == "__main__":
    asyncio.run(main())
