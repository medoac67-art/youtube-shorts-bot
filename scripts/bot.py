import asyncio
from difflib import SequenceMatcher
import hashlib
import json
import math
import os
import random
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import edge_tts
import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path("output")
DATA_DIR = Path("data")
HISTORY_FILE = DATA_DIR / "stories_history.json"

OUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

W, H = 1080, 1920
GRID_W, GRID_H = 72, 128
PX = 15
PRIVACY_STATUS = "public"

COMMON_AR_FIXES = {
    "اذا": "إذا",
    "الى": "إلى",
    "الي": "إلى",
    "او": "أو",
    "الا": "إلا",
    "احد": "أحد",
    "اشياء": "أشياء",
    "اكثر": "أكثر",
    "اقل": "أقل",
    "امام": "أمام",
    "وراء": "وراء",
    "علي": "على",
    "متي": "متى",
    "حدي": "حتى",
    "حتي": "حتى",
    "لؤلؤ": "لؤلؤ",
    "مسؤل": "مسؤول",
    "سؤال": "سؤال",
    "فجأه": "فجأة",
    "ظلاما": "ظلاماً",
    "شيئا": "شيئاً",
    "صوتا": "صوتاً",
    "بابا": "باباً",
    "سرا": "سراً",
    "ليلا": "ليلاً",
    "صباحا": "صباحاً",
}


