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
import urllib.parse
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
ART_CACHE_DIR = OUT_DIR / "art_cache"
HISTORY_FILE = DATA_DIR / "stories_history.json"

OUT_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
ART_CACHE_DIR.mkdir(parents=True, exist_ok=True)

W, H = 1080, 1920
GRID_W, GRID_H = 270, 480
PX = 4
PRIVACY_STATUS = "public"

ALL_ENVIRONMENTS = [
    "mountain_pass",
    "desert_canyon",
    "misty_forest",
    "open_sea",
    "underground_cave",
    "ancient_ruins",
    "night_harbor",
    "old_archive",
    "abandoned_town",
    "twilight_plain",
]

STORY_THEMES = [
    "بعثة استكشافية اختفت وسط جبال عالية",
    "رسالة قديمة عُثر عليها داخل سفينة عالقة",
    "إشارة لاسلكية غامضة من محطة مهجورة",
    "خريطة أثرية تقود إلى كهف تحت الأرض",
    "ظاهرة ضوئية غير مفسرة في صحراء نائية",
    "سجل يوميات يعود لعقود في مدينة منسية",
    "رحلة ليلية عبر ممر جبلي قبل الفجر",
    "لغز ميناء قديم توقفت فيه الساعات",
]

DEFAULT_FISH_KEYS = [
    "sk-fish-6YUfu-lSRlfppfamSTY-NrEAM1Y-D6fp-hMFWbOIfzI",
    "sk-fish-6iO-qu3Rvu4riNr2yY321BirGup5LPvsu3_pwJNgApE",
    "sk-fish-3Oojt9SvyaueZKY-Tm38z6i6BT4wRCneQdcWHGL4HUg",
    "sk-fish-LGMwCDbpMZwCYU_bBXAYOD0V-UKy16Ut3iEfXZ9T3s8",
]

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
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if not s:
        return s
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


def enforce_title_with_emojis_and_1_to_3_tags(title: str, tags_pool: list[str] | None = None, fallback_title: str = "لغز الممر المفقود") -> str:
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
        title_tags = ["#shorts", "#pixelart"]

    no_tags = re.sub(r"#[^\s#]+", "", raw)
    clean_text = fix_arabic_spelling(strip_emotes_for_caption(no_tags))
    words = [w for w in clean_text.split() if w.strip()][:5]
    short_title = " ".join(words).strip() or fallback_title

    e1 = emojis[0] if len(emojis) >= 1 else "🧭"
    e2 = emojis[1] if len(emojis) >= 2 else "🌫️"

    combined = f"{short_title} {e1}{e2} {' '.join(title_tags[:3])}".strip()
    return combined[:95].strip()


