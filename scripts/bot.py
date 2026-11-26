import asyncio
import base64
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
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT_DIR = Path("output")
DATA_DIR = Path("data")
HISTORY_FILE = DATA_DIR / "stories_history.json"

OUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

W, H = 1080, 1920
GRID_W, GRID_H = 120, 213
PX = 9
PRIVACY_STATUS = "public"

DEFAULT_FISH_KEYS = [
    "sk-fish-6YUfu-lSRlfppfamSTY-NrEAM1Y-D6fp-hMFWbOIfzI",
    "sk-fish-6iO-qu3Rvu4riNr2yY321BirGup5LPvsu3_pwJNgApE",
    "sk-fish-3Oojt9SvyaueZKY-Tm38z6i6BT4wRCneQdcWHGL4HUg",
    "sk-fish-LGMwCDbpMZwCYU_bBXAYOD0V-UKy16Ut3iEfXZ9T3s8",
]

# نركز على النبرات الهادئة والغامضة فقط ونتجنب الحماس المفرط أو الفرح
EMOTE_ALIAS_MAP = {
    "whisper": "whispering",
    "sigh": "sighing",
    "gasp": "gasping",
    "laugh": "sighing",
    "lauging": "sighing",
    "laughing": "sighing",
    "excited": "nervous",
}

ALLOWED_EMOTES = {
    "whispering",
    "sighing",
    "gasping",
    "nervous",
    "sad",
}

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
    "سرورية": "غامضة",
    "المتدلية": "القديمة",
    "داخليا": "داخلياً",
}


def normalize_emote_name(raw_tag: str) -> str:
    tag = re.sub(r"[^a-zA-Z0-9_\-\s]", "", str(raw_tag or "")).strip().lower()
    tag = re.sub(r"\s+", " ", tag)
    if tag in EMOTE_ALIAS_MAP:
        tag = EMOTE_ALIAS_MAP[tag]
    return tag if tag in ALLOWED_EMOTES else ""


