import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import requests

DATA_DIR = Path("data")
HISTORY_FILE = DATA_DIR / "stories_history.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)

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
        norm = normalize_emote_name(m.group(1).strip().lower())
        return f" [{norm}] " if norm else " "

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


def enforce_title_with_emojis_and_1_to_3_tags(
    title: str,
    tags_pool: list[str] | None = None,
    fallback_title: str = "لغز الممر المفقود",
) -> str:
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

    return f"{short_title} {e1}{e2} {' '.join(title_tags[:3])}".strip()[:95].strip()


def build_large_description_hashtags(
    raw_tags: list[str] | None,
    extra_keywords: list[str] | None = None,
    max_tags: int = 500,
) -> list[str]:
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
    return json.loads(match.group(0) if match else cleaned)


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
    return hashlib.sha256(f"{title} | {captions}".encode("utf-8")).hexdigest()


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