def build_large_description_hashtags(raw_tags: list[str] | None, extra_keywords: list[str] | None = None, max_tags: int = 500) -> list[str]:
    base_pool = [
        "shorts", "pixelart", "غموض", "قصص_غموض", "ملفات_سرية", "استكشاف", "قصص_تاريخية",
        "تشويق", "قصة_قصيرة", "مغامرة", "أرشيف_سري", "ليالي_الغموض", "قصة_حقيقية",
        "حكايات", "تاريخ_غامض", "سر_خطير", "نهاية_صادمة", "ظلام", "ضباب", "ألغاز",
        "تحقيق", "قصة_اليوم", "ترند", "اكسبلور", "fyp", "viral", "mystery", "story",
        "creepy", "thriller", "darkstory", "pixel_art", "8bit", "16bit", "retro",
        "secretfiles", "unsolved", "expedition", "historymystery", "shortstory",
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


def call_llm_raw(prompt: str, temperature: float = 0.9) -> str | None:
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
        b["scene_prompt"] = re.sub(r"\s+", " ", str(b.get("scene_prompt") or "")).strip()

    title_in = enforce_title_with_emojis_and_1_to_3_tags(
        payload.get("title", ""),
        tags_pool=payload.get("title_hashtags") or payload.get("hashtags") or [],
    )

    review_prompt = f"""
راجع النصوص العربية التالية وصحح جميع الأخطاء الإملائية والصياغة وعلامات الترقيم دون تغيير عدد المقاطع ({len(beats_in)} مقطعاً).
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
        "أين اختفت هذه البعثة دون أثر؟",
        "ما السر الذي بقي مخفياً طوال هذه السنوات؟",
    ]
    payload["video_points"] = [fix_arabic_spelling(strip_emotes_for_caption(p)) for p in (payload.get("video_points") or []) if fix_arabic_spelling(p)][:4] or [
        "انطلاق الرحلة وسط ظروف طبيعية غامضة",
        "العثور على أثر يغيّر مسار البحث",
        "انكشاف الحقيقة في المشهد الأخير",
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
        if old_title and SequenceMatcher(None, cand_title, old_title).ratio() > 0.62:
            return True
        if old_text and SequenceMatcher(None, cand_text, old_text).ratio() > 0.55:
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
    raw = call_llm_raw(prompt, temperature=0.95)
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
    env_kind = ALL_ENVIRONMENTS[idx % len(ALL_ENVIRONMENTS)]
    theme = STORY_THEMES[idx % len(STORY_THEMES)]
    time_of_day = ["fog", "dusk", "night", "day"][idx % 4]

    beats = [
        {"caption": "في الصباح الباكر، تقدمت القافلة عبر الممر الجبلي الضيق، والضباب يلف التلال.", "speech_text": "في الصباح الباكر، تقدمت القافلة، عبر الممر الجبلي الضيق، والضباب يلف التلال.", "env": env_kind, "time": time_of_day, "mood": "neutral", "action": "walk", "prop": "none", "has_shadow": False, "scene_prompt": "detailed pixel art expedition marching along a misty rocky mountain ridge with dense clouds and dramatic lighting"},
        {"caption": "كان الهواء بارداً، ومع ذلك، ظهرت آثار أقدام قديمة على الصخر الرطب.", "speech_text": "كان الهواء بارداً، ومع ذلك، ظهرت آثار أقدام قديمة، على الصخر الرطب... [nervous]", "env": env_kind, "time": time_of_day, "mood": "curious", "action": "point", "prop": "idea", "has_shadow": False, "scene_prompt": "detailed pixel art explorer examining ancient footprints on wet stone path in misty highlands"},
        {"caption": "توقف المستكشف قليلاً، ورفع المنظار نحو المنحدر المقابل.", "speech_text": "توقف المستكشف قليلاً، ورفع المنظار، نحو المنحدر المقابل.", "env": env_kind, "time": time_of_day, "mood": "curious", "action": "point", "prop": "none", "has_shadow": False, "scene_prompt": "detailed pixel art traveler looking through brass spyglass toward a towering cliff and drifting fog"},
        {"caption": "لم يكن هناك سوى صخور عالية، لكن سحابة كثيفة هبطت فجأة.", "speech_text": "لم يكن هناك سوى صخور عالية، لكن سحابة كثيفة، هبطت فجأة... [whispering]", "env": env_kind, "time": "fog", "mood": "surprised", "action": "alert", "prop": "exclamation", "has_shadow": False, "scene_prompt": "detailed pixel art thick eerie cloud descending over mountain pass engulfing distant silhouettes"},
        {"caption": "تقدمت الصفوف الأمامية ببطء، والخطوات تتردد بين الجبال.", "speech_text": "تقدمت الصفوف الأمامية ببطء، والخطوات تتردد، بين الجبال.", "env": env_kind, "time": "fog", "mood": "neutral", "action": "walk", "prop": "none", "has_shadow": True, "scene_prompt": "detailed pixel art column of travelers walking slowly into thick mist with long shadows"},
        {"caption": "عندما دخلوا قلب السحابة، خفتت الأصوات تماماً، واختفى الضوء.", "speech_text": "عندما دخلوا قلب السحابة، خفتت الأصوات تماماً، واختفى الضوء... [gasping]", "env": env_kind, "time": "fog", "mood": "shocked", "action": "alert", "prop": "exclamation", "has_shadow": True, "scene_prompt": "detailed pixel art figures fading inside dense pale cloud bank with muted atmospheric colors"},
        {"caption": "حاول البعض النداء من الخلف، فلم يجب أحد سوى صدى بعيد.", "speech_text": "حاول البعض النداء من الخلف، فلم يجب أحد، سوى صدى بعيد. [sighing]", "env": env_kind, "time": "fog", "mood": "surprised", "action": "wave", "prop": "speech", "has_shadow": True, "scene_prompt": "detailed pixel art lone figure waving in misty valley under towering cliffs at dusk"},
        {"caption": "وبعد أن انقشع الضباب، كانت الأرض خالية إلا من معدات متروكة.", "speech_text": "وبعد أن انقشع الضباب، كانت الأرض خالية، إلا من معدات متروكة... [whispering]", "env": env_kind, "time": "dusk", "mood": "shocked", "action": "point", "prop": "exclamation", "has_shadow": False, "scene_prompt": "detailed pixel art empty rocky trail with abandoned expedition gear after mist clears at sunset"},
        {"caption": "سجلت التقارير الرسمية اختفاء المجموعة، دون أي دليل واضح.", "speech_text": "سجلت التقارير الرسمية اختفاء المجموعة، دون أي دليل واضح.", "env": env_kind, "time": "dusk", "mood": "curious", "action": "walk", "prop": "none", "has_shadow": False, "scene_prompt": "detailed pixel art investigator standing on overlook above quiet canyon during twilight"},
        {"caption": "ولم يُعثر على أثر لهم، حتى بعد انتهاء البحث في المنطقة.", "speech_text": "ولم يُعثر على أثر لهم، حتى بعد انتهاء البحث، في المنطقة... [sighing]", "env": env_kind, "time": "night", "mood": "surprised", "action": "alert", "prop": "none", "has_shadow": True, "scene_prompt": "detailed pixel art starry night sky over silent mountain ridge and quiet valley"},
        {"caption": "بقي المكان شاهداً على واحدة من أغرب الوقائع التي لم تُفسَّر.", "speech_text": "بقي المكان شاهداً، على واحدة من أغرب الوقائع، التي لم تُفسَّر.", "env": env_kind, "time": "night", "mood": "curious", "action": "point", "prop": "idea", "has_shadow": True, "scene_prompt": "detailed pixel art cinematic landscape of mysterious highland pass under moonlit clouds"},
    ]

    return {
        "title": f"اختفاء وسط الضباب #{idx}{run_id} 🧭🌫️ #shorts #pixelart",
        "hook_questions": [
            "كيف اختفت مجموعة كاملة وسط الضباب دون أثر؟",
            "ماذا بقي خلفهم عندما انقشعت السحابة؟",
        ],
        "video_points": [
            f"محور القصة: {theme}",
            "تقدم المجموعة عبر التضاريس وهبوط الضباب الكثيف",
            "انكشاف المشهد بعد انقشاع الضباب وبقاء اللغز قائماً",
        ],
        "viral_keywords": ["ملفات سرية", "غموض", "بيكسل آرت", "قصص تاريخية", "اختفاء غامض", "شورتس"],
        "description_hashtags": build_large_description_hashtags(["#shorts", "#pixelart", "#غموض"], max_tags=500),
        "thumbnail_text": "لغز غامض",
        "beats": beats,
    }


def generate_unique_story_payload(slot: int, history: list[dict]) -> dict:
    now_utc = datetime.now(timezone.utc)
    recent_titles = [fix_arabic_spelling(h.get("title", "")) for h in history[-35:] if h.get("title")]
    recent_summaries = [fix_arabic_spelling(h.get("summary_text", ""))[:120] for h in history[-15:] if h.get("summary_text")]
    avoid_block = "\n".join(f"- {t}" for t in recent_titles) if recent_titles else "- لا توجد قصص سابقة بعد"
    avoid_plots = "\n".join(f"- {s}" for s in recent_summaries) if recent_summaries else "- لا توجد حبكات سابقة"

    idx = len(history) + slot
    chosen_theme = STORY_THEMES[idx % len(STORY_THEMES)]
    chosen_env = ALL_ENVIRONMENTS[idx % len(ALL_ENVIRONMENTS)]

    for attempt in range(4):
        nonce = uuid.uuid4().hex[:8]
        prompt = f"""
ابتكر قصة غموض عربية واقعية ومشوقة ومتماسكة من البداية إلى النهاية لقناة يوتيوب شورتس بإملاء عربي فصيح وصياغة طبيعية سليمة جداً.
معرف الجلسة: {nonce} | المحاولة: {attempt + 1} | التاريخ: {now_utc.strftime("%Y-%m-%d %H:%M")} UTC | الفترة: #{slot}.
الفكرة المقترحة لهذه الحلقة: {chosen_theme}
البيئة الأساسية المقترحة: {chosen_env} (يمكن تنويعها حسب تسلسل الأحداث).

شروط الترابط والتنويع والصوت والرسوم:
1. قصة واحدة متماسكة؛ كل جملة تبني على الجملة التي قبلها وتقود إلى التي بعدها في تسلسل منطقي مشوق.
2. تجنب تكرار القصص أو الأماكن السابقة؛ اجعل الأحداث والشخصيات والمكان مختلفين بوضوح عن القصص السابقة.
3. نبرة السرد هادئة وواقعية وغامضة (ليست فرحة ولا حماسية مبالغاً فيها).
4. أكثر من الفواصل (،) وعلامات التوقف الطبيعية (...) داخل الجمل وعند المواضع التي يحتاج فيها القارئ إلى وقفة أو تنفس ليبدو الإلقاء طبيعياً.
5. في "speech_text" استخدم فقط وسوم هادئة أو غامضة عند الحاجة مثل [whispering] أو [sighing] أو [nervous] (ولا تستخدم [laughing] أو [excited]).
6. اجعل "caption" نصاً عربياً صافياً بدون أي وسوم [...] أو <...>.
7. أضف لكل مقطع "scene_prompt" بالإنجليزية يصف مشهداً بيكسلياً واقعياً غنياً بالتفاصيل (detailed pixel art scene, rich lighting, realistic environment and characters, vertical composition).
8. "title": عنوان قصير جذاب (3 إلى 5 كلمات) مع إيموجيين اثنين، ويحتوي في نهايته على من هاشتاج واحد إلى 3 هاشتاجات فقط.
9. "hook_questions": سؤالان أو ثلاثة أسئلة مشوقة.
10. "video_points": 3 إلى 4 نقاط تلخص أحداث الفيديو.
11. "viral_keywords": 5 إلى 8 كلمات فيروسية.
12. "description_hashtags": قائمة غنية بالهاشتاغات المتنوعة (حتى 500 هاشتاج للقسم الخامس من الوصف).

ممنوع تكرار هذه العناوين أو الحبكات السابقة:
{avoid_block}
{avoid_plots}

أعد النتيجة بصيغة JSON فقط بهذا الشكل:
{{
  "title": "عنوان قصير جذاب 🧭🌫️ #shorts #pixelart",
  "hook_questions": ["سؤال مشوق؟", "سؤال آخر؟"],
  "video_points": ["نقطة 1", "نقطة 2", "نقطة 3"],
  "viral_keywords": ["ملفات سرية", "غموض", "بيكسل آرت", "قصة غامضة", "شورتس"],
  "description_hashtags": ["#shorts", "#pixelart", "#غموض", "#قصص_غموض", "#ملفات_سرية"],
  "thumbnail_text": "كلمتان",
  "beats": [
    {{
      "caption": "جملة مترابطة فيها فواصل طبيعية لتنظيم الوقفات.",
      "speech_text": "نفس الجملة مع فواصل وعلامات توقف ووسم هادئ عند الحاجة",
      "scene_prompt": "detailed pixel art scene of explorers walking in misty mountains, rich shading",
      "env": "{chosen_env}",
      "time": "fog",
      "mood": "neutral",
      "action": "walk",
      "prop": "none",
      "has_shadow": false
    }}
  ]
}}

القواعد الصارمة:
- عدد المقاطع (beats) يجب أن يكون 12 إلى 15 مقطعاً مترابطاً.
- "env": اختر من {json.dumps(ALL_ENVIRONMENTS)}.
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
    if x1 < x0 or y1 < y0:
        return
    draw.rectangle([x0 * PX, y0 * PX, (x1 + 1) * PX - 1, (y1 + 1) * PX - 1], fill=fill, outline=outline)


def draw_dither_band(draw: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int, c1: tuple[int, int, int], c2: tuple[int, int, int], step: int = 2) -> None:
    for y in range(y0, y1 + 1, step):
        for x in range(x0, x1 + 1, step):
            col = c1 if ((x + y) // step) % 2 == 0 else c2
            px_rect(draw, x, y, min(x1, x + step - 1), min(y1, y + step - 1), col)


def draw_procedural_pixel_art(env: str, time_of_day: str, mood: str, action: str, has_shadow: bool, coat_color: tuple[int, int, int], seed: int) -> Image.Image:
    base = Image.new("RGB", (W, H), (18, 18, 32))
    draw = ImageDraw.Draw(base)
    rng = random.Random(seed)

    if time_of_day == "day":
        sky_top, sky_bot, far_c, mid_c, ground_c = (98, 174, 232), (228, 214, 174), (138, 152, 168), (112, 122, 94), (148, 114, 76)
    elif time_of_day == "dusk":
        sky_top, sky_bot, far_c, mid_c, ground_c = (48, 36, 76), (218, 128, 76), (86, 68, 96), (68, 54, 74), (92, 68, 52)
    elif time_of_day == "fog":
        sky_top, sky_bot, far_c, mid_c, ground_c = (86, 96, 108), (168, 174, 180), (108, 116, 124), (84, 92, 98), (94, 82, 68)
    else:
        sky_top, sky_bot, far_c, mid_c, ground_c = (22, 28, 54), (62, 68, 104), (42, 48, 76), (34, 42, 62), (56, 52, 68)

    horizon_y = 205
    for y in range(0, horizon_y, 2):
        t = y / max(horizon_y, 1)
        col = (
            int(sky_top[0] * (1 - t) + sky_bot[0] * t),
            int(sky_top[1] * (1 - t) + sky_bot[1] * t),
            int(sky_top[2] * (1 - t) + sky_bot[2] * t),
        )
        px_rect(draw, 0, y, GRID_W - 1, y + 1, col)

    draw_dither_band(draw, 0, horizon_y - 24, GRID_W - 1, horizon_y, sky_bot, far_c, step=2)

    if time_of_day in ("night", "dusk"):
        for _ in range(55):
            sx, sy = rng.randint(4, GRID_W - 5), rng.randint(6, 115)
            px_rect(draw, sx, sy, sx, sy, (245, 248, 255))

    for cx in range(-30, GRID_W + 30, 72):
        cy = 34 + (abs(cx + seed) % 34)
        px_rect(draw, cx, cy + 10, cx + 54, cy + 24, (112, 116, 142))
        px_rect(draw, cx + 6, cy + 4, cx + 46, cy + 18, (162, 166, 192))
        px_rect(draw, cx + 14, cy, cx + 36, cy + 10, (206, 210, 232))

    for x in range(0, GRID_W, 2):
        ridge_h = int(148 + 24 * math.sin((x + seed) * 0.032) + 12 * math.cos((x - seed) * 0.075))
        px_rect(draw, x, ridge_h, x + 1, horizon_y, far_c)

    front_top = 225 if env not in ("open_sea", "night_harbor") else 275
    px_rect(draw, 0, front_top, GRID_W - 1, GRID_H - 1, ground_c)
    draw_dither_band(draw, 0, front_top, GRID_W - 1, front_top + 18, mid_c, ground_c, step=2)

    for _ in range(90):
        gx = rng.randint(4, GRID_W - 6)
        gy = rng.randint(front_top + 6, GRID_H - 6)
        stone_c = (max(0, ground_c[0] - 24), max(0, ground_c[1] - 24), max(0, ground_c[2] - 24))
        px_rect(draw, gx, gy, gx + 2, gy + 1, stone_c)

    # رسم شخصية رئيسية بتفاصيل بيكسلية دقيقة
    cx, cy = 120, 340
    ink = (16, 16, 24)
    hat_c = (56, 66, 52)
    skin_c = (232, 184, 144)
    pants_c = (54, 62, 78)

    px_rect(draw, cx - 14, cy + 2, cx + 14, cy + 5, (20, 18, 28))
    px_rect(draw, cx - 6, cy - 46, cx + 6, cy - 32, skin_c, outline=ink)
    px_rect(draw, cx - 8, cy - 50, cx + 8, cy - 43, hat_c, outline=ink)
    px_rect(draw, cx - 10, cy - 44, cx + 10, cy - 42, hat_c, outline=ink)
    px_rect(draw, cx - 3, cy - 39, cx - 2, cy - 38, ink)
    px_rect(draw, cx + 2, cy - 39, cx + 3, cy - 38, ink)
    px_rect(draw, cx - 11, cy - 31, cx - 7, cy - 15, (78, 54, 38), outline=ink)
    px_rect(draw, cx - 7, cy - 31, cx + 7, cy - 13, coat_color, outline=ink)
    px_rect(draw, cx - 6, cy - 13, cx - 1, cy + 1, pants_c, outline=ink)
    px_rect(draw, cx + 1, cy - 13, cx + 6, cy + 1, pants_c, outline=ink)

    if has_shadow:
        sx = cx + 54
        px_rect(draw, sx - 12, cy + 2, sx + 12, cy + 5, (14, 14, 22))
        px_rect(draw, sx - 6, cy - 46, sx + 6, cy - 32, (32, 32, 48), outline=ink)
        px_rect(draw, sx - 8, cy - 50, sx + 8, cy - 43, (24, 24, 38), outline=ink)
        px_rect(draw, sx - 7, cy - 31, sx + 7, cy - 13, (22, 22, 36), outline=ink)
        px_rect(draw, sx - 6, cy - 13, sx - 1, cy + 1, (18, 18, 30), outline=ink)
        px_rect(draw, sx + 1, cy - 13, sx + 6, cy + 1, (18, 18, 30), outline=ink)

    return base


def get_or_generate_scene_artwork(beat: dict, beat_idx: int, story_seed: int, coat_color: tuple[int, int, int]) -> Image.Image:
    """توليد لقطة فنية بيكسلية عالية التفاصيل لكل مشهد مع تخزين مؤقت في الذاكرة المحلية."""
    env = str(beat.get("env", "mountain_pass")).strip()
    time_of_day = str(beat.get("time", "fog")).strip()
    mood = str(beat.get("mood", "neutral")).strip()
    action = str(beat.get("action", "walk")).strip()
    has_shadow = bool(beat.get("has_shadow", False))
    custom_prompt = str(beat.get("scene_prompt") or "").strip()

    prompt_text = custom_prompt or (
        f"detailed pixel art scene in {env} during {time_of_day}, realistic characters, rich pixel shading, "
        f"atmospheric depth, vertical 9:16 composition, no text, no watermark"
    )
    if "pixel art" not in prompt_text.lower():
        prompt_text = f"detailed pixel art, {prompt_text}, vertical 9:16 composition, no text"

    cache_key = hashlib.sha256(f"{story_seed}:{beat_idx}:{prompt_text}".encode("utf-8")).hexdigest()[:16]
    cached_file = ART_CACHE_DIR / f"{cache_key}.png"
    if cached_file.exists():
        try:
            return Image.open(cached_file).convert("RGB").resize((W, H), Image.Resampling.LANCZOS)
        except Exception:
            pass

    # محاولة جلب لقطة فنية عالية التفاصيل عبر Pollinations (مجاني بدون مفتاح) مع العودة للرسم البيكسلي الدقيق تلقائياً
    try:
        encoded = urllib.parse.quote(prompt_text)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width={W}&height={H}&seed={story_seed + beat_idx}&nologo=true"
        resp = requests.get(url, timeout=25)
        if resp.ok and resp.content and len(resp.content) > 4096:
            tmp_raw = ART_CACHE_DIR / f"{cache_key}_raw.bin"
            tmp_raw.write_bytes(resp.content)
            art = Image.open(tmp_raw).convert("RGB").resize((W, H), Image.Resampling.LANCZOS)
            # إضفاء طابع Pixel Art حاد ومتقن على الصورة المولدة
            small = art.resize((GRID_W, GRID_H), Image.Resampling.BILINEAR)
            pixelated = small.resize((W, H), Image.Resampling.NEAREST)
            pixelated.save(cached_file)
            return pixelated
    except Exception:
        pass

    fallback_art = draw_procedural_pixel_art(
        env=env,
        time_of_day=time_of_day,
        mood=mood,
        action=action,
        has_shadow=has_shadow,
        coat_color=coat_color,
        seed=story_seed + beat_idx * 41,
    )
    fallback_art.save(cached_file)
    return fallback_art


def apply_camera_and_atmosphere(base_art: Image.Image, mood: str, frame_idx: int, num_frames: int, story_seed: int) -> Image.Image:
    progress = frame_idx / max(num_frames - 1, 1)
    ease = 0.5 - 0.5 * math.cos(progress * math.pi)
    zoom_factor = 1.02 + 0.06 * ease
    crop_w = int(W / zoom_factor)
    crop_h = int(H / zoom_factor)

    pan_x = int(math.sin(progress * math.pi) * 24)
    pan_y = int(math.cos(progress * math.pi) * 16)
    shake_x = int(math.sin(frame_idx * 1.9) * 6) if mood == "shocked" else 0
    shake_y = int(math.cos(frame_idx * 2.3) * 4) if mood == "shocked" else 0

    left = max(0, min(W - crop_w, (W - crop_w) // 2 + pan_x + shake_x))
    top = max(0, min(H - crop_h, (H - crop_h) // 2 + pan_y + shake_y))
    cropped = base_art.crop((left, top, left + crop_w, top + crop_h))
    frame_img = cropped.resize((W, H), Image.Resampling.NEAREST).convert("RGBA")

    vig_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    vdraw = ImageDraw.Draw(vig_layer)
    vdraw.rectangle([0, 0, W, 130], fill=(8, 10, 18, 95))
    vdraw.rectangle([0, H - 220, W, H], fill=(8, 10, 18, 130))
    vig_layer = vig_layer.filter(ImageFilter.GaussianBlur(radius=24))

    part_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(part_layer)
    rng = random.Random(story_seed + 999)
    for i in range(14):
        bx = rng.randint(60, W - 60)
        by = rng.randint(180, 1320)
        dx = int(math.sin(frame_idx * 0.12 + i) * 20)
        dy = int(math.cos(frame_idx * 0.09 + i) * 16)
        alpha = 30 + ((i * 7 + frame_idx * 5) % 40)
        pdraw.ellipse([bx + dx, by + dy, bx + dx + 6, by + dy + 6], fill=(245, 240, 220, alpha))
    part_layer = part_layer.filter(ImageFilter.GaussianBlur(radius=2))

    composed = Image.alpha_composite(frame_img, part_layer)
    composed = Image.alpha_composite(composed, vig_layer)
    return composed.convert("RGB")


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
    mood = str(beat.get("mood", "neutral")).strip()
    caption_raw = fix_arabic_spelling(strip_emotes_for_caption(beat.get("caption", "")))

    coat_palette = [
        (88, 108, 138),
        (142, 92, 74),
        (74, 118, 98),
        (134, 112, 72),
        (106, 86, 134),
    ]
    coat_color = coat_palette[(story_seed + beat_idx) % len(coat_palette)]
    scene_art = get_or_generate_scene_artwork(beat, beat_idx, story_seed, coat_color)

    font_cap = load_arabic_font(62)
    chunks_timeline = build_auto_caption_chunks(caption_raw, num_frames)
    out_frames: list[Path] = []

    for f_i in range(num_frames):
        img = apply_camera_and_atmosphere(scene_art, mood, f_i, num_frames, story_seed + beat_idx * 13)
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
    clean_t = fix_arabic_spelling(strip_emotes_for_caption(thumb_text)) or "لغز غامض"
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
