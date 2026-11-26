import os
import re
from datetime import datetime, timezone
import requests


def _clean_brief_text(text: str) -> str:
    s = re.sub(r"[\u064B-\u065F\u0670]", "", str(text or ""))
    s = re.sub(r"\[[^\]]*\]|<[^>]*>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _call_intel_llm(prompt: str, temperature: float = 0.85) -> str | None:
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


def _parse_json_obj(raw: str) -> dict:
    import json
    cleaned = re.sub(r"```json|```", "", raw, flags=re.IGNORECASE).strip()
    m = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    return json.loads(m.group(0) if m else cleaned)


def fetch_trending_news_snippets() -> list[str]:
    snippets: list[str] = []
    rss_urls = [
        "https://news.google.com/rss?hl=ar&gl=MA&ceid=MA:ar",
        "https://news.google.com/rss?hl=ar&gl=EG&ceid=EG:ar",
        "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
    ]
    for url in rss_urls:
        try:
            r = requests.get(url, timeout=15)
            if r.ok and r.text:
                titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", r.text)
                for t1, t2 in titles[1:10]:
                    clean_t = _clean_brief_text(t1 or t2)
                    if clean_t and len(clean_t) > 6 and clean_t not in snippets:
                        snippets.append(clean_t)
        except Exception:
            pass
    return snippets[:12]


def fetch_competitor_patterns(keywords: list[str] | None = None) -> list[str]:
    api_key = os.getenv("GOOGLE_API_KEY", "").strip()
    queries = keywords or ["pixel art mystery story", "قصص غموض قصيرة", "unsolved mystery shorts"]
    findings: list[str] = []

    if api_key:
        for q in queries[:2]:
            try:
                r = requests.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params={
                        "part": "snippet",
                        "q": q,
                        "type": "video",
                        "order": "viewCount",
                        "maxResults": 5,
                        "key": api_key,
                    },
                    timeout=15,
                )
                if r.ok:
                    for item in r.json().get("items", []):
                        sn = item.get("snippet", {})
                        ch = _clean_brief_text(sn.get("channelTitle", ""))
                        vt = _clean_brief_text(sn.get("title", ""))
                        if vt:
                            findings.append(f"نمط عنوان رائج لدى ({ch}): {vt}")
            except Exception:
                pass

    if not findings:
        findings = [
            "البدء بحدث غامض أو سؤال صادم في أول 3 ثوانٍ يرفع نسبة إكمال المشاهدة",
            "القصص ذات التحولات الواضحة والرسائل الغامضة والاختفاءات تزيد المشاركة وإعادة المشاهدة",
            "طرح تساؤل يحفز التوقع في النهاية يرفع التعليقات واللايكات والاشتراك بشكل طبيعي",
        ]
    return findings[:8]


def build_growth_and_content_brief(slot: int, recent_titles: list[str], history_len: int = 0) -> dict:
    now_utc = datetime.now(timezone.utc)
    trends = fetch_trending_news_snippets()
    competitor_notes = fetch_competitor_patterns()
    series_number = (history_len // 3) + 1

    trends_text = "\n".join(f"- {t}" for t in trends) if trends else "- لا توجد عناوين مباشرة حالياً؛ ابتكر حدثاً واقعياً معاصراً مشوقاً."
    comp_text = "\n".join(f"- {c}" for c in competitor_notes)

    prompt = f"""
أنت خبير نمو قنوات يوتيوب شورتس ومحلل محتوى تنافسي.
التاريخ: {now_utc.strftime("%Y-%m-%d %H:%M")} UTC | الفترة اليومية: #{slot} | رقم الملف/الحلقة المقترح: #{series_number}.

1) ما يشتهر به المنافسون (نستلهم الفكرة والجاذبية فقط دون أي نسخ حرفي أو اقتباس مباشر لحماية القناة من حقوق الطبع والنشر 100%):
{comp_text}

2) آخر الأخبار والقصص الرائجة حالياً (حوّل إحدى الزوايا إلى قصة غموض/بيكسل آرت تناسب هويتنا):
{trends_text}

3) خطة النمو السريع (مشاهدات ومشتركين ولايكات وتعليقات):
- Hook قوي في أول 3 ثوانٍ يوقف التمرير.
- تصاعد درامي واضح مع ترابط يشد المشاهد حتى النهاية (مع إمكانية ربطها بنهاية تمهّد لحلقة أو سؤال تفاعلي).
- تحفيز طبيعي للتعليق واللايك والاشتراك دون إزعاج.
- تجنب تكرار هذه العناوين السابقة: {", ".join(recent_titles[-20:]) if recent_titles else "لا يوجد"}

أعد النتيجة بصيغة JSON فقط بهذا الشكل:
{{
  "inspired_trend_angle": "زاوية مستوحاة من خبر أو ترند حالي محوّلة لقصة غموض أصلية",
  "competitor_hook_pattern": "نمط بداية وتشويق مستوحى دون أي نسخ",
  "growth_tactic": "تكتيك رفع الاحتفاظ بالمشاهدة والتعليقات واللايكات والاشتراك",
  "suggested_theme": "فكرة القصة الأصلية الجديدة بالكامل",
  "suggested_env": "mountain_pass",
  "series_tag": "ملف #{series_number}"
}}
"""
    raw = _call_intel_llm(prompt, temperature=0.88)
    if raw:
        try:
            data = _parse_json_obj(raw)
            return {
                "inspired_trend_angle": _clean_brief_text(data.get("inspired_trend_angle") or ""),
                "competitor_hook_pattern": _clean_brief_text(data.get("competitor_hook_pattern") or ""),
                "growth_tactic": _clean_brief_text(data.get("growth_tactic") or ""),
                "suggested_theme": _clean_brief_text(data.get("suggested_theme") or ""),
                "suggested_env": str(data.get("suggested_env") or "mountain_pass").strip(),
                "series_tag": _clean_brief_text(data.get("series_tag") or f"ملف #{series_number}"),
            }
        except Exception:
            pass

    return {
        "inspired_trend_angle": trends[0] if trends else "ظاهرة غامضة أثارت تساؤلات الناس مؤخراً",
        "competitor_hook_pattern": "بداية غامضة مباشرة مع تصاعد تدريجي للغز",
        "growth_tactic": "طرح سؤال محيّر في النهاية يدعو المشاهدين لكتابة توقعهم في التعليقات",
        "suggested_theme": "تحقيق ميداني في أثر غامض ظهر فجأة في منطقة معزولة",
        "suggested_env": "misty_forest",
        "series_tag": f"ملف #{series_number}",
    }
