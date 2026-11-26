import os
import re
from datetime import datetime, timezone
import requests

from story_engine import call_llm_raw, extract_json, fix_arabic_spelling, strip_emotes_for_caption


def fetch_trending_news_snippets() -> list[str]:
    """جلب عناوين وأخبار وقصص رائجة حالياً عبر RSS مجاني أو بحث الويب إن توفر."""
    snippets: list[str] = []
    rss_urls = [
        "https://news.google.com/rss?hl=ar&gl=MA&ceid=MA:ar",
        "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
    ]
    for url in rss_urls:
        try:
            r = requests.get(url, timeout=15)
            if r.ok and r.text:
                titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>|<title>(.*?)</title>", r.text)
                for t1, t2 in titles[1:12]:
                    raw_t = fix_arabic_spelling(strip_emotes_for_caption(t1 or t2))
                    if raw_t and len(raw_t) > 6:
                        snippets.append(raw_t)
        except Exception:
            pass
    return snippets[:12]


def fetch_competitor_patterns(keywords: list[str] | None = None) -> list[str]:
    """جمع أنماط وعناوين رائجة في قنوات القصص والغموض والبيكسل آرت عبر YouTube Data API إذا توفر مفتاح بحث أو إرجاع أنماط تحليلية."""
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
                        ch = sn.get("channelTitle", "")
                        vt = sn.get("title", "")
                        if vt:
                            findings.append(f"قناة ({ch}) حققت رواجاً بنمط عنوان: {vt}")
            except Exception:
                pass

    if not findings:
        findings = [
            "قصص البعثات المفقودة والرسائل القديمة ذات النهايات غير المتوقعة تحصد أعلى إكمال للمشاهدة",
            "المشاهد التي تبدأ بحدث غامض مباشر في أول 3 ثوانٍ ترفع نسبة الاستمرار",
            "طرح سؤال محيّر في الوسط والنهاية يزيد التفاعل في التعليقات والمشاركات",
        ]
    return findings[:8]


def build_growth_and_content_brief(slot: int, recent_titles: list[str]) -> dict:
    """دمج تحليل المنافسين + استراتيجية النمو السريع + الترند الحالي لإنتاج مخطط قصة أصلية غير منسوخة."""
    now_utc = datetime.now(timezone.utc)
    trends = fetch_trending_news_snippets()
    competitor_notes = fetch_competitor_patterns()

    trends_text = "\n".join(f"- {t}" for t in trends) if trends else "- لا توجد أخبار مباشرة متاحة حالياً؛ ابتكر حدثاً واقعياً معاصراً مشوقاً."
    comp_text = "\n".join(f"- {c}" for c in competitor_notes)

    prompt = f"""
أنت خبير استراتيجية نمو قنوات يوتيوب شورتس ومحلل محتوى تنافسي.
التاريخ: {now_utc.strftime("%Y-%m-%d %H:%M")} UTC | الفترة: #{slot}.

1) ما يشتهر به المنافسون (نستلهم الفكرة والجاذبية فقط دون أي نسخ نصي أو اقتباس مباشر لتجنب حقوق الطبع والنشر 100%):
{comp_text}

2) آخر الأخبار والقصص الرائجة حالياً (حوّل إحداها أو استلهم روحها إلى قصة غموض/بيكسل آرت تناسب محتوانا):
{trends_text}

3) هدف النمو السريع للقناة:
- جذب المشاهد في أول 3 ثوانٍ بحدث غير متوقع.
- بناء تشويق متصاعد يرفع نسبة إكمال الفيديو.
- إثارة تساؤل قوي يدفع المشاهدين للتعليق واللايك والاشتراك بشكل طبيعي.
- تجنب تكرار هذه العناوين السابقة: {", ".join(recent_titles[-15:]) if recent_titles else "لا يوجد"}

أعد النتيجة بصيغة JSON فقط بهذا الشكل:
{{
  "inspired_trend_angle": "زاوية مستوحاة من الترند أو خبر حالي محوّلة لقصة غموض أصلية",
  "competitor_hook_pattern": "نمط بداية وتشويق مستوحى دون نسخ",
  "growth_tactic": "أسلوب تحفيز اللايك والتعليق والاشتراك داخل السرد",
  "suggested_theme": "فكرة القصة الأصلية الجديدة بالكامل",
  "suggested_env": "mountain_pass"
}}
"""
    raw = call_llm_raw(prompt, temperature=0.85)
    if raw:
        try:
            data = extract_json(raw)
            return {
                "inspired_trend_angle": fix_arabic_spelling(str(data.get("inspired_trend_angle") or "")),
                "competitor_hook_pattern": fix_arabic_spelling(str(data.get("competitor_hook_pattern") or "")),
                "growth_tactic": fix_arabic_spelling(str(data.get("growth_tactic") or "")),
                "suggested_theme": fix_arabic_spelling(str(data.get("suggested_theme") or "")),
                "suggested_env": str(data.get("suggested_env") or "mountain_pass").strip(),
            }
        except Exception:
            pass

    return {
        "inspired_trend_angle": trends[0] if trends else "ظاهرة غامضة أثارت تساؤلات الناس مؤخراً",
        "competitor_hook_pattern": "بداية غامضة مباشرة مع تصاعد تدريجي للغز",
        "growth_tactic": "طرح سؤال مفتوح في النهاية يدعو المشاهدين لتوقع السبب في التعليقات",
        "suggested_theme": "تحقيق ميداني في أثر غامض ظهر فجأة في منطقة معزولة",
        "suggested_env": "misty_forest",
    }
