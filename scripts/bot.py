import asyncio
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import edge_tts
import requests
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path("output")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def get_slot_number() -> int:
    now_hour = datetime.now(timezone.utc).hour
    if now_hour < 10:
        return 1
    if now_hour < 14:
        return 2
    if now_hour < 18:
        return 3
    return 4


def extract_json(text: str) -> dict:
    cleaned = re.sub(r"```json|```", "", text, flags=re.IGNORECASE).strip()
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    raw = match.group(0) if match else cleaned
    return json.loads(raw)


def generate_story_payload(slot: int) -> dict:
    prompt = f"""
Create a distinct short story (#{slot} of 4 daily shorts) for a pixel-art YouTube Short.
Return ONLY valid JSON with these exact keys:
{{
  "title": "catchy short title",
  "story": "engaging narration in Arabic suitable for 35-50 seconds (around 90-120 words)",
  "translation": "natural English translation of the story",
  "description": "engaging 2-3 sentence hook description",
  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"],
  "hashtags": ["#shorts", "#pixelart", "#story", "#animation", "#mystery"],
  "scenes": [
    "pixel art scene 1 description, 16-bit retro aesthetic, crisp pixels, dramatic lighting",
    "pixel art scene 2 description, 16-bit retro aesthetic, crisp pixels, dramatic lighting",
    "pixel art scene 3 description, 16-bit retro aesthetic, crisp pixels, dramatic lighting",
    "pixel art scene 4 description, 16-bit retro aesthetic, crisp pixels, dramatic lighting"
  ],
  "thumbnail_text": "2-4 short punchy words"
}}
"""
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    google_key = os.getenv("GOOGLE_API_KEY", "").strip()
    zhipu_key = os.getenv("ZHIPU_API_KEY", "").strip()

    if openrouter_key:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {openrouter_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openrouter/free",
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        if r.ok:
            content = r.json()["choices"][0]["message"]["content"]
            return extract_json(content)

    if groq_key:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {groq_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.8,
            },
            timeout=60,
        )
        if r.ok:
            content = r.json()["choices"][0]["message"]["content"]
            return extract_json(content)

    if zhipu_key:
        r = requests.post(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            headers={
                "Authorization": f"Bearer {zhipu_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "glm-4-flash",
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        if r.ok:
            content = r.json()["choices"][0]["message"]["content"]
            return extract_json(content)

    if google_key:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={google_key}",
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )
        if r.ok:
            content = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return extract_json(content)

    return {
        "title": f"قصة بكسل آرت #{slot}",
        "story": "في مدينة مبنية بالكامل من وحدات البكسل، اكتشف بطلنا بوابة مخفية تفتح عالماً لم يره أحد من قبل. ومع كل خطوة كان الصوت يزداد وضوحاً والسر يقترب من الانكشاف.",
        "translation": "In a city built entirely out of pixels, our hero discovered a hidden gate opening to an unseen world.",
        "description": "مغامرة قصيرة بأسلوب Pixel Art تكشف سراً غامضاً في لحظات حاسمة.",
        "keywords": ["pixel art", "قصص قصيرة", "شورتس", "أنيميشن", "غموض"],
        "hashtags": ["#shorts", "#pixelart", "#story", "#retro", "#gaming"],
        "scenes": [
            "retro 16-bit pixel art city at night with glowing neon signs, crisp pixels",
            "pixel art adventurer standing before an ancient glowing portal, crisp pixels",
            "pixel art floating islands and mysterious runes in deep space, crisp pixels",
            "dramatic pixel art close-up of a glowing key in hand, crisp pixels",
        ],
        "thumbnail_text": "سر البوابة",
    }


def build_formatted_description(payload: dict) -> str:
    desc = str(payload.get("description", "")).strip()
    keywords = payload.get("keywords", [])
    hashtags = payload.get("hashtags", [])

    kw_text = ", ".join([str(k).strip() for k in keywords if str(k).strip()])
    tags_text = " ".join(
        [
            t if str(t).strip().startswith("#") else f"#{str(t).strip()}"
            for t in hashtags
            if str(t).strip()
        ]
    )

    return f"{desc}\n\nالكلمات المفتاحية:\n{kw_text}\n\n{tags_text}".strip()


def pixelize_image(img: Image.Image, target_w: int = 1080, target_h: int = 1920) -> Image.Image:
    base = img.convert("RGB").resize((target_w, target_h), Image.Resampling.LANCZOS)
    small = base.resize((135, 240), Image.Resampling.BILINEAR).quantize(colors=32, method=Image.Quantize.MEDIANCUT).convert("RGB")
    return small.resize((target_w, target_h), Image.Resampling.NEAREST)


def fetch_pixel_scene(scene_prompt: str, out_path: Path) -> None:
    encoded = quote(f"{scene_prompt}, pixel art, 16-bit, crisp edges")
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=540&height=960&nologo=true"
    try:
        r = requests.get(url, timeout=45)
        if r.ok and r.headers.get("content-type", "").startswith("image"):
            from io import BytesIO
            im = Image.open(BytesIO(r.content))
            pixelize_image(im).save(out_path)
            return
    except Exception:
        pass

    fallback = Image.new("RGB", (1080, 1920), (20, 24, 48))
    draw = ImageDraw.Draw(fallback)
    for y in range(0, 1920, 120):
        color = (35 + (y // 120) * 10, 20, 65 + (y // 120) * 8)
        draw.rectangle([0, y, 1080, y + 120], fill=color)
    pixelize_image(fallback).save(out_path)


def create_thumbnail(scene_path: Path, thumb_text: str, out_thumb: Path) -> None:
    im = Image.open(scene_path).convert("RGBA").resize((1080, 1920), Image.Resampling.NEAREST)
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([0, 1380, 1080, 1920], fill=(10, 10, 20, 190))

    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    font = ImageFont.truetype(font_path, 76) if Path(font_path).exists() else ImageFont.load_default()
    draw.text((70, 1520), thumb_text[:36], fill=(255, 220, 60, 255), font=font)

    combined = Image.alpha_composite(im, overlay).convert("RGB")
    combined.save(out_thumb)


async def generate_voice