def enrich_natural_pauses(text: str) -> str:
    """إضافة فواصل وعلامات توقف طبيعية لتهدئة الإيقاع ومنع القراءة السريعة المتصلة."""
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if not s:
        return s
    # فصل الجمل الطويلة بفواصل طبيعية بعد أدوات الربط والظروف
    connectors = [
        "ثم ", "بعدها ", "وعندما ", "حين ", "فإذا ", "لكنه ", "لكن ",
        "ببطء ", "بصمت ", "في تلك اللحظة ", "على الفور ", "من خلفه ",
    ]
    for conn in connectors:
        s = re.sub(rf"(?<!،\s)(?<!\.\.\.\s)\b{re.escape(conn.strip())}\b", f"، {conn.strip()}", s)
    s = re.sub(r"^،\s*", "", s)
    s = re.sub(r"\s*،\s*،", "،", s)
    s = re.sub(r"\s+([،.!?؛:])", r"\1", s)
    s = re.sub(r"([،.!?؛:])([^\s\d\.])", r"\1 \2", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_brackets_in_speech_text(text: str) -> str:
    s = str(text or "").replace("［", "[").replace("］", "]")
    s = re.sub(r"[*_~`]+", " ", s)

    def _repl(m: re.Match) -> str:
        inner = m.group(1).strip().lower()
        norm = normalize_emote_name(inner)
        if norm:
            return f" [{norm}] "
        return " "

    s = re.sub(r"\[([^\[\]]+)\]", _repl, s)
    s = re.sub(r"<[^<>]+>", " ", s)
    s = enrich_natural_pauses(s)
    return re.sub(r"\s+", " ", s).strip()


def format_tts_for_provider(text: str, provider: str) -> str:
    normalized = normalize_brackets_in_speech_text(text)
    if provider == "fish":
        return normalized
    if provider == "google":
        return re.sub(r"\[([^\[\]]+)\]", r"<\1>", normalized)
    no_tags = re.sub(r"\[([^\[\]]+)\]", "", normalized)
    return re.sub(r"\s+", " ", no_tags).strip()


def strip_emotes_for_caption(text: str) -> str:
    s = str(text or "").replace("［", "[").replace("］", "]")
    s = re.sub(r"\[[^\[\]]*\]", "", s)
    s = re.sub(r"<[^<>]*>", "", s)
    s = re.sub(r"[*_~`]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def fix_arabic_spelling(text: str) -> str:
    cleaned = re.sub(r"[\u064B-\u065F\u0670]", "", str(text or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""

    for wrong, right in COMMON_AR_FIXES.items():
        cleaned = re.sub(rf"(?<!\S){re.escape(wrong)}(?!\S)", right, cleaned)

    cleaned = re.sub(r"\s+([،.!?؛:])", r"\1", cleaned)
    cleaned = re.sub(r"([،.!?؛:])([^\s\d\.])", r"\1 \2", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def clean_tag_token(raw: str) -> str:
    tok = fix_arabic_spelling(strip_emotes_for_caption(str(raw or ""))).lstrip("#").strip()
    tok = re.sub(r"[^\w\u0600-\u06FF]+", "_", tok, flags=re.UNICODE).strip("_")
    return f"#{tok}" if tok else ""


def enforce_title_with_emojis_and_1_to_3_tags(title: str, tags_pool: list[str] | None = None, fallback_title: str = "سر الغرفة المظلمة") -> str:
    raw = str(title or "")
    emojis = re.findall(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", raw)
    title_tags = [clean_tag_token(t) for t in re.findall(r"#[^\s#]+", raw)]
    title_tags = [t for t in title_tags if t][:3]

    if not title_tags:
        for cand in (tags_pool or []):
            t = clean_tag_token(cand)
            if t and t.lower() not in [x.lower() for x in title_tags]:
                title_tags.append(t)
            if len(title_tags) >= 2:
                break
    if not title_tags:
        title_tags = ["#shorts", "#غموض"]

    no_tags = re.sub(r"#[^\s#]+", "", raw)
    clean_text = fix_arabic_spelling(strip_emotes_for_caption(no_tags))
    words = [w for w in clean_text.split() if w.strip()][:5]
    short_title = " ".join(words).strip() or fallback_title

    e1 = emojis[0] if len(emojis) >= 1 else "🕯️"
    e2 = emojis[1] if len(emojis) >= 2 else "👁️"

    combined = f"{short_title} {e1}{e2} {' '.join(title_tags[:3])}".strip()
    return combined[:95].strip()


def build_large_description_hashtags(raw_tags: list[str] | None, extra_keywords: list[str] | None = None, max_tags: int = 500) -> list[str]:
    base_pool = [
        "shorts", "pixelart", "غموض", "قصص_غموض", "ملفات_سرية", "رعب", "قصص_رعب",
        "تشويق", "قصة_قصيرة", "مغامرة", "أرشيف_سري", "ليالي_الغموض", "قصة_حقيقية",
        "حكايات", "تاريخ_غامض", "سر_خطير", "نهاية_صادمة", "ظلام", "أشباح", "ألغاز",
        "تحقيق", "قصة_اليوم", "ترند", "اكسبلور", "fyp", "viral", "mystery", "story",
        "creepy", "thriller", "darkstory", "pixel_art", "8bit", "16bit", "retro",
        "secretfiles", "unsolved", "scarystory", "horrorstories", "shortstory",
    ]
    seen: set[str] = set()
    result: list[str] = []

    for src in [*(raw_tags or []), *(extra_keywords or []), *base_pool]:
        t = clean_tag_token(src)
        if not t:
            continue
        low = t.lower()
        if low in seen:
            continue
        seen.add(low)
        result.append(t)
        if len(result) >= max_tags:
            break

    return result


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
    thumb_in = fix_arabic_spelling(strip_emotes_for_caption(payload.get("thumbnail_text", "")))
    beats_in = payload.get("beats", [])

    for b in beats_in:
        raw_speech = str(b.get("speech_text") or b.get("caption") or "")
        b["speech_text"] = normalize_brackets_in_speech_text(fix_arabic_spelling(raw_speech))
        raw_cap = str(b.get("caption") or strip_emotes_for_caption(raw_speech))
        b["caption"] = enrich_natural_pauses(fix_arabic_spelling(strip_emotes_for_caption(raw_cap)))

    title_in = enforce_title_with_emojis_and_1_to_3_tags(
        payload.get("title", ""),
        tags_pool=payload.get("title_hashtags") or payload.get("hashtags") or [],
    )

    review_prompt = f"""
راجع النصوص العربية التالية وصحح جميع الأخطاء الإملائية والصياغة غير الطبيعية وعلامات الترقيم دون تغيير عدد المقاطع ({len(beats_in)} مقطعاً).
اجعل الجمل متماسكة كقصة واحدة متصلة في نفس المكان والحدث، وأكثر الفواصل (،) وعلامات الترقيم الطبيعية لتنظيم التنفس.
أعد النتيجة بصيغة JSON فقط بنفس المفاتيح:
{{
  "title": "عنوان قصير جذاب (3 إلى 5 كلمات) مع إيموجيين اثنين ومن هاشتاج إلى 3 هاشتاجات فقط",
  "hook_questions": ["سؤال مشوق 1؟", "سؤال مشوق 2؟"],
  "video_points": ["نقطة 1", "نقطة 2", "نقطة 3"],
  "viral_keywords": ["كلمة 1", "كلمة 2", "كلمة 3", "كلمة 4", "كلمة 5"],
  "description_hashtags": ["#shorts", "#pixelart", "#غموض"],
  "thumbnail_text": "كلمتان",
  "captions": ["...", "..."]
}}

المدخلات:
{json.dumps({
    "title": title_in,
    "hook_questions": payload.get("hook_questions", []),
    "video_points": payload.get("video_points", []),
    "viral_keywords": payload.get("viral_keywords", []),
    "description_hashtags": payload.get("description_hashtags") or payload.get("hashtags") or [],
    "thumbnail_text": thumb_in,
    "captions": [b.get("caption", "") for b in beats_in]
}, ensure_ascii=False)}
"""
    raw_rev = call_llm_raw(review_prompt, temperature=0.2)
    if raw_rev:
        try:
            rev = extract_json(raw_rev)
            if rev.get("title"):
                payload["title"] = enforce_title_with_emojis_and_1_to_3_tags(
                    rev["title"],
                    tags_pool=payload.get("title_hashtags") or [],
                )
            if isinstance(rev.get("hook_questions"), list) and rev["hook_questions"]:
                payload["hook_questions"] = [fix_arabic_spelling(strip_emotes_for_caption(q)) for q in rev["hook_questions"] if fix_arabic_spelling(q)][:3]
            if isinstance(rev.get("video_points"), list) and rev["video_points"]:
                payload["video_points"] = [fix_arabic_spelling(strip_emotes_for_caption(p)) for p in rev["video_points"] if fix_arabic_spelling(p)][:4]
            if isinstance(rev.get("viral_keywords"), list) and rev["viral_keywords"]:
                payload["viral_keywords"] = [fix_arabic_spelling(strip_emotes_for_caption(k)) for k in rev["viral_keywords"] if fix_arabic_spelling(k)][:8]
            if isinstance(rev.get("description_hashtags"), list) and rev["description_hashtags"]:
                payload["description_hashtags"] = build_large_description_hashtags(
                    [*rev["description_hashtags"], *(payload.get("description_hashtags") or [])],
                    extra_keywords=payload.get("viral_keywords") or [],
                    max_tags=500,
                )
            if fix_arabic_spelling(rev.get("thumbnail_text", "")):
                payload["thumbnail_text"] = fix_arabic_spelling(strip_emotes_for_caption(rev["thumbnail_text"]))
            rev_caps = rev.get("captions", [])
            if isinstance(rev_caps, list) and len(rev_caps) == len(beats_in):
                for idx, cap in enumerate(rev_caps):
                    fixed_c = enrich_natural_pauses(fix_arabic_spelling(strip_emotes_for_caption(cap)))
                    if fixed_c:
                        beats_in[idx]["caption"] = fixed_c
                        old_speech = beats_in[idx].get("speech_text", "")
                        tags_found = [t for t in re.findall(r"\[[^\[\]]+\]", old_speech) if normalize_emote_name(t.strip("[]"))]
                        beats_in[idx]["speech_text"] = f"{fixed_c} {' '.join(tags_found)}".strip() if tags_found else fixed_c
        except Exception:
            pass

    payload["hook_questions"] = [fix_arabic_spelling(strip_emotes_for_caption(q)) for q in (payload.get("hook_questions") or []) if fix_arabic_spelling(q)][:3] or [
        "ماذا كان مكتوباً في الصفحة الأخيرة؟",
        "لماذا توقف الصوت فجأة داخل الغرفة؟",
    ]
    payload["video_points"] = [fix_arabic_spelling(strip_emotes_for_caption(p)) for p in (payload.get("video_points") or []) if fix_arabic_spelling(p)][:4] or [
        "دخول هادئ إلى غرفة الأرشيف في منتصف الليل",
        "اكتشاف دفتر قديم يحمل تفاصيل غير متوقعة",
        "انكشاف الحقيقة الغامضة في اللحظة الأخيرة",
    ]
    payload["viral_keywords"] = [fix_arabic_spelling(strip_emotes_for_caption(k)) for k in (payload.get("viral_keywords") or []) if fix_arabic_spelling(k)][:8] or [
        "ملفات سرية", "غموض", "بيكسل آرت", "قصة غامضة", "شورتس"
    ]
    payload["description_hashtags"] = build_large_description_hashtags(
        payload.get("description_hashtags") or payload.get("hashtags") or [],
        extra_keywords=payload["viral_keywords"],
        max_tags=500,
    )
    payload["title"] = enforce_title_with_emojis_and_1_to_3_tags(
        payload.get("title", ""),
        tags_pool=payload.get("title_hashtags") or payload["description_hashtags"][:3],
    )
    payload["thumbnail_text"] = fix_arabic_spelling(strip_emotes_for_caption(payload.get("thumbnail_text", ""))) or "ملف سري"

    for b in beats_in:
        b["caption"] = enrich_natural_pauses(fix_arabic_spelling(strip_emotes_for_caption(b.get("caption", ""))))
        b["speech_text"] = normalize_brackets_in_speech_text(b.get("speech_text") or b["caption"])

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
    if now_hour < 12:
        return 1
    if now_hour < 18:
        return 2
    return 3


def call_llm_for_story(prompt: str) -> dict | None:
    raw = call_llm_raw(prompt, temperature=0.85)
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
    run_id = uuid.uuid4().hex[:4]
    idx = len(history) + slot
    env_kind = "library"
    time_of_day = "night"
    beats = [
        {"caption": "في منتصف الليل، دخلت غرفة الأرشيف القديمة، والهدوء يعم المكان.", "speech_text": "في منتصف الليل، دخلت غرفة الأرشيف القديمة، والهدوء يعم المكان.", "env": env_kind, "time": time_of_day, "mood": "neutral", "action": "walk", "prop": "none", "has_shadow": False},
        {"caption": "كانت المصابيح منطفئة، لكن ضوءاً خافتاً ظهر من خلف المكتب الخشبي.", "speech_text": "كانت المصابيح منطفئة، لكن ضوءاً خافتاً ظهر من خلف المكتب الخشبي... [whispering]", "env": env_kind, "time": time_of_day, "mood": "curious", "action": "point", "prop": "idea", "has_shadow": False},
        {"caption": "اقتربت ببطء، فسمعت صوت نقر خفيف يشبه آلة كاتبة قديمة.", "speech_text": "اقتربت ببطء، فسمعت صوت نقر خفيف، يشبه آلة كاتبة قديمة.", "env": env_kind, "time": time_of_day, "mood": "surprised", "action": "alert", "prop": "exclamation", "has_shadow": False},
        {"caption": "لم يكن في الغرفة أحد، ومع ذلك، كانت الحروف تطبع نفسها بهدوء.", "speech_text": "لم يكن في الغرفة أحد، ومع ذلك، كانت الحروف تطبع نفسها بهدوء... [nervous]", "env": env_kind, "time": time_of_day, "mood": "curious", "action": "point", "prop": "none", "has_shadow": False},
        {"caption": "توقفت عند الطاولة، وقرأت سطراً يصف خطواتي داخل الغرفة.", "speech_text": "توقفت عند الطاولة، وقرأت سطراً، يصف خطواتي داخل الغرفة.", "env": env_kind, "time": time_of_day, "mood": "surprised", "action": "point", "prop": "idea", "has_shadow": False},
        {"caption": "توقف نفسي فجأة، حين رأيت ظلاً يتحرك على الجدار المقابل.", "speech_text": "توقف نفسي فجأة، حين رأيت ظلاً، يتحرك على الجدار المقابل... [gasping]", "env": env_kind, "time": time_of_day, "mood": "shocked", "action": "alert", "prop": "exclamation", "has_shadow": True},
        {"caption": "كان الظل يقف عند الباب، دون أن يصدر أي صوت خطوات.", "speech_text": "كان الظل يقف عند الباب، دون أن يصدر، أي صوت خطوات. [whispering]", "env": env_kind, "time": time_of_day, "mood": "shocked", "action": "alert", "prop": "exclamation", "has_shadow": True},
        {"caption": "أشار الظل نحو السطر الأخير، ثم تراجع خطوة إلى الظلام.", "speech_text": "أشار الظل نحو السطر الأخير، ثم تراجع خطوة، إلى الظلام.", "env": env_kind, "time": time_of_day, "mood": "surprised", "action": "point", "prop": "none", "has_shadow": True},
        {"caption": "اقتربت لأقرأ ما كُتب، فوجدت اسمي مكتوباً بوضوح.", "speech_text": "اقتربت لأقرأ ما كُتب، فوجدت اسمي، مكتوباً بوضوح... [gasping]", "env": env_kind, "time": time_of_day, "mood": "shocked", "action": "alert", "prop": "exclamation", "has_shadow": False},
        {"caption": "أخذت نفساً طويلاً، وحاولت فهم سبب وجود اسمي في هذا الملف.", "speech_text": "أخذت نفساً طويلاً، وحاولت فهم سبب وجود اسمي، في هذا الملف. [sighing]", "env": env_kind, "time": time_of_day, "mood": "curious", "action": "walk", "prop": "speech", "has_shadow": False},
        {"caption": "في تلك اللحظة، انطفأ ضوء النافذة، وخيم صمت ثقيل على المكان.", "speech_text": "في تلك اللحظة، انطفأ ضوء النافذة، وخيم صمت ثقيل، على المكان.", "env": env_kind, "time": "fog", "mood": "shocked", "action": "alert", "prop": "exclamation", "has_shadow": True},
        {"caption": "سمعت همساً خافتاً خلف أذني، يقول إن الباب أُغلق من الخارج.", "speech_text": "سمعت همساً خافتاً خلف أذني، يقول إن الباب، أُغلق من الخارج... [whispering]", "env": env_kind, "time": "fog", "mood": "shocked", "action": "alert", "prop": "speech", "has_shadow": True},
        {"caption": "عدت إلى الورقة مرة أخرى، فوجدت السطر الأخير قد اختفى.", "speech_text": "عدت إلى الورقة مرة أخرى، فوجدت السطر الأخير، قد اختفى.", "env": env_kind, "time": "night", "mood": "shocked", "action": "point", "prop": "exclamation", "has_shadow": False},
        {"caption": "أدركت حينها، أن القصة لم تبدأ اليوم، بل كانت تنتظر وصولي.", "speech_text": "أدركت حينها، أن القصة لم تبدأ اليوم، بل كانت تنتظر وصولي... [sighing]", "env": env_kind, "time": "night", "mood": "surprised", "action": "alert", "prop": "none", "has_shadow": True},
    ]
    return {
        "title": f"سر الأرشيف #{idx}{run_id} 🕯️👁️ #shorts #غموض",
        "hook_questions": [
            "كيف كُتب اسمي داخل غرفة الأرشيف المغلقة؟",
            "ماذا حدث عندما انطفأ الضوء في اللحظة الأخيرة؟",
        ],
        "video_points": [
            "الدخول إلى غرفة الأرشيف ليلاً وسماع نقر غامض",
            "قراءة سطور تصف الأحداث قبل وقوعها وظهور الظل",
            "اكتشاف اختفاء السطر الأخير وفهم حقيقة الملف",
        ],
        "viral_keywords": ["ملفات سرية", "غموض", "بيكسل آرت", "قصة غامضة", "أرشيف الليل", "شورتس"],
        "description_hashtags": build_large_description_hashtags(["#shorts", "#pixelart", "#غموض"], max_tags=500),
        "thumbnail_text": "ملف سري",
        "beats": beats,
    }


def generate_unique_story_payload(slot: int, history: list[dict]) -> dict:
    now_utc = datetime.now(timezone.utc)
    recent_titles = [fix_arabic_spelling(h.get("title", "")) for h in history[-30:] if h.get("title")]
    avoid_block = "\n".join(f"- {t}" for t in recent_titles) if recent_titles else "- لا توجد قصص سابقة بعد"

    for attempt in range(4):
        nonce = uuid.uuid4().hex[:8]
        prompt = f"""
ابتكر قصة غموض عربية واحدة متماسكة ومترابطة من البداية إلى النهاية لقناة يوتيوب شورتس بإملاء عربي فصيح وصياغة طبيعية سليمة جداً.
معرف الجلسة: {nonce} | المحاولة: {attempt + 1} | التاريخ: {now_utc.strftime("%Y-%m-%d %H:%M")} UTC | الفترة: #{slot}.

شروط الترابط واللغة والصوت:
1. قصة واحدة متصلة في نفس المكان والحدث؛ كل جملة تكمل الجملة السابقة منطقياً ولا تقفز فجأة إلى مكان أو فكرة بلا تمهيد.
2. تجنب الكلمات الركيكة أو غير المألوفة؛ اكتب بلغة عربية واضحة ومؤثرة.
3. نبرة السرد يجب أن تكون هادئة وغامضة وموزونة (ليست فرحة ولا صاخبة ولا حماسية مبالغاً فيها).
4. أكثر من الفواصل (،) وعلامات التوقف الطبيعية (...) داخل الجمل وعند المواضع التي يحتاج فيها القارئ إلى وقفة أو تنفس ليبدو الإلقاء طبيعياً وإنسانياً دون الاعتماد على علامات التعجب فقط.
5. في "speech_text" استخدم فقط وسوم هادئة أو غامضة عند الحاجة مثل [whispering] أو [sighing] أو [nervous] (ولا تستخدم [laughing] أو [excited]).
6. اجعل "caption" نصاً عربياً صافياً بدون أي وسوم [...] أو <...>.
7. "title": عنوان قصير جذاب (3 إلى 5 كلمات) مع إيموجيين اثنين، ويحتوي في نهايته على من هاشتاج واحد إلى 3 هاشتاجات فقط.
8. "hook_questions": سؤالان أو ثلاثة أسئلة مشوقة.
9. "video_points": 3 إلى 4 نقاط تلخص أحداث الفيديو.
10. "viral_keywords": 5 إلى 8 كلمات فيروسية.
11. "description_hashtags": قائمة غنية بالهاشتاغات المتنوعة (حتى 500 هاشتاج للقسم الخامس من الوصف).

ممنوع تكرار هذه العناوين أو الأفكار السابقة:
{avoid_block}

أعد النتيجة بصيغة JSON فقط بهذا الشكل:
{{
  "title": "عنوان قصير جذاب 🕯️👁️ #shorts #غموض",
  "hook_questions": ["سؤال مشوق؟", "سؤال آخر؟"],
  "video_points": ["نقطة 1", "نقطة 2", "نقطة 3"],
  "viral_keywords": ["ملفات سرية", "غموض", "بيكسل آرت", "قصة غامضة", "شورتس"],
  "description_hashtags": ["#shorts", "#pixelart", "#غموض", "#قصص_غموض", "#ملفات_سرية"],
  "thumbnail_text": "كلمتان",
  "beats": [
    {{
      "caption": "جملة مترابطة فيها فواصل طبيعية مثل: في بداية الليل، دخلت المكان، وكان كل شيء هادئاً.",
      "speech_text": "نفس الجملة مع فواصل وعلامات توقف ووسم هادئ عند الحاجة",
      "env": "library",
      "time": "night",
      "mood": "neutral",
      "action": "walk",
      "prop": "none",
      "has_shadow": false
    }}
  ]
}}

القواعد الصارمة:
- عدد المقاطع (beats) يجب أن يكون 13 إلى 15 مقطعاً مترابطاً.
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
    section_1_title = fix_arabic_spelling(strip_emotes_for_caption(payload.get("title", "")))

    points = [fix_arabic_spelling(strip_emotes_for_caption(p)) for p in (payload.get("video_points") or []) if fix_arabic_spelling(p)]
    section_2_points = "\n".join(f"• {p}" for p in points)

    questions = [fix_arabic_spelling(strip_emotes_for_caption(q)) for q in (payload.get("hook_questions") or []) if fix_arabic_spelling(q)]
    section_3_questions = "\n".join(f"؟ {q.lstrip('؟?')}" for q in questions)

    keywords = [fix_arabic_spelling(strip_emotes_for_caption(k)) for k in (payload.get("viral_keywords") or []) if fix_arabic_spelling(k)]
    section_4_keywords = " • ".join(keywords)

    all_tags = build_large_description_hashtags(
        payload.get("description_hashtags") or payload.get("hashtags") or [],
        extra_keywords=keywords,
        max_tags=500,
    )

    header_part = (
        f"1. عنوان المقطع:\n{section_1_title}\n\n"
        f"2. نقاط الفيديو:\n{section_2_points}\n\n"
        f"3. أسئلة تجذب الانتباه:\n{section_3_questions}\n\n"
        f"4. كلمات فيروسية:\n{section_4_keywords}\n\n"
        f"5. الهاشتاغات:\n"
    )
    remaining_chars = max(200, 4880 - len(header_part))
    tags_chunks: list[str] = []
    used_len = 0
    for tag in all_tags:
        add_len = len(tag) + 1
        if used_len + add_len > remaining_chars:
            break
        tags_chunks.append(tag)
        used_len += add_len

    return (header_part + " ".join(tags_chunks)).strip()[:4900]


def px_rect(draw: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int, fill, outline=None) -> None:
    draw.rectangle([x0 * PX, y0 * PX, (x1 + 1) * PX - 1, (y1 + 1) * PX - 1], fill=fill, outline=outline)


def draw_dynamic_scene(draw: ImageDraw.ImageDraw, env: str, time_of_day: str, frame_idx: int, seed_offset: int) -> None:
    rng = random.Random(seed_offset + (frame_idx // 5))

    if time_of_day == "day":
        sky_top, sky_bot, wall_c, floor_c = (112, 186, 240), (176, 224, 250), (218, 206, 188), (146, 128, 106)
    elif time_of_day == "dusk":
        sky_top, sky_bot, wall_c, floor_c = (56, 36, 78), (206, 102, 78), (94, 72, 92), (62, 50, 68)
    elif time_of_day == "fog":
        sky_top, sky_bot, wall_c, floor_c = (78, 92, 104), (138, 150, 158), (82, 86, 96), (64, 66, 74)
    else:
        sky_top, sky_bot, wall_c, floor_c = (24, 28, 56), (44, 46, 82), (76, 68, 98), (54, 48, 72)

    for y in range(0, 56, 4):
        t = y / 56.0
        col = (
            int(sky_top[0] * (1 - t) + sky_bot[0] * t),
            int(sky_top[1] * (1 - t) + sky_bot[1] * t),
            int(sky_top[2] * (1 - t) + sky_bot[2] * t),
        )
        px_rect(draw, 0, y, GRID_W - 1, y + 3, col)

    if time_of_day in ("night", "dusk", "fog"):
        mx = 92
        px_rect(draw, mx, 10, mx + 10, 20, (248, 242, 196), outline=(196, 188, 136))
        px_rect(draw, mx + 2, 12, mx + 4, 14, (226, 218, 172))
        for i in range(18):
            sx, sy = rng.randint(4, GRID_W - 6), rng.randint(4, 38)
            twinkle = (245, 248, 255) if ((i + frame_idx) % 4 != 0) else (150, 165, 210)
            px_rect(draw, sx, sy, sx + 1, sy + 1, twinkle)

    cloud_shift = (frame_idx // 2) % 24
    for cx in range(-16 + cloud_shift, GRID_W + 16, 36):
        cy = 18 + (abs(cx) % 10)
        px_rect(draw, cx, cy + 2, cx + 14, cy + 6, (186, 192, 218))
        px_rect(draw, cx + 3, cy, cx + 10, cy + 5, (214, 218, 236))

    px_rect(draw, 0, 56, GRID_W - 1, 148, wall_c)
    for wy in range(60, 146, 8):
        shade = (max(0, wall_c[0] - 10), max(0, wall_c[1] - 10), max(0, wall_c[2] - 10))
        px_rect(draw, 0, wy, GRID_W - 1, wy, shade)

    win_x, win_y = 82, 66
    px_rect(draw, win_x, win_y, win_x + 24, win_y + 26, (44, 38, 58), outline=(20, 18, 28))
    px_rect(draw, win_x + 2, win_y + 2, win_x + 11, win_y + 12, (62, 78, 122))
    px_rect(draw, win_x + 13, win_y + 2, win_x + 22, win_y + 12, (62, 78, 122))
    px_rect(draw, win_x + 2, win_y + 14, win_x + 11, win_y + 24, (54, 68, 108))
    px_rect(draw, win_x + 13, win_y + 14, win_x + 22, win_y + 24, (54, 68, 108))
    px_rect(draw, win_x - 1, win_y + 26, win_x + 25, win_y + 28, (112, 86, 68), outline=(22, 18, 26))

    if env == "library":
        for sx in range(8, 72, 22):
            px_rect(draw, sx, 64, sx + 18, 146, (68, 44, 30), outline=(24, 16, 12))
            for ry in range(72, 140, 14):
                px_rect(draw, sx + 2, ry + 6, sx + 16, ry + 8, (48, 30, 20))
                for bx in range(sx + 2, sx + 15, 3):
                    bc = (176, 68, 58) if (bx % 6 == 0) else ((68, 124, 168) if (bx % 4 == 0) else (188, 148, 64))
                    px_rect(draw, bx, ry, bx + 2, ry + 5, bc)
        px_rect(draw, 14, 118, 46, 148, (96, 62, 38), outline=(24, 16, 12))
        px_rect(draw, 18, 108, 40, 118, (64, 122, 98), outline=(20, 24, 22))
        px_rect(draw, 22, 102, 36, 108, (232, 228, 214))
    elif env == "lighthouse":
        px_rect(draw, 84, 30, 102, 148, (226, 228, 236), outline=(22, 24, 34))
        for ry in range(38, 142, 18):
            px_rect(draw, 84, ry, 102, ry + 8, (196, 62, 62))
        px_rect(draw, 86, 22, 100, 30, (255, 228, 112), outline=(36, 32, 24))
    elif env == "forest":
        for tx in range(8, GRID_W - 10, 24):
            px_rect(draw, tx + 3, 86, tx + 7, 148, (78, 50, 32), outline=(22, 16, 12))
            px_rect(draw, tx - 3, 62, tx + 13, 88, (38, 104, 62), outline=(18, 34, 24))
            px_rect(draw, tx, 50, tx + 10, 66, (52, 126, 74))

    px_rect(draw, 0, 148, GRID_W - 1, GRID_H - 1, floor_c)
    for fy in range(152, GRID_H, 10):
        line_c = (max(0, floor_c[0] - 12), max(0, floor_c[1] - 12), max(0, floor_c[2] - 12))
        px_rect(draw, 0, fy, GRID_W - 1, fy, line_c)

    if time_of_day == "fog":
        shift = (frame_idx * 2) % 18
        for fy in range(118, 152, 12):
            px_rect(draw, shift, fy, GRID_W - 18 + (shift // 2), fy + 3, (198, 206, 214))


def draw_character(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    step_phase: int,
    mood: str = "neutral",
    action: str = "walk",
    shirt_color=(98, 118, 214),
    is_shadow: bool = False,
) -> None:
    outline = (16, 16, 24)
    if is_shadow:
        hair_c = (24, 24, 38)
        skin_c = (32, 32, 48)
        shirt_c = (22, 22, 36)
        pants_c = (18, 18, 30)
        eye_c = (145, 165, 240)
        px_rect(draw, cx - 8, cy + 5, cx + 8, cy + 7, (14, 14, 24))
    else:
        hair_c = (38, 34, 48)
        skin_c = (242, 196, 158)
        shirt_c = shirt_color
        pants_c = (46, 56, 86)
        eye_c = (20, 20, 28)
        px_rect(draw, cx - 8, cy + 5, cx + 8, cy + 7, (24, 22, 34))

    px_rect(draw, cx - 7, cy - 34, cx + 7, cy - 18, skin_c, outline=outline)
    px_rect(draw, cx - 8, cy - 28, cx - 7, cy - 24, skin_c, outline=outline)
    px_rect(draw, cx + 7, cy - 28, cx + 8, cy - 24, skin_c, outline=outline)
    px_rect(draw, cx - 7, cy - 36, cx + 7, cy - 28, hair_c, outline=outline)
    px_rect(draw, cx - 7, cy - 29, cx - 4, cy - 27, hair_c)

    if mood == "shocked":
        px_rect(draw, cx - 5, cy - 25, cx - 2, cy - 21, (255, 255, 255), outline=outline)
        px_rect(draw, cx + 2, cy - 25, cx + 5, cy - 21, (255, 255, 255), outline=outline)
        px_rect(draw, cx - 4, cy - 24, cx - 3, cy - 22, eye_c)
        px_rect(draw, cx + 3, cy - 24, cx + 4, cy - 22, eye_c)
        px_rect(draw, cx - 2, cy - 20, cx + 2, cy - 18, (46, 22, 26))
    elif mood == "surprised":
        px_rect(draw, cx - 4, cy - 25, cx - 2, cy - 22, eye_c)
        px_rect(draw, cx + 2, cy - 25, cx + 4, cy - 22, eye_c)
        px_rect(draw, cx - 1, cy - 20, cx + 1, cy - 19, (46, 22, 26))
    elif mood == "happy":
        px_rect(draw, cx - 5, cy - 24, cx - 2, cy - 23, eye_c)
        px_rect(draw, cx + 2, cy - 24, cx + 5, cy - 23, eye_c)
        px_rect(draw, cx - 3, cy - 20, cx + 3, cy - 19, (186, 76, 76))
    else:
        px_rect(draw, cx - 4, cy - 24, cx - 2, cy - 22, eye_c)
        px_rect(draw, cx + 2, cy - 24, cx + 4, cy - 22, eye_c)
        px_rect(draw, cx - 2, cy - 20, cx + 2, cy - 19, (92, 58, 52))

    px_rect(draw, cx - 7, cy - 17, cx + 7, cy - 4, shirt_c, outline=outline)
    px_rect(draw, cx - 2, cy - 17, cx + 2, cy - 15, (238, 240, 248))

    if action == "point":
        px_rect(draw, cx - 10, cy - 16, cx - 8, cy - 6, shirt_c, outline=outline)
        px_rect(draw, cx + 8, cy - 18, cx + 14, cy - 15, shirt_c, outline=outline)
        px_rect(draw, cx + 14, cy - 18, cx + 16, cy - 16, skin_c, outline=outline)
    elif action == "wave":
        w = -2 if (step_phase % 2 == 0) else 0
        px_rect(draw, cx - 10, cy - 16, cx - 8, cy - 6, shirt_c, outline=outline)
        px_rect(draw, cx + 8, cy - 22 + w, cx + 10, cy - 11 + w, shirt_c, outline=outline)
        px_rect(draw, cx + 8, cy - 24 + w, cx + 10, cy - 22 + w, skin_c, outline=outline)
    elif action == "alert":
        px_rect(draw, cx - 10, cy - 19, cx - 8, cy - 11, shirt_c, outline=outline)
        px_rect(draw, cx + 8, cy - 19, cx + 10, cy - 11, shirt_c, outline=outline)
    else:
        px_rect(draw, cx - 10, cy - 16, cx - 8, cy - 6, shirt_c, outline=outline)
        px_rect(draw, cx + 8, cy - 16, cx + 10, cy - 6, shirt_c, outline=outline)

    step_l = -1 if (step_phase % 2 == 0) else 1
    step_r = 1 if (step_phase % 2 == 0) else -1
    px_rect(draw, cx - 6, cy - 3, cx - 1, cy + 4 + step_l, pants_c, outline=outline)
    px_rect(draw, cx + 1, cy - 3, cx + 6, cy + 4 + step_r, pants_c, outline=outline)
    px_rect(draw, cx - 6, cy + 4 + step_l, cx - 1, cy + 5 + step_l, (28, 26, 34))
    px_rect(draw, cx + 1, cy + 4 + step_r, cx + 6, cy + 5 + step_r, (28, 26, 34))


def draw_prop(draw: ImageDraw.ImageDraw, cx: int, cy: int, prop: str, frame_idx: int) -> None:
    bob = (frame_idx % 4) // 2
    py = cy - 48 - bob
    if prop == "exclamation":
        px_rect(draw, cx - 1, py, cx + 1, py + 7, (245, 68, 58), outline=(20, 16, 20))
        px_rect(draw, cx - 1, py + 9, cx + 1, py + 11, (245, 68, 58), outline=(20, 16, 20))
    elif prop == "idea":
        px_rect(draw, cx - 3, py, cx + 4, py + 6, (255, 224, 72), outline=(25, 22, 20))
        px_rect(draw, cx - 2, py + 7, cx + 3, py + 9, (186, 190, 200))
    elif prop == "speech":
        px_rect(draw, cx - 7, py, cx + 8, py + 7, (245, 248, 255), outline=(20, 22, 30))
        for dx in [-3, 0, 3]:
            px_rect(draw, cx + dx, py + 3, cx + dx, py + 5, (35, 38, 52))
    elif prop == "heart":
        px_rect(draw, cx - 4, py + 1, cx + 5, py + 5, (238, 64, 98))
        px_rect(draw, cx - 3, py, cx, py + 1, (238, 64, 98))
        px_rect(draw, cx + 1, py, cx + 4, py + 1, (238, 64, 98))
        px_rect(draw, cx - 2, py + 6, cx + 3, py + 7, (238, 64, 98))
        px_rect(draw, cx - 1, py + 8, cx + 2, py + 9, (238, 64, 98))


def apply_after_effects_polish(img: Image.Image, char_x: int, char_y: int, mood: str, frame_idx: int, num_frames: int, story_seed: int) -> Image.Image:
    glow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow_layer)
    gx = char_x * PX + PX // 2
    gy = (char_y - 14) * PX
    pulse = int(math.sin((frame_idx / max(num_frames, 1)) * math.pi * 2) * 24)
    glow_color = (255, 206, 118, max(24, 52 + pulse)) if mood in ("shocked", "surprised") else (130, 185, 255, max(18, 38 + pulse))
    gdraw.ellipse([gx - 220, gy - 220, gx + 220, gy + 220], fill=glow_color)
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=60))

    part_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(part_layer)
    rng = random.Random(story_seed + 999)
    for i in range(16):
        bx = rng.randint(60, W - 60)
        by = rng.randint(180, 1320)
        dx = int(math.sin(frame_idx * 0.12 + i) * 26)
        dy = int(math.cos(frame_idx * 0.09 + i) * 20)
        alpha = 40 + ((i * 7 + frame_idx * 5) % 50)
        pdraw.ellipse([bx + dx, by + dy, bx + dx + 7, by + dy + 7], fill=(245, 240, 220, alpha))
    part_layer = part_layer.filter(ImageFilter.GaussianBlur(radius=2))

    vig_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    vdraw = ImageDraw.Draw(vig_layer)
    vdraw.rectangle([0, 0, W, 140], fill=(8, 10, 18, 110))
    vdraw.rectangle([0, H - 180, W, H], fill=(8, 10, 18, 125))
    vdraw.rectangle([0, 0, 55, H], fill=(8, 10, 18, 75))
    vdraw.rectangle([W - 55, 0, W, H], fill=(8, 10, 18, 75))
    vig_layer = vig_layer.filter(ImageFilter.GaussianBlur(radius=28))

    composed = Image.alpha_composite(img.convert("RGBA"), glow_layer)
    composed = Image.alpha_composite(composed, part_layer)
    composed = Image.alpha_composite(composed, vig_layer)

    progress = frame_idx / max(num_frames - 1, 1)
    ease = 0.5 - 0.5 * math.cos(progress * math.pi)
    zoom_factor = 1.0 + 0.04 * ease
    crop_w = int(W / zoom_factor)
    crop_h = int(H / zoom_factor)

    shake_x = int(math.sin(frame_idx * 1.9) * 7) if mood == "shocked" else 0
    shake_y = int(math.cos(frame_idx * 2.3) * 5) if mood == "shocked" else 0

    left = max(0, min(W - crop_w, (W - crop_w) // 2 + shake_x))
    top = max(0, min(H - crop_h, (H - crop_h) // 2 + shake_y))
    cropped = composed.crop((left, top, left + crop_w, top + crop_h))
    return cropped.resize((W, H), Image.Resampling.LANCZOS).convert("RGB")


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


def draw_outlined_text(
    draw: ImageDraw.ImageDraw,
    pos: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill=(255, 255, 255),
    stroke_fill=(12, 14, 24),
    stroke_width: int = 5,
) -> None:
    x, y = pos
    for dx in range(-stroke_width, stroke_width + 1):
        for dy in range(-stroke_width, stroke_width + 1):
            if dx == 0 and dy == 0:
                continue
            if (dx * dx + dy * dy) <= (stroke_width * stroke_width + 2):
                draw.text((x + dx, y + dy), text, font=font, fill=stroke_fill)
    draw.text((x, y + 3), text, font=font, fill=(6, 8, 14))
    draw.text((x, y), text, font=font, fill=fill)


def build_auto_caption_chunks(text: str, num_frames: int) -> list[tuple[int, int, list[str]]]:
    words = [w for w in text.split() if w.strip()]
    if not words:
        return []

    chunks: list[str] = []
    cur: list[str] = []
    for w in words:
        cur.append(w)
        if len(cur) >= 3 or w.endswith((".", "!", "؟", "،", ":")):
            chunks.append(" ".join(cur))
            cur = []
    if cur:
        chunks.append(" ".join(cur))

    n_chunks = len(chunks)
    result: list[tuple[int, int, list[str]]] = []
    for i, ch in enumerate(chunks):
        start_f = int(round((i / n_chunks) * num_frames))
        end_f = int(round(((i + 1) / n_chunks) * num_frames))
        if end_f <= start_f:
            end_f = min(num_frames, start_f + 1)
        result.append((start_f, end_f, ch.split()))
    return result


def render_beat_frames(beat: dict, beat_idx: int, duration_sec: float, story_seed: int, fps: int = 12) -> list[Path]:
    num_frames = max(int(round(duration_sec * fps)), 10)
    env = str(beat.get("env", "library")).strip()
    time_of_day = str(beat.get("time", "night")).strip()
    mood = str(beat.get("mood", "neutral")).strip()
    action = str(beat.get("action", "walk")).strip()
    prop = str(beat.get("prop", "none")).strip()
    has_shadow = bool(beat.get("has_shadow", True))
    caption_raw = fix_arabic_spelling(strip_emotes_for_caption(beat.get("caption", "")))

    shirt_palette = [
        (98, 118, 214),
        (212, 94, 86),
        (70, 166, 136),
        (216, 150, 64),
        (156, 96, 210),
    ]
    shirt_color = shirt_palette[(story_seed + beat_idx) % len(shirt_palette)]
    font_cap = load_arabic_font(62)
    chunks_timeline = build_auto_caption_chunks(caption_raw, num_frames)
    out_frames: list[Path] = []

    for f_i in range(num_frames):
        img = Image.new("RGB", (W, H), (18, 18, 32))
        draw = ImageDraw.Draw(img)

        draw_dynamic_scene(draw, env, time_of_day, f_i, story_seed + beat_idx * 37)
        char_y = 152

        drift = int(math.sin((f_i / max(num_frames, 1)) * math.pi) * 8)
        char_x = 34 + ((story_seed + beat_idx * 7) % 20) + drift
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
            shadow_x = min(char_x + 32 - (drift // 2), GRID_W - 14)
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

        img = apply_after_effects_polish(img, char_x, char_y, mood, f_i, num_frames, story_seed + beat_idx * 13)
        draw = ImageDraw.Draw(img)

        active_words: list[str] = []
        active_idx = 0
        for sf, ef, w_list in chunks_timeline:
            if sf <= f_i < ef:
                active_words = w_list
                span = max(ef - sf, 1)
                rel = (f_i - sf) / span
                active_idx = min(len(w_list) - 1, int(rel * len(w_list)))
                break
        if not active_words and chunks_timeline:
            active_words = chunks_timeline[-1][2]
            active_idx = len(active_words) - 1

        if active_words:
            full_line = " ".join(active_words)
            bbox_all = draw.textbbox((0, 0), full_line, font=font_cap)
            line_h = max(bbox_all[3] - bbox_all[1], 68)
            base_y = 1430

            cur_word = active_words[active_idx]
            w_box = draw.textbbox((0, 0), cur_word, font=font_cap)
            pw = (w_box[2] - w_box[0]) + 44
            ph = line_h + 18
            px0 = (W - pw) // 2
            py0 = base_y + line_h + 20
            draw.rounded_rectangle([px0 + 4, py0 + 6, px0 + pw + 4, py0 + ph + 6], radius=18, fill=(8, 10, 18))
            draw.rounded_rectangle([px0, py0, px0 + pw, py0 + ph], radius=18, fill=(255, 214, 72), outline=(18, 20, 30), width=3)
            draw_outlined_text(
                draw,
                ((W - (w_box[2] - w_box[0])) // 2, py0 + 6),
                cur_word,
                font=font_cap,
                fill=(18, 20, 32),
                stroke_fill=(255, 236, 150),
                stroke_width=2,
            )

            lx = (W - (bbox_all[2] - bbox_all[0])) // 2
            draw_outlined_text(
                draw,
                (lx, base_y),
                full_line,
                font=font_cap,
                fill=(255, 255, 255),
                stroke_fill=(10, 12, 22),
                stroke_width=5,
            )

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


def get_fish_api_keys() -> list[str]:
    keys: list[str] = []
    for i in range(1, 5):
        env_val = os.getenv(f"FISH_API_KEY_{i}", "").strip()
        if env_val:
            keys.append(env_val)
    for dk in DEFAULT_FISH_KEYS:
        if dk and dk not in keys:
            keys.append(dk)
    return keys


def try_fish_audio_tts(fish_script: str, audio_path: Path) -> bool:
    for idx, api_key in enumerate(get_fish_api_keys(), start=1):
        try:
            resp = requests.post(
                "https://api.fish.audio/v1/tts",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "model": "s2.1-pro-free",
                },
                json={"text": fish_script, "format": "mp3"},
                timeout=90,
            )
            if resp.ok and resp.content and len(resp.content) > 1024:
                audio_path.write_bytes(resp.content)
                print(f"TTS succeeded using Fish Audio key #{idx} with emotes.")
                return True
            print(f"Fish Audio key #{idx} returned status {resp.status_code}; trying next...")
        except Exception as e:
            print(f"Fish Audio key #{idx} error: {e}; trying next...")
    return False


def try_google_ai_studio_tts(google_script: str, audio_path: Path) -> bool:
    google_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not google_key:
        return False
    models_to_try = [
        "gemini-3.8-flash-tts",
        "gemini-2.5-flash-preview-tts",
    ]
    for model_name in models_to_try:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={google_key}"
            prompt_text = (
                f"اقرأ النص التالي باللغة العربية بنبرة هادئة وغامضة ومتزنة، مع التزام الفواصل وعلامات الترقيم والتعبيرات داخل الأقواس الزاوية مثل <whispering> و<sighing> دون حماس مفرط أو غناء:\n\n{google_script}"
                if "3.8" in model_name
                else f"اقرأ هذه القصة بنبرة هادئة وغامضة وواضحة مع الوقف عند الفواصل وعلامات الترقيم دون غناء:\n\n{strip_emotes_for_caption(google_script)}"
            )
            payload = {
                "contents": [{"parts": [{"text": prompt_text}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": "Puck"
                            }
                        }
                    },
                },
            }
            r = requests.post(url, json=payload, timeout=90)
            if r.ok:
                parts = (
                    r.json()
                    .get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [])
                )
                for p in parts:
                    inline = p.get("inlineData") or p.get("inline_data")
                    if inline and inline.get("data"):
                        raw_audio = base64.b64decode(inline["data"])
                        tmp_audio = OUT_DIR / "google_tts_raw.bin"
                        tmp_audio.write_bytes(raw_audio)
                        mime = str(inline.get("mimeType") or inline.get("mime_type") or "")
                        if "pcm" in mime.lower() or "l16" in mime.lower():
                            subprocess.run(
                                [
                                    "ffmpeg",
                                    "-y",
                                    "-f",
                                    "s16le",
                                    "-ar",
                                    "24000",
                                    "-ac",
                                    "1",
                                    "-i",
                                    str(tmp_audio),
                                    "-c:a",
                                    "libmp3lame",
                                    "-b:a",
                                    "192k",
                                    str(audio_path),
                                ],
                                check=True,
                            )
                        else:
                            subprocess.run(
                                [
                                    "ffmpeg",
                                    "-y",
                                    "-i",
                                    str(tmp_audio),
                                    "-c:a",
                                    "libmp3lame",
                                    "-b:a",
                                    "192k",
                                    str(audio_path),
                                ],
                                check=True,
                            )
                        if audio_path.exists() and audio_path.stat().st_size > 1024:
                            print(f"TTS succeeded using Google AI Studio ({model_name}).")
                            return True
        except Exception as e:
            print(f"Google AI Studio TTS ({model_name}) error:", e)
    return False


async def generate_continuous_voice(beats: list[dict], audio_path: Path) -> list[float]:
    raw_lines = [
        normalize_brackets_in_speech_text(fix_arabic_spelling(b.get("speech_text") or b.get("caption", ""))) or "ملفات سرية..."
        for b in beats
    ]
    clean_lines = [strip_emotes_for_caption(line) or "ملفات سرية" for line in raw_lines]

    fish_script = " ... ".join(format_tts_for_provider(line, "fish") for line in raw_lines)
    google_script = " ... ".join(format_tts_for_provider(line, "google") for line in raw_lines)
    edge_script = " ... ".join(format_tts_for_provider(line, "edge") for line in raw_lines)

    if not try_fish_audio_tts(fish_script, audio_path):
        if not try_google_ai_studio_tts(google_script, audio_path):
            voice = "ar-SA-HamedNeural"
            comm = edge_tts.Communicate(text=edge_script, voice=voice, rate="-4%")
            with open(audio_path, "wb") as f:
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
            print("TTS succeeded using Edge-TTS fallback.")

    total_dur = get_audio_duration(audio_path)
    weights = [max(len(re.findall(r"\S+", line)), 1) for line in clean_lines]
    weight_sum = sum(weights) or len(clean_lines)
    return [max((w / weight_sum) * total_dur, 1.5) for w in weights]


def create_thumbnail(first_frame: Path, thumb_text: str, out_thumb: Path) -> None:
    im = Image.open(first_frame).convert("RGB").resize((W, H), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(im)
    draw.rectangle([0, 1540, W, H], fill=(12, 14, 24))
    clean_t = fix_arabic_spelling(strip_emotes_for_caption(thumb_text)) or "ملف سري"
    font_t = load_arabic_font(72)
    bbox = draw.textbbox((0, 0), clean_t, font=font_t)
    tw = bbox[2] - bbox[0]
    tx = (W - tw) // 2
    draw_outlined_text(draw, (tx, 1650), clean_t, font=font_t, fill=(255, 220, 72), stroke_fill=(10, 12, 20), stroke_width=4)
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
            "scale=1080:1920:flags=neighbor,eq=contrast=1.06:saturation=1.12,format=yuv420p",
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

    try:
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

    except Exception as e:
        err_msg = str(e)
        if "uploadLimitExceeded" in err_msg or "exceeded the number of videos they may upload" in err_msg:
            print("YouTube daily upload limit reached (uploadLimitExceeded). Video is saved locally at:", video_path)
            return None
        print("YouTube upload failed with error:", err_msg)
        return None


def main() -> None:
    slot = get_slot_number()
    history = load_history()
    payload = generate_unique_story_payload(slot, history)
    beats = payload.get("beats", [])[:15]
    story_seed = int(story_fingerprint(payload)[:8], 16)

    formatted_description = build_formatted_description(payload)
    meta = {
        "slot": slot,
        "title": payload.get("title", ""),
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

    video_id = upload_to_youtube(
        video_path=video_path,
        thumb_path=thumb_path,
        title=str(meta["title"]),
        description=formatted_description,
        keywords=payload.get("viral_keywords", []),
        privacy_status=PRIVACY_STATUS,
    )

    history.append(
        {
            "date_utc": datetime.now(timezone.utc).isoformat(),
            "title": meta["title"],
            "summary_text": " ".join(fix_arabic_spelling(b.get("caption", "")) for b in beats),
            "fingerprint": story_fingerprint(payload),
            "youtube_video_id": video_id,
        }
    )
    save_history(history)

    print("DONE! Generated:", video_path, thumb_path)
    print("\n--- DESCRIPTION ---\n" + formatted_description)


if __name__ == "__main__":
    main()
