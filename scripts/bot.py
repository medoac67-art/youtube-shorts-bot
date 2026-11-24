import asyncio
import json
import math
import os
import re
import subprocess
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
OUT_DIR.mkdir(parents=True, exist_ok=True)

W, H = 1080, 1920
GRID_W, GRID_H = 72, 128
PX = 15
PRIVACY_STATUS = "public"


def clean_ar_text(text: str) -> str:
    no_diacritics = re.sub(r"[\u064B-\u065F\u0670]", "", str(text or ""))
    return re.sub(r"\s+", " ", no_diacritics).strip()


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
Create a punchy, suspenseful Arabic short story (#{slot} of 4 daily shorts) for a mystery YouTube Shorts channel ("ملفات سرية") in a rich 16-bit pixel-art style.
Return ONLY valid JSON with this exact structure:
{{
  "title": "عنوان قصير ومثير للفضول",
  "description": "وصف قصير مشوق باللغة العربية من جملتين",
  "keywords": ["ملفات سرية", "pixel art", "قصص غموض", "شورتس", "أنيميشن"],
  "hashtags": ["#shorts", "#pixelart", "#ملفات_سرية", "#غموض", "#قصص"],
  "thumbnail_text": "سر الليل",
  "beats": [
    {{
      "caption": "في منتصف الليل",
      "scene": "alley_night",
      "mood": "neutral",
      "action": "walk",
      "prop": "none",
      "has_shadow": true
    }},
    {{
      "caption": "ظهر ظل يتحرك",
      "scene": "alley_night",
      "mood": "surprised",
      "action": "point",
      "prop": "exclamation",
      "has_shadow": true
    }},
    {{
      "caption": "يشير نحو باب مغلق",
      "scene": "alley_night",
      "mood": "curious",
      "action": "point",
      "prop": "idea",
      "has_shadow": true
    }},
    {{
      "caption": "فتحت الباب فجأة",
      "scene": "corridor",
      "mood": "shocked",
      "action": "alert",
      "prop": "exclamation",
      "has_shadow": false
    }},
    {{
      "caption": "كان الممر طويلا جدا",
      "scene": "corridor",
      "mood": "curious",
      "action": "walk",
      "prop": "speech",
      "has_shadow": true
    }},
    {{
      "caption": "في الصباح تغير كل شيء",
      "scene": "street_day",
      "mood": "happy",
      "action": "wave",
      "prop": "idea",
      "has_shadow": false
    }},
    {{
      "caption": "لكن السر بقي في الغرفة",
      "scene": "room_night",
      "mood": "shocked",
      "action": "alert",
      "prop": "heart",
      "has_shadow": true
    }}
  ]
}}

Rules:
- 7 to 8 short Arabic captions (2 to 4 words each), fast and engaging narration.
- Do not use Arabic diacritics (تشكيل).
- "scene" in ["alley_night", "corridor", "room_night", "street_day"].
- "mood" in ["neutral", "surprised", "shocked", "happy", "curious"].
- "action" in ["walk", "point", "alert", "wave"].
- "prop" in ["none", "exclamation", "idea", "speech", "heart"].
"""
    for env_name, url, model in [
        ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/chat/completions", "openrouter/free"),
        ("GROQ_API_KEY", "https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile"),
        ("ZHIPU_API_KEY", "https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-4-flash"),
    ]:
        key = os.getenv(env_name, "").strip()
        if not key:
            continue
        try:
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.75},
                timeout=45,
            )
            if r.ok:
                data = extract_json(r.json()["choices"][0]["message"]["content"])
                if isinstance(data.get("beats"), list) and len(data["beats"]) >= 4:
                    return data
        except Exception:
            pass

    google_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if google_key:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={google_key}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=45,
            )
            if r.ok:
                data = extract_json(r.json()["candidates"][0]["content"]["parts"][0]["text"])
                if isinstance(data.get("beats"), list) and len(data["beats"]) >= 4:
                    return data
        except Exception:
            pass

    return {
        "title": f"ملف سري #{slot}: ظل في منتصف الليل",
        "description": "قصة غموض قصيرة بأسلوب Pixel Art من سلسلة ملفات سرية. ماذا يختبئ خلف الباب المغلق؟",
        "keywords": ["ملفات سرية", "pixel art", "قصص غموض", "شورتس", "أنيميشن"],
        "hashtags": ["#shorts", "#pixelart", "#ملفات_سرية", "#غموض", "#قصص"],
        "thumbnail_text": "ملف سري",
        "beats": [
            {"caption": "في منتصف الليل", "scene": "alley_night", "mood": "neutral", "action": "walk", "prop": "none", "has_shadow": True},
            {"caption": "ظهر ظل يتحرك", "scene": "alley_night", "mood": "surprised", "action": "point", "prop": "exclamation", "has_shadow": True},
            {"caption": "يشير نحو باب مغلق", "scene": "alley_night", "mood": "curious", "action": "point", "prop": "idea", "has_shadow": True},
            {"caption": "فتحت الباب فجأة", "scene": "corridor", "mood": "shocked", "action": "alert", "prop": "exclamation", "has_shadow": False},
            {"caption": "كان الممر طويلا جدا", "scene": "corridor", "mood": "curious", "action": "walk", "prop": "speech", "has_shadow": True},
            {"caption": "في الصباح تغير كل شيء", "scene": "street_day", "mood": "happy", "action": "wave", "prop": "idea", "has_shadow": False},
            {"caption": "لكن السر بقي في الغرفة", "scene": "room_night", "mood": "shocked", "action": "alert", "prop": "heart", "has_shadow": True},
        ],
    }


def build_formatted_description(payload: dict) -> str:
    desc = clean_ar_text(payload.get("description", ""))
    keywords = payload.get("keywords", [])
    hashtags = payload.get("hashtags", [])
    kw_text = ", ".join([clean_ar_text(k) for k in keywords if clean_ar_text(k)])
    tags_text = " ".join(
        [
            t if str(t).strip().startswith("#") else f"#{clean_ar_text(t).replace(' ', '_')}"
            for t in hashtags
            if clean_ar_text(t)
        ]
    )
    return f"{desc}\n\nالكلمات المفتاحية:\n{kw_text}\n\n{tags_text}".strip()


def px_rect(draw: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int, fill, outline=None) -> None:
    draw.rectangle([x0 * PX, y0 * PX, (x1 + 1) * PX - 1, (y1 + 1) * PX - 1], fill=fill, outline=outline)


def draw_alley_night(draw: ImageDraw.ImageDraw, frame_idx: int) -> None:
    px_rect(draw, 0, 0, GRID_W - 1, 24, (22, 24, 52))
    px_rect(draw, 0, 25, GRID_W - 1, 32, (36, 32, 68))
    px_rect(draw, 10, 7, 17, 14, (245, 238, 185))
    px_rect(draw, 12, 9, 15, 12, (255, 250, 210))

    stars = [(4, 5), (22, 9), (34, 4), (45, 12), (56, 7), (64, 16), (28, 18), (8, 20)]
    for idx_s, (sx, sy) in enumerate(stars):
        if (frame_idx + idx_s) % 5 != 0:
            px_rect(draw, sx, sy, sx, sy, (240, 242, 255))

    for bx in range(0, GRID_W, 12):
        bh = 20 + ((bx * 3) % 7)
        px_rect(draw, bx, 33 - bh, bx + 10, 32, (18, 18, 36))
        for wy in range(35 - bh, 30, 5):
            px_rect(draw, bx + 2, wy, bx + 3, wy + 2, (235, 210, 120))
            px_rect(draw, bx + 7, wy, bx + 8, wy + 2, (110, 125, 165))

    px_rect(draw, 0, 33, GRID_W - 1, 88, (152, 68, 58))
    for row in range(34, 88, 4):
        offset = 0 if ((row // 4) % 2 == 0) else 3
        for col in range(offset, GRID_W, 7):
            px_rect(draw, col, row, col + 5, row + 1, (178, 84, 72))
            px_rect(draw, col, row + 2, col + 5, row + 3, (108, 44, 40))

    px_rect(draw, 44, 54, 58, 88, (64, 38, 28), outline=(18, 16, 22))
    px_rect(draw, 46, 56, 56, 86, (92, 54, 38))
    px_rect(draw, 54, 71, 55, 73, (235, 195, 75))

    px_rect(draw, 26, 44, 38, 58, (72, 42, 28), outline=(18, 16, 22))
    for wy in range(46, 58, 4):
        px_rect(draw, 28, wy, 36, wy + 2, (142, 88, 48))

    px_rect(draw, 18, 35, 20, 88, (54, 58, 76), outline=(16, 16, 22))
    px_rect(draw, 15, 35, 25, 37, (54, 58, 76), outline=(16, 16, 22))
    lamp_col = (255, 224, 124) if (frame_idx % 7 != 0) else (195, 170, 95)
    px_rect(draw, 20, 38, 24, 41, lamp_col)

    px_rect(draw, 0, 89, GRID_W - 1, 95, (102, 100, 118))
    px_rect(draw, 0, 96, GRID_W - 1, GRID_H - 1, (72, 70, 92))
    for py in range(98, GRID_H - 1, 5):
        for px_i in range(2, GRID_W - 2, 8):
            shift = (px_i + py) % 3
            px_rect(draw, px_i + shift, py, px_i + shift + 3, py + 1, (90, 88, 112))


def draw_corridor(draw: ImageDraw.ImageDraw, frame_idx: int) -> None:
    px_rect(draw, 0, 0, GRID_W - 1, 20, (48, 54, 94))
    px_rect(draw, 0, 21, GRID_W - 1, 90, (64, 72, 118))
    px_rect(draw, 0, 91, GRID_W - 1, GRID_H - 1, (92, 96, 138))

    lights = [
        (30, 22, 42, 27, (254, 242, 180)),
        (32, 35, 40, 38, (232, 222, 166)),
        (34, 45, 38, 47, (206, 200, 160)),
    ]
    for x0, y0, x1, y1, col in lights:
        px_rect(draw, x0, y0, x1, y1, col)

    px_rect(draw, 8, 42, 18, 88, (44, 112, 118), outline=(18, 20, 32))
    px_rect(draw, 54, 42, 64, 88, (44, 112, 118), outline=(18, 20, 32))
    swing = (frame_idx // 4) % 2
    px_rect(draw, 1, 28, 8 + swing, 90, (54, 132, 138), outline=(18, 20, 32))


def draw_room_night(draw: ImageDraw.ImageDraw, frame_idx: int) -> None:
    px_rect(draw, 0, 0, GRID_W - 1, 90, (96, 62, 42))
    for wx in range(0, GRID_W, 8):
        px_rect(draw, wx, 0, wx + 6, 90, (112, 74, 50))
    px_rect(draw, 0, 91, GRID_W - 1, GRID_H - 1, (76, 48, 32))

    px_rect(draw, 44, 24, 60, 46, (24, 28, 56), outline=(22, 16, 12))
    px_rect(draw, 52, 24, 52, 46, (45, 32, 24))
    px_rect(draw, 44, 35, 60, 35, (45, 32, 24))
    if frame_idx % 5 != 0:
        px_rect(draw, 48, 28, 48, 28, (242, 242, 255))
        px_rect(draw, 56, 39, 56, 39, (242, 242, 255))


def draw_street_day(draw: ImageDraw.ImageDraw, frame_idx: int) -> None:
    px_rect(draw, 0, 0, GRID_W - 1, 46, (118, 194, 245))
    cloud_x = 8 + (frame_idx % 14)
    px_rect(draw, cloud_x, 9, cloud_x + 10, 12, (248, 252, 255))
    px_rect(draw, cloud_x + 3, 6, cloud_x + 7, 9, (248, 252, 255))

    px_rect(draw, 5, 18, 24, 70, (150, 168, 196), outline=(28, 34, 48))
    px_rect(draw, 29, 13, 48, 70, (198, 142, 118), outline=(28, 34, 48))
    px_rect(draw, 52, 24, 70, 70, (124, 152, 184), outline=(28, 34, 48))

    for wy in range(21, 64, 8):
        for wx in [9, 17, 33, 41, 56, 63]:
            px_rect(draw, wx, wy, wx + 3, wy + 4, (255, 236, 150))

    px_rect(draw, 0, 71, GRID_W - 1, 80, (176, 178, 188))
    px_rect(draw, 0, 81, GRID_W - 1, 98, (68, 72, 82))
    for rx in range(3, GRID_W, 10):
        px_rect(draw, rx, 89, rx + 5, 90, (240, 215, 90))
    px_rect(draw, 0, 99, GRID_W - 1, GRID_H - 1, (86, 168, 76))


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


def render_beat_frames(beat: dict, beat_idx: int, duration_sec: float, fps: int = 12) -> list[Path]:
    num_frames = max(int(round(duration_sec * fps)), 8)
    scene_name = str(beat.get("scene", "alley_night")).strip()
    mood = str(beat.get("mood", "neutral")).strip()
    action = str(beat.get("action", "walk")).strip()
    prop = str(beat.get("prop", "none")).strip()
    has_shadow = bool(beat.get("has_shadow", True))
    caption_raw = clean_ar_text(beat.get("caption", ""))

    font_cap = load_arabic_font(58)
    out_frames: list[Path] = []

    for f_i in range(num_frames):
        img = Image.new("RGB", (W, H), (18, 18, 32))
        draw = ImageDraw.Draw(img)

        if scene_name == "corridor":
            draw_corridor(draw, f_i)
            char_y = 92
        elif scene_name == "room_night":
            draw_room_night(draw, f_i)
            char_y = 92
        elif scene_name == "street_day":
            draw_street_day(draw, f_i)
            char_y = 98
        else:
            draw_alley_night(draw, f_i)
            char_y = 92

        drift = int(math.sin((f_i / max(num_frames, 1)) * math.pi) * 5)
        char_x = 22 + drift
        draw_character(
            draw,
            char_x,
            char_y,
            f_i,
            mood=mood,
            action=action,
            shirt_color=(102, 112, 212),
            is_shadow=False,
        )

        if has_shadow:
            shadow_x = 46 - (drift // 2)
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
            bbox = draw.textbbox((0, 0), caption_raw, font=font_cap)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            box_w = min(tw + 70, W - 80)
            box_h = th + 42
            bx0 = (W - box_w) // 2
            by0 = 1430
            bx1 = bx0 + box_w
            by1 = by0 + box_h

            draw.rounded_rectangle([bx0, by0, bx1, by1], radius=20, fill=(14, 16, 26), outline=(120, 210, 175), width=3)
            tx = (W - tw) // 2
            ty = by0 + (box_h - th) // 2 - 4
            draw.text((tx, ty), caption_raw, font=font_cap, fill=(255, 255, 255))

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


async def generate_continuous_voice(beats: list[dict], audio_path: Path) -> list[float]:
    lines = [clean_ar_text(b.get("caption", "")) or "ملفات سرية" for b in beats]
    full_script = ". ".join(lines) + "."

    # صوت واضح ومتصل بسرعة أسرع وأكثر حيوية دون تقطيع بين الجمل
    voice = "ar-SA-HamedNeural"
    comm = edge_tts.Communicate(text=full_script, voice=voice, rate="+20%")
    with open(audio_path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])

    total_dur = get_audio_duration(audio_path)
    weights = [max(len(re.findall(r"\S+", line)), 1) for line in lines]
    weight_sum = sum(weights) or len(lines)
    return [max((w / weight_sum) * total_dur, 1.0) for w in weights]


def create_thumbnail(first_frame: Path, thumb_text: str, out_thumb: Path) -> None:
    im = Image.open(first_frame).convert("RGB").resize((W, H), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(im)
    draw.rectangle([0, 1540, W, H], fill=(12, 14, 24))
    clean_t = clean_ar_text(thumb_text) or "ملف سري"
    font_t = load_arabic_font(72)
    bbox = draw.textbbox((0, 0), clean_t, font=font_t)
    tw = bbox[2] - bbox[0]
    tx = (W - tw) // 2
    draw.text((tx, 1650), clean_t, font=font_t, fill=(255, 220, 72))
    im.save(out_thumb)


def render_video_from_beats(beats: list[dict], beat_durations: list[float], audio_path: Path, out_video: Path) -> None:
    fps = 12
    all_frames: list[Path] = []
    for idx, (beat, dur) in enumerate(zip(beats, beat_durations)):
        frames = render_beat_frames(beat, idx, dur, fps=fps)
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

    clean_tags = [clean_ar_text(k).lstrip("#")[:30] for k in keywords if clean_ar_text(k)][:15]
    body = {
        "snippet": {
            "title": clean_ar_text(title)[:95],
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
    payload = generate_story_payload(slot)
    beats = payload.get("beats", [])[:8]

    formatted_description = build_formatted_description(payload)
    meta = {
        "slot": slot,
        "title": clean_ar_text(payload.get("title", "")),
        "description": formatted_description,
        "beats": beats,
    }
    (OUT_DIR / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    audio_path = OUT_DIR / "voice.mp3"
    beat_durations = asyncio.run(generate_continuous_voice(beats, audio_path))

    video_path = OUT_DIR / "short_final.mp4"
    render_video_from_beats(beats, beat_durations, audio_path, video_path)

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

    print("DONE! Generated:", video_path, thumb_path)
    print("\n--- DESCRIPTION ---\n" + formatted_description)


if __name__ == "__main__":
    main()