def fix_arabic_spelling(text: str) -> str:
    # إزالة التشكيل الزائد وتوحيد المسافات
    cleaned = re.sub(r"[\u064B-\u065F\u0670]", "", str(text or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""

    # تصحيح كلمات شائعة بدقة حدود الكلمة
    for wrong, right in COMMON_AR_FIXES.items():
        cleaned = re.sub(rf"(?<!\S){re.escape(wrong)}(?!\S)", right, cleaned)

    # إزالة المسافات الزائدة قبل علامات الترقيم
    cleaned = re.sub(r"\s+([،.!?؛:])", r"\1", cleaned)
    cleaned = re.sub(r"([،.!?؛:])([^\s\d])", r"\1 \2", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def call_llm_raw(prompt: str, temperature: float = 0.85) -> str | None:
    for env_name, url, model in [
        ("GROQ_API_KEY", "https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile"),
        ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/chat/completions", "openrouter/free"),
        ("ZHIPU_API_KEY", "https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-4-flash"),
    ]:
        key = os.getenv(env_name, "").strip()
        if not key:
            continue
        try:
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": temperature},
                timeout=60,
            )
            if r.ok:
                content = r.json()["choices"][0]["message"]["content"]
                if content and content.strip():
                    return content
        except Exception:
            pass

    google_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if google_key:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={google_key}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=60,
            )
            if r.ok:
                content = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                if content and content.strip():
                    return content
        except Exception:
            pass

    return None


def extract_json(text: str) -> dict:
    cleaned = re.sub(r"```json|```", "", text, flags=re.IGNORECASE).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    raw = match.group(0) if match else cleaned
    return json.loads(raw)


def polish_arabic_payload(payload: dict) -> dict:
    title_in = fix_arabic_spelling(payload.get("title", ""))
    desc_in = fix_arabic_spelling(payload.get("description", ""))
    thumb_in = fix_arabic_spelling(payload.get("thumbnail_text", ""))
    beats_in = payload.get("beats", [])

    # تطبيق التصحيح السريع أولاً
    for b in beats_in:
        b["caption"] = fix_arabic_spelling(b.get("caption", ""))

    # مراجعة إملائية ولغوية شاملة عبر النموذج إن توفر
    review_prompt = f"""
راجع النصوص العربية التالية وصحح جميع الأخطاء الإملائية والنحوية وعلامات الترقيم دون تغيير المعنى أو عدد المقاطع ({len(beats_in)} مقطعاً).
أعد النتيجة بصيغة JSON فقط بنفس المفاتيح:
{{
  "title": "...",
  "description": "...",
  "thumbnail_text": "...",
  "captions": ["...", "..."]
}}

المدخلات:
{json.dumps({"title": title_in, "description": desc_in, "thumbnail_text": thumb_in, "captions": [b.get("caption", "") for b in beats_in]}, ensure_ascii=False)}
"""
    raw_rev = call_llm_raw(review_prompt, temperature=0.2)
    if raw_rev:
        try:
            rev = extract_json(raw_rev)
            if fix_arabic_spelling(rev.get("title", "")):
                payload["title"] = fix_arabic_spelling(rev["title"])
            if fix_arabic_spelling(rev.get("description", "")):
                payload["description"] = fix_arabic_spelling(rev["description"])
            if fix_arabic_spelling(rev.get("thumbnail_text", "")):
                payload["thumbnail_text"] = fix_arabic_spelling(rev["thumbnail_text"])
            rev_caps = rev.get("captions", [])
            if isinstance(rev_caps, list) and len(rev_caps) == len(beats_in):
                for idx, cap in enumerate(rev_caps):
                    fixed_c = fix_arabic_spelling(cap)
                    if fixed_c:
                        beats_in[idx]["caption"] = fixed_c
        except Exception:
            pass

    payload["title"] = fix_arabic_spelling(payload.get("title", ""))
    payload["description"] = fix_arabic_spelling(payload.get("description", ""))
    payload["thumbnail_text"] = fix_arabic_spelling(payload.get("thumbnail_text", ""))
    for b in beats_in:
        b["caption"] = fix_arabic_spelling(b.get("caption", ""))

    return payload


def load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(items: list[dict]) -> None:
    HISTORY_FILE.write_text(json.dumps(items[-300:], ensure_ascii=False, indent=2), encoding="utf-8")


def story_fingerprint(payload: dict) -> str:
    title = fix_arabic_spelling(payload.get("title", ""))
    captions = " ".join(fix_arabic_spelling(b.get("caption", "")) for b in payload.get("beats", []))
    raw = f"{title} | {captions}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def is_duplicate_story(candidate: dict, history: list[dict]) -> bool:
    cand_title = fix_arabic_spelling(candidate.get("title", ""))
    cand_text = " ".join(fix_arabic_spelling(b.get("caption", "")) for b in candidate.get("beats", []))
    cand_fp = story_fingerprint(candidate)

    if not cand_title or len(cand_text) < 80:
        return True

    for item in history:
        if item.get("fingerprint") == cand_fp:
            return True
        old_title = fix_arabic_spelling(item.get("title", ""))
        old_text = fix_arabic_spelling(item.get("summary_text", ""))
        if old_title and SequenceMatcher(None, cand_title, old_title).ratio() > 0.68:
            return True
        if old_text and SequenceMatcher(None, cand_text, old_text).ratio() > 0.62:
            return True
    return False


def get_slot_number() -> int:
    now_hour = datetime.now(timezone.utc).hour
    if now_hour < 10:
        return 1
    if now_hour < 14:
        return 2
    if now_hour < 18:
        return 3
    return 4


def call_llm_for_story(prompt: str) -> dict | None:
    raw = call_llm_raw(prompt, temperature=1.0)
    if not raw:
        return None
    try:
        data = extract_json(raw)
        if isinstance(data.get("beats"), list) and len(data["beats"]) >= 12:
            return data
    except Exception:
        return None
    return None


def build_unique_fallback(slot: int, history: list[dict]) -> dict:
    run_id = uuid.uuid4().hex[:6]
    idx = len(history) + slot
    place_ar = "المنارة الساحلية القديمة"
    env_kind = "lighthouse"
    time_of_day = "night"
    beats = [
        {"caption": "عند منتصف الليل اقترب المحقق من المنارة الساحلية القديمة والبحر يضرب الصخور.", "env": env_kind, "time": time_of_day, "mood": "neutral", "action": "walk", "prop": "none", "has_shadow": False},
        {"caption": "كانت المنارة مطفأة منذ سنوات طويلة، لكن ضوءاً خافتاً ظهر في نافذتها العليا.", "env": env_kind, "time": time_of_day, "mood": "curious", "action": "point", "prop": "idea", "has_shadow": False},
        {"caption": "دفع الباب الخشبي الثقيل فصدر صرير حاد تردد داخل البناء الفارغ.", "env": env_kind, "time": time_of_day, "mood": "surprised", "action": "alert", "prop": "exclamation", "has_shadow": False},
        {"caption": "أشعل مصباحه اليدوي وبدأ يصعد الدرج الحجري الملتف خطوة بعد خطوة.", "env": env_kind, "time": time_of_day, "mood": "curious", "action": "walk", "prop": "none", "has_shadow": False},
        {"caption": "على الجدار الحجري الرطب وجد آثار أقدام حديثة تتجه نحو الأعلى.", "env": env_kind, "time": time_of_day, "mood": "surprised", "action": "point", "prop": "idea", "has_shadow": False},
        {"caption": "توقف فجأة عندما شعر بأن أحداً يقف خلفه في الظلام.", "env": env_kind, "time": time_of_day, "mood": "shocked", "action": "alert", "prop": "exclamation", "has_shadow": True},
        {"caption": "استدار بسرعة فلم يجد أحداً، لكن خيالاً غريباً انعكس على الجدار.", "env": env_kind, "time": time_of_day, "mood": "shocked", "action": "alert", "prop": "exclamation", "has_shadow": True},
        {"caption": "اقترب الخيال ببطء ثم أشار نحو صندوق معدني قديم في الزاوية.", "env": env_kind, "time": time_of_day, "mood": "surprised", "action": "point", "prop": "none", "has_shadow": True},
        {"caption": "فتح المحقق الصندوق فوجد دفتراً قديماً يحمل تاريخ آخر ليلة عملت فيها المنارة.", "env": env_kind, "time": time_of_day, "mood": "curious", "action": "point", "prop": "speech", "has_shadow": False},
        {"caption": "كانت الصفحات الأخيرة تحذر من أن الضوء لا ينطفئ بل يختبئ.", "env": env_kind, "time": time_of_day, "mood": "shocked", "action": "alert", "prop": "exclamation", "has_shadow": False},
        {"caption": "وفجأة انطفأ المصباح اليدوي في يده وأصبح المكان غارقاً في العتمة.", "env": env_kind, "time": time_of_day, "mood": "shocked", "action": "alert", "prop": "exclamation", "has_shadow": True},
        {"caption": "سمع صوت همس خافت يطلب منه ألا ينظر نحو البحر.", "env": env_kind, "time": time_of_day, "mood": "shocked", "action": "alert", "prop": "speech", "has_shadow": True},
        {"caption": "رفع رأسه نحو النافذة ليرى سفينة قديمة تتحرك بلا أضواء وسط الضباب.", "env": env_kind, "time": "fog", "mood": "shocked", "action": "point", "prop": "exclamation", "has_shadow": False},
        {"caption": "وعندما عاد لينظر إلى الدفتر كانت الصفحة الأخيرة قد اختفت تماماً.", "env": env_kind, "time": "night", "mood": "surprised", "action": "alert", "prop": "none", "has_shadow": True},
    ]
    return {
        "title": f"ملف سري #{idx} [{run_id}]: سر {place_ar}",
        "description": f"قصة غموض طويلة ومترابطة من سلسلة ملفات سرية داخل {place_ar}. تابع التفاصيل حتى النهاية.",
        "keywords": ["ملفات سرية", "pixel art", "قصص غموض", "شورتس", "قصة طويلة"],
        "hashtags": ["#shorts", "#pixelart", "#ملفات_سرية", "#غموض", "#قصص"],
        "thumbnail_text": "سر المنارة",
        "beats": beats,
    }


def generate_unique_story_payload(slot: int, history: list[dict]) -> dict:
    now_utc = datetime.now(timezone.utc)
    recent_titles = [fix_arabic_spelling(h.get("title", "")) for h in history[-30:] if h.get("title")]
    avoid_block = "\n".join(f"- {t}" for t in recent_titles) if recent_titles else "- لا توجد قصص سابقة بعد"

    for attempt in range(4):
        nonce = uuid.uuid4().hex[:8]
        prompt = f"""
ابتكر قصة غموض عربية مترابطة ومشوقة وطويلة نسبياً لقناة يوتيوب شورتس ("ملفات سرية") بإملاء عربي فصيح سليم وخالٍ من الأخطاء الإملائية.
معرف الجلسة: {nonce} | المحاولة: {attempt + 1} | التاريخ: {now_utc.strftime("%Y-%m-%d %H:%M")} UTC | الفترة: #{slot}.

المدة المطلوبة عند القراءة الصوتية: من 45 إلى 60 ثانية (14 إلى 15 مقطعاً متسلسلاً).
ممنوع منعاً باتاً عمل قفزات غير منطقية أو جمل منفصلة؛ يجب أن تكون كل جملة استمراراً مباشراً وطبيعياً للجملة التي قبلها في نفس القصة والمكان.
ممنوع تكرار هذه العناوين أو الأفكار السابقة:
{avoid_block}

أعد النتيجة بصيغة JSON فقط بهذا الشكل:
{{
  "title": "عنوان مشوق بإملاء سليم",
  "description": "وصف مشوق من جملتين باللغة العربية بإملاء سليم",
  "keywords": ["ملفات سرية", "pixel art", "قصص غموض", "شورتس", "قصة جديدة"],
  "hashtags": ["#shorts", "#pixelart", "#ملفات_سرية", "#غموض", "#قصص"],
  "thumbnail_text": "كلمتان",
  "beats": [
    {{
      "caption": "جملة مترابطة واضحة بإملاء صحيح من 8 إلى 13 كلمة",
      "env": "lighthouse",
      "time": "night",
      "mood": "neutral",
      "action": "walk",
      "prop": "none",
      "has_shadow": false
    }}
  ]
}}

القواعد الصارمة:
- عدد المقاطع (beats) يجب أن يكون 14 إلى 15 مقطعاً مترابطاً تحكي قصة واحدة متماسكة من البداية إلى النهاية.
- التزم بالإملاء العربي الصحيح (الهمزات، الألف المقصورة، التاء المربوطة، وعلامات الترقيم).
- "env": اختر من ["lighthouse", "forest", "tunnel", "library", "ship", "basement", "alley", "corridor", "room", "street"].
- "time": اختر من ["night", "fog", "dusk", "day"].
- "mood": اختر من ["neutral", "surprised", "shocked", "happy", "curious"].
- "action": اختر من ["walk", "point", "alert", "wave"].
- "prop": اختر من ["none", "exclamation", "idea", "speech", "heart"].
"""
        data = call_llm_for_story(prompt)
        if data and isinstance(data.get("beats"), list) and len(data["beats"]) >= 12:
            data["beats"] = data["beats"][:15]
            polished = polish_arabic_payload(data)
            if not is_duplicate_story(polished, history):
                return polished

    return polish_arabic_payload(build_unique_fallback(slot, history))


def build_formatted_description(payload: dict) -> str:
    desc = fix_arabic_spelling(payload.get("description", ""))
    keywords = payload.get("keywords", [])
    hashtags = payload.get("hashtags", [])
    kw_text = ", ".join([fix_arabic_spelling(k) for k in keywords if fix_arabic_spelling(k)])
    tags_text = " ".join(
        [
            t if str(t).strip().startswith("#") else f"#{fix_arabic_spelling(t).replace(' ', '_')}"
            for t in hashtags
            if fix_arabic_spelling(t)
        ]
    )
    return f"{desc}\n\nالكلمات المفتاحية:\n{kw_text}\n\n{tags_text}".strip()


def px_rect(draw: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int, fill, outline=None) -> None:
    draw.rectangle([x0 * PX, y0 * PX, (x1 + 1) * PX - 1, (y1 + 1) * PX - 1], fill=fill, outline=outline)


def draw_dynamic_scene(draw: ImageDraw.ImageDraw, env: str, time_of_day: str, frame_idx: int, seed_offset: int) -> None:
    rng = random.Random(seed_offset + (frame_idx // 6))

    if time_of_day == "day":
        sky_top, sky_bot, ground_c = (112, 190, 245), (168, 222, 252), (82, 162, 74)
    elif time_of_day == "dusk":
        sky_top, sky_bot, ground_c = (66, 36, 86), (198, 92, 78), (56, 48, 74)
    elif time_of_day == "fog":
        sky_top, sky_bot, ground_c = (78, 92, 104), (126, 140, 148), (58, 72, 74)
    else:
        sky_top, sky_bot, ground_c = (20 + (seed_offset % 14), 22, 50 + (seed_offset % 18)), (34, 30, 66), (66, 64, 86)

    px_rect(draw, 0, 0, GRID_W - 1, 28, sky_top)
    px_rect(draw, 0, 29, GRID_W - 1, 36, sky_bot)

    if time_of_day in ("night", "dusk"):
        mx = 8 + (seed_offset % 46)
        px_rect(draw, mx, 6, mx + 6, 12, (245, 238, 185))
        for _ in range(8):
            sx, sy = rng.randint(2, GRID_W - 3), rng.randint(2, 22)
            px_rect(draw, sx, sy, sx, sy, (240, 242, 255))

    if env == "lighthouse":
        px_rect(draw, 0, 76, GRID_W - 1, GRID_H - 1, (34, 78, 122))
        px_rect(draw, 46, 18, 58, 78, (226, 228, 238), outline=(20, 22, 32))
        for ry in range(24, 74, 12):
            px_rect(draw, 46, ry, 58, ry + 5, (196, 58, 58))
        px_rect(draw, 48, 12, 56, 18, (255, 232, 120))
    elif env == "forest":
        px_rect(draw, 0, 74, GRID_W - 1, GRID_H - 1, ground_c)
        for tx in range(4, GRID_W - 6, 14):
            px_rect(draw, tx, 38, tx + 4, 75, (74, 46, 30), outline=(18, 14, 12))
            px_rect(draw, tx - 4, 22, tx + 8, 42, (36, 96, 58), outline=(16, 28, 20))
    elif env == "tunnel":
        px_rect(draw, 0, 0, GRID_W - 1, 86, (44, 48, 64))
        px_rect(draw, 22, 24, 50, 86, (24, 26, 36), outline=(92, 98, 118))
        px_rect(draw, 0, 87, GRID_W - 1, GRID_H - 1, (68, 70, 82))
    elif env == "library":
        px_rect(draw, 0, 0, GRID_W - 1, 86, (88, 56, 36))
        for sx in range(4, GRID_W - 8, 18):
            px_rect(draw, sx, 22, sx + 14, 76, (62, 38, 24), outline=(22, 16, 12))
            for by in range(26, 72, 10):
                px_rect(draw, sx + 2, by, sx + 12, by + 5, (168, 68, 58))
        px_rect(draw, 0, 87, GRID_W - 1, GRID_H - 1, (112, 74, 46))
    elif env == "ship":
        px_rect(draw, 0, 78, GRID_W - 1, GRID_H - 1, (28, 66, 98))
        px_rect(draw, 8, 54, 62, 80, (92, 54, 34), outline=(18, 14, 12))
        px_rect(draw, 32, 18, 36, 54, (64, 40, 26))
    elif env == "basement":
        px_rect(draw, 0, 0, GRID_W - 1, 86, (58, 56, 68))
        px_rect(draw, 30, 34, 42, 62, (32, 30, 38), outline=(16, 16, 20))
        px_rect(draw, 0, 87, GRID_W - 1, GRID_H - 1, (76, 74, 86))
    else:
        px_rect(draw, 0, 36, GRID_W - 1, 86, (132, 68, 64))
        for bx in range(4, GRID_W - 10, 16):
            bh = 18 + ((bx + seed_offset) % 10)
            px_rect(draw, bx, 37 - bh, bx + 12, 36, (34, 38, 58), outline=(16, 18, 28))
        px_rect(draw, 0, 87, GRID_W - 1, GRID_H - 1, ground_c)

    if time_of_day == "fog":
        fog_shift = (frame_idx * 2) % 12
        for fy in range(42, 86, 10):
            px_rect(draw, fog_shift, fy, GRID_W - 12 + (fog_shift // 2), fy + 2, (195, 205, 212))


def draw_character(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    step_phase: int,
    mood: str = "neutral",
    action: str = "walk",
    shirt_color=(102, 112, 212),
    is_shadow: bool = False,
) -> None:
    outline = (16, 16, 24)
    if is_shadow:
        hair_c = (28, 28, 44)
        skin_c = (34, 34, 52)
        shirt_c = (24, 24, 40)
        pants_c = (18, 18, 32)
        eye_c = (135, 155, 235)
    else:
        hair_c = (36, 36, 48)
        skin_c = (242, 196, 156)
        shirt_c = shirt_color
        pants_c = (48, 56, 86)
        eye_c = (20, 20, 28)

    px_rect(draw, cx - 5, cy - 22, cx + 5, cy - 11, skin_c, outline=outline)
    px_rect(draw, cx - 5, cy - 23, cx + 5, cy - 17, hair_c, outline=outline)

    if mood == "shocked":
        px_rect(draw, cx - 4, cy - 16, cx - 2, cy - 13, (255, 255, 255))
        px_rect(draw, cx + 2, cy - 16, cx + 4, cy - 13, (255, 255, 255))
        px_rect(draw, cx - 3, cy - 15, cx - 3, cy - 14, eye_c)
        px_rect(draw, cx + 3, cy - 15, cx + 3, cy - 14, eye_c)
        px_rect(draw, cx - 1, cy - 12, cx + 1, cy - 11, (40, 20, 24))
    elif mood == "surprised":
        px_rect(draw, cx - 3, cy - 16, cx - 2, cy - 13, eye_c)
        px_rect(draw, cx + 2, cy - 16, cx + 3, cy - 13, eye_c)
        px_rect(draw, cx, cy - 12, cx, cy - 11, (40, 20, 24))
    elif mood == "happy":
        px_rect(draw, cx - 4, cy - 15, cx - 2, cy - 14, eye_c)
        px_rect(draw, cx + 2, cy - 15, cx + 4, cy - 14, eye_c)
        px_rect(draw, cx - 2, cy - 12, cx + 2, cy - 12, (185, 78, 78))
    else:
        px_rect(draw, cx - 3, cy - 15, cx - 2, cy - 14, eye_c)
        px_rect(draw, cx + 2, cy - 15, cx + 3, cy - 14, eye_c)
        px_rect(draw, cx - 1, cy - 12, cx + 1, cy - 12, (90, 58, 52))

    px_rect(draw, cx - 5, cy - 10, cx + 5, cy - 2, shirt_c, outline=outline)

    if action == "point":
        px_rect(draw, cx - 8, cy - 10, cx - 6, cy - 3, shirt_c, outline=outline)
        px_rect(draw, cx + 6, cy - 12, cx + 10, cy - 10, shirt_c, outline=outline)
    elif action == "wave":
        w = (step_phase % 2) * -1
        px_rect(draw, cx - 8, cy - 10, cx - 6, cy - 3, shirt_c, outline=outline)
        px_rect(draw, cx + 6, cy - 14 + w, cx + 8, cy - 7 + w, shirt_c, outline=outline)
    elif action == "alert":
        px_rect(draw, cx - 8, cy - 12, cx - 6, cy - 7, shirt_c, outline=outline)
        px_rect(draw, cx + 6, cy - 12, cx + 8, cy - 7, shirt_c, outline=outline)
    else:
        px_rect(draw, cx - 8, cy - 10, cx - 6, cy - 3, shirt_c, outline=outline)
        px_rect(draw, cx + 6, cy - 10, cx + 8, cy - 3, shirt_c, outline=outline)

    left_y = cy - 1 if (step_phase % 2 == 0) else cy - 2
    right_y = cy - 2 if (step_phase % 2 == 0) else cy - 1
    px_rect(draw, cx - 4, left_y, cx - 1, cy + 3, pants_c, outline=outline)
    px_rect(draw, cx + 1, right_y, cx + 4, cy + 3, pants_c, outline=outline)


def draw_prop(draw: ImageDraw.ImageDraw, cx: int, cy: int, prop: str, frame_idx: int) -> None:
    bob = (frame_idx % 4) // 2
    py = cy - 32 - bob
    if prop == "exclamation":
        px_rect(draw, cx, py, cx + 1, py + 6, (245, 68, 58), outline=(20, 16, 20))
        px_rect(draw, cx, py + 8, cx + 1, py + 9, (245, 68, 58), outline=(20, 16, 20))
    elif prop == "idea":
        px_rect(draw, cx - 2, py, cx + 3, py + 5, (255, 224, 72), outline=(25, 22, 20))
        px_rect(draw, cx - 1, py + 6, cx + 2, py + 8, (180, 185, 195))
    elif prop == "speech":
        px_rect(draw, cx - 5, py, cx + 6, py + 6, (245, 248, 255), outline=(20, 22, 30))
        for dx in [-2, 0, 2]:
            px_rect(draw, cx + dx, py + 2, cx + dx, py + 4, (35, 38, 52))
    elif prop == "heart":
        px_rect(draw, cx - 3, py + 1, cx + 4, py + 4, (238, 64, 98))
        px_rect(draw, cx - 2, py, cx, py + 1, (238, 64, 98))
        px_rect(draw, cx + 1, py, cx + 3, py + 1, (238, 64, 98))
        px_rect(draw, cx - 1, py + 5, cx + 2, py + 6, (238, 64, 98))
        px_rect(draw, cx, py + 7, cx + 1, py + 7, (238, 64, 98))


def load_arabic_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def wrap_caption_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        test = f"{cur} {w}"
        bbox = draw.textbbox((0, 0), test, font=font)
        if (bbox[2] - bbox[0]) <= max_w:
            cur = test
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines[:2]


def render_beat_frames(beat: dict, beat_idx: int, duration_sec: float, story_seed: int, fps: int = 12) -> list[Path]:
    num_frames = max(int(round(duration_sec * fps)), 10)
    env = str(beat.get("env", "lighthouse")).strip()
    time_of_day = str(beat.get("time", "night")).strip()
    mood = str(beat.get("mood", "neutral")).strip()
    action = str(beat.get("action", "walk")).strip()
    prop = str(beat.get("prop", "none")).strip()
    has_shadow = bool(beat.get("has_shadow", True))
    caption_raw = fix_arabic_spelling(beat.get("caption", ""))

    shirt_palette = [
        (102, 112, 212),
        (214, 96, 88),
        (72, 168, 138),
        (218, 152, 66),
        (158, 98, 214),
    ]
    shirt_color = shirt_palette[(story_seed + beat_idx) % len(shirt_palette)]
    font_cap = load_arabic_font(52)
    out_frames: list[Path] = []

    for f_i in range(num_frames):
        img = Image.new("RGB", (W, H), (18, 18, 32))
        draw = ImageDraw.Draw(img)

        draw_dynamic_scene(draw, env, time_of_day, f_i, story_seed + beat_idx * 37)
        char_y = 92

        drift = int(math.sin((f_i / max(num_frames, 1)) * math.pi) * 6)
        char_x = 16 + ((story_seed + beat_idx * 5) % 14) + drift
        draw_character(
            draw,
            char_x,
            char_y,
            f_i,
            mood=mood,
            action=action,
            shirt_color=shirt_color,
            is_shadow=False,
        )

        if has_shadow:
            shadow_x = min(char_x + 22 - (drift // 2), GRID_W - 8)
            draw_character(
                draw,
                shadow_x,
                char_y,
                f_i + 1,
                mood=mood,
                action=action,
                is_shadow=True,
            )

        if prop != "none":
            draw_prop(draw, char_x, char_y, prop, f_i)

        if caption_raw:
            lines = wrap_caption_lines(draw, caption_raw, font_cap, W - 140)
            line_boxes = [draw.textbbox((0, 0), ln, font=font_cap) for ln in lines]
            line_h = max([(b[3] - b[1]) for b in line_boxes] or [56])
            max_line_w = max([(b[2] - b[0]) for b in line_boxes] or [200])

            box_w = min(max_line_w + 76, W - 70)
            box_h = len(lines) * (line_h + 10) + 34
            bx0 = (W - box_w) // 2
            by0 = 1410
            bx1 = bx0 + box_w
            by1 = by0 + box_h

            draw.rounded_rectangle([bx0, by0, bx1, by1], radius=22, fill=(14, 16, 26), outline=(120, 210, 175), width=3)
            cur_y = by0 + 16
            for ln, b in zip(lines, line_boxes):
                lw = b[2] - b[0]
                lx = (W - lw) // 2
                draw.text((lx, cur_y), ln, font=font_cap, fill=(255, 255, 255))
                cur_y += line_h + 10

        frame_path = OUT_DIR / f"beat_{beat_idx:02d}_f{f_i:03d}.png"
        img.save(frame_path)
        out_frames.append(frame_path)

    return out_frames


def get_audio_duration(audio_path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return max(float(res.stdout.strip()), 1.0)


def try_phonon_tts(full_script: str, audio_path: Path) -> bool:
    phonon_model = os.getenv("PHONON_MODEL", "").strip()
    if not phonon_model:
        return False
    try:
        import ptts  # type: ignore

        wav_tmp = OUT_DIR / "phonon_raw.wav"
        tts = ptts.TTS(phonon_model)
        tts.save(str(wav_tmp), full_script)
        if wav_tmp.exists() and wav_tmp.stat().st_size > 1024:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(wav_tmp),
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "192k",
                    str(audio_path),
                ],
                check=True,
            )
            return audio_path.exists() and audio_path.stat().st_size > 1024
    except Exception as e:
        print("Phonon TTS fallback note:", e)
    return False


async def generate_continuous_voice(beats: list[dict], audio_path: Path) -> list[float]:
    lines = [fix_arabic_spelling(b.get("caption", "")) or "ملفات سرية" for b in beats]
    full_script = " ".join(lines)

    used_phonon = try_phonon_tts(full_script, audio_path)
    if not used_phonon:
        voice = "ar-SA-HamedNeural"
        comm = edge_tts.Communicate(text=full_script, voice=voice, rate="+2%")
        with open(audio_path, "wb") as f:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])

    total_dur = get_audio_duration(audio_path)
    weights = [max(len(re.findall(r"\S+", line)), 1) for line in lines]
    weight_sum = sum(weights) or len(lines)
    return [max((w / weight_sum) * total_dur, 1.5) for w in weights]


def create_thumbnail(first_frame: Path, thumb_text: str, out_thumb: Path) -> None:
    im = Image.open(first_frame).convert("RGB").resize((W, H), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(im)
    draw.rectangle([0, 1540, W, H], fill=(12, 14, 24))
    clean_t = fix_arabic_spelling(thumb_text) or "ملف سري"
    font_t = load_arabic_font(72)
    bbox = draw.textbbox((0, 0), clean_t, font=font_t)
    tw = bbox[2] - bbox[0]
    tx = (W - tw) // 2
    draw.text((tx, 1650), clean_t, font=font_t, fill=(255, 220, 72))
    im.save(out_thumb)


def render_video_from_beats(beats: list[dict], beat_durations: list[float], audio_path: Path, story_seed: int, out_video: Path) -> None:
    fps = 12
    all_frames: list[Path] = []
    for idx, (beat, dur) in enumerate(zip(beats, beat_durations)):
        frames = render_beat_frames(beat, idx, dur, story_seed=story_seed, fps=fps)
        all_frames.extend(frames)

    frames_txt = OUT_DIR / "frames.txt"
    frame_dur = 1.0 / fps
    with open(frames_txt, "w", encoding="utf-8") as f:
        for fp in all_frames:
            f.write(f"file '{fp.resolve()}'\n")
            f.write(f"duration {frame_dur:.6f}\n")
        if all_frames:
            f.write(f"file '{all_frames[-1].resolve()}'\n")

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(frames_txt),
            "-i",
            str(audio_path),
            "-vf",
            "scale=1080:1920:flags=neighbor,format=yuv420p",
            "-r",
            "24",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(out_video),
        ],
        check=True,
    )


def upload_to_youtube(
    video_path: Path,
    thumb_path: Path,
    title: str,
    description: str,
    keywords: list[str],
    privacy_status: str = PRIVACY_STATUS,
) -> str | None:
    client_id = os.getenv("YT_CLIENT_ID", "").strip()
    client_secret = os.getenv("YT_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("YT_REFRESH_TOKEN", "").strip()

    if not (client_id and client_secret and refresh_token):
        print("YouTube secrets not found; skipping upload.")
        return None

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.force-ssl",
        ],
    )
    creds.refresh(Request())

    youtube = build("youtube", "v3", credentials=creds)

    clean_tags = [fix_arabic_spelling(k).lstrip("#")[:30] for k in keywords if fix_arabic_spelling(k)][:15]
    body = {
        "snippet": {
            "title": fix_arabic_spelling(title)[:95],
            "description": description[:4900],
            "tags": clean_tags,
            "categoryId": "24",
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _, response = request.next_chunk()

    video_id = response.get("id")
    if video_id and thumb_path.exists():
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumb_path), mimetype="image/png"),
            ).execute()
        except Exception as e:
            print("Thumbnail upload note:", e)

    print("Uploaded to YouTube! Video ID:", video_id, "Privacy:", privacy_status)
    return video_id


def main() -> None:
    slot = get_slot_number()
    history = load_history()
    payload = generate_unique_story_payload(slot, history)
    beats = payload.get("beats", [])[:15]
    story_seed = int(story_fingerprint(payload)[:8], 16)

    formatted_description = build_formatted_description(payload)
    meta = {
        "slot": slot,
        "title": fix_arabic_spelling(payload.get("title", "")),
        "description": formatted_description,
        "beats": beats,
    }
    (OUT_DIR / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    audio_path = OUT_DIR / "voice.mp3"
    beat_durations = asyncio.run(generate_continuous_voice(beats, audio_path))

    video_path = OUT_DIR / "short_final.mp4"
    render_video_from_beats(beats, beat_durations, audio_path, story_seed, video_path)

    first_frame = OUT_DIR / "beat_00_f000.png"
    thumb_path = OUT_DIR / "thumbnail.png"
    if first_frame.exists():
        create_thumbnail(first_frame, str(payload.get("thumbnail_text", "ملفات سرية")), thumb_path)

    upload_to_youtube(
        video_path=video_path,
        thumb_path=thumb_path,
        title=str(meta["title"]),
        description=formatted_description,
        keywords=payload.get("keywords", []),
        privacy_status=PRIVACY_STATUS,
    )

    history.append(
        {
            "date_utc": datetime.now(timezone.utc).isoformat(),
            "title": meta["title"],
            "summary_text": " ".join(fix_arabic_spelling(b.get("caption", "")) for b in beats),
            "fingerprint": story_fingerprint(payload),
        }
    )
    save_history(history)

    print("DONE! Generated:", video_path, thumb_path)
    print("\n--- DESCRIPTION ---\n" + formatted_description)


if __name__ == "__main__":
    main()
