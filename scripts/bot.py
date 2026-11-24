import asyncio
import json
import math
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display
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
GRID_W, GRID_H = 54, 96
PX = 20
PRIVACY_STATUS = "public"


def shape_ar(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    try:
        reshaped = arabic_reshaper.reshape(clean)
        return get_display(reshaped)
    except Exception:
        return clean


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
Create a dramatic, fast-paced short mystery story (#{slot} of 4 daily shorts) for an Arabic YouTube Shorts channel ("ملفات سرية") inspired by retro 2D pixel-art story animations.
Return ONLY valid JSON with this exact structure:
{{
  "title": "عنوان مشوق وقصير",
  "description": "وصف قصير مشوق من جملتين أو ثلاث باللغة العربية",
  "keywords": ["ملفات سرية", "pixel art", "قصص غموض", "شورتس", "أنيميشن"],
  "hashtags": ["#shorts", "#pixelart", "#ملفات_سرية", "#غموض", "#قصص"],
  "thumbnail_text": "كلمة أو كلمتان",
  "beats": [
    {{
      "caption": "في منتصف الليل",
      "highlight_word": "الليل",
      "scene": "alley_night",
      "mood": "neutral",
      "action": "walk",
      "prop": "none",
      "has_shadow": true
    }},
    {{
      "caption": "ظهر ظل يتحرك",
      "highlight_word": "ظل",
      "scene": "alley_night",
      "mood": "surprised",
      "action": "point",
      "prop": "exclamation",
      "has_shadow": true
    }},
    {{
      "caption": "يشير نحو باب مغلق",
      "highlight_word": "باب",
      "scene": "alley_night",
      "mood": "curious",
      "action": "point",
      "prop": "idea",
      "has_shadow": true
    }},
    {{
      "caption": "فتحت الباب فجأة",
      "highlight_word": "فجأة",
      "scene": "corridor",
      "mood": "shocked",
      "action": "alert",
      "prop": "exclamation",
      "has_shadow": false
    }},
    {{
      "caption": "وكان الممر طويلا",
      "highlight_word": "الممر",
      "scene": "corridor",
      "mood": "curious",
      "action": "walk",
      "prop": "speech",
      "has_shadow": true
    }},
    {{
      "caption": "في الصباح تغير كل شيء",
      "highlight_word": "الصباح",
      "scene": "street_day",
      "mood": "happy",
      "action": "wave",
      "prop": "idea",
      "has_shadow": false
    }},
    {{
      "caption": "لكن السر بقي في الغرفة",
      "highlight_word": "السر",
      "scene": "room_night",
      "mood": "shocked",
      "action": "alert",
      "prop": "heart",
      "has_shadow": true
    }}
  ]
}}

Rules:
- Produce 7 to 8 beats forming a coherent Arabic mystery story (about 30-42 seconds total).
- Each "caption" must be 2 to 4 short Arabic words.
- "highlight_word" must be one word from "caption".
- "scene" must be one of: "alley_night", "corridor", "room_night", "street_day".
- "mood" must be one of: "neutral", "surprised", "shocked", "happy", "curious".
- "action" must be one of: "walk", "point", "alert", "wave".
- "prop" must be one of: "none", "exclamation", "idea", "speech", "heart".
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
                json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.8},
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
        "title": f"ملف سري #{slot}: إشارة في منتصف الليل",
        "description": "قصة غموض بأسلوب Pixel Art من سلسلة ملفات سرية. ما الذي يختبئ خلف الباب في قلب الليل؟",
        "keywords": ["ملفات سرية", "pixel art", "قصص غموض", "شورتس", "أنيميشن"],
        "hashtags": ["#shorts", "#pixelart", "#ملفات_سرية", "#غموض", "#قصص"],
        "thumbnail_text": "ملف سري",
        "beats": [
            {"caption": "في منتصف الليل", "highlight_word": "الليل", "scene": "alley_night", "mood": "neutral", "action": "walk", "prop": "none", "has_shadow": True},
            {"caption": "ظهر ظل يتحرك", "highlight_word": "ظل", "scene": "alley_night", "mood": "surprised", "action": "point", "prop": "exclamation", "has_shadow": True},
            {"caption": "يشير نحو باب مغلق", "highlight_word": "باب", "scene": "alley_night", "mood": "curious", "action": "point", "prop": "idea", "has_shadow": True},
            {"caption": "فتحت الباب فجأة", "highlight_word": "فجأة", "scene": "corridor", "mood": "shocked", "action": "alert", "prop": "exclamation", "has_shadow": False},
            {"caption": "وكان الممر طويلا", "highlight_word": "الممر", "scene": "corridor", "mood": "curious", "action": "walk", "prop": "speech", "has_shadow": True},
            {"caption": "في الصباح تغير كل شيء", "highlight_word": "الصباح", "scene": "street_day", "mood": "happy", "action": "wave", "prop": "idea", "has_shadow": False},
            {"caption": "لكن السر بقي في الغرفة", "highlight_word": "السر", "scene": "room_night", "mood": "shocked", "action": "alert", "prop": "heart", "has_shadow": True},
        ],
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


def px_rect(draw: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int, fill, outline=None) -> None:
    draw.rectangle([x0 * PX, y0 * PX, (x1 + 1) * PX - 1, (y1 + 1) * PX - 1], fill=fill, outline=outline)


def draw_alley_night(draw: ImageDraw.ImageDraw, frame_idx: int) -> None:
    px_rect(draw, 0, 0, GRID_W - 1, 18, (22, 24, 52))
    px_rect(draw, 0, 19, GRID_W - 1, 24, (36, 32, 68))
    px_rect(draw, 8, 5, 13, 10, (245, 238, 185))
    px_rect(draw, 9, 6, 12, 9, (255, 250, 210))

    stars = [(3, 4), (17, 7), (25, 3), (34, 9), (42, 5), (48, 12), (21, 13), (6, 15)]
    for idx_s, (sx, sy) in enumerate(stars):
        if (frame_idx + idx_s) % 5 != 0:
            px_rect(draw, sx, sy, sx, sy, (238, 240, 255))

    for bx in range(0, GRID_W, 9):
        bh = 15 + ((bx * 3) % 5)
        px_rect(draw, bx, 25 - bh, bx + 7, 24, (18, 18, 36))

    px_rect(draw, 0, 25, GRID_W - 1, 66, (154, 70, 60))
    for row in range(26, 66, 3):
        offset = 0 if ((row // 3) % 2 == 0) else 2
        for col in range(offset, GRID_W, 5):
            px_rect(draw, col, row, col + 3, row + 1, (176, 84, 72))
            px_rect(draw, col, row + 2, col + 3, row + 2, (108, 44, 40))

    px_rect(draw, 33, 33, 43, 44, (74, 42, 28), outline=(20, 18, 24))
    for wy in range(34, 44, 3):
        px_rect(draw, 34, wy, 42, wy + 1, (138, 86, 48))

    px_rect(draw, 16, 26, 17, 66, (52, 56, 72), outline=(18, 18, 24))
    px_rect(draw, 14, 26, 22, 27, (52, 56, 72), outline=(18, 18, 24))
    lamp_col = (255, 222, 122) if (frame_idx % 7 != 0) else (190, 165, 95)
    px_rect(draw, 18, 28, 21, 30, lamp_col)

    px_rect(draw, 0, 67, GRID_W - 1, 71, (98, 96, 114))
    px_rect(draw, 0, 72, GRID_W - 1, GRID_H - 1, (72, 70, 92))
    for py in range(73, GRID_H - 1, 4):
        for px_i in range(2, GRID_W - 2, 6):
            shift = (px_i + py) % 3
            px_rect(draw, px_i + shift, py, px_i + shift + 2, py + 1, (88, 86, 110))
    px_rect(draw, 31, 76, 41, 79, (56, 72, 118))
    px_rect(draw, 34, 77, 38, 78, (125, 155, 215))


def draw_corridor(draw: ImageDraw.ImageDraw, frame_idx: int) -> None:
    px_rect(draw, 0, 0, GRID_W - 1, 14, (50, 56, 96))
    px_rect(draw, 0, 15, GRID_W - 1, 68, (64, 72, 118))
    px_rect(draw, 0, 69, GRID_W - 1, GRID_H - 1, (92, 96, 138))

    lights = [
        (22, 16, 31, 20, (252, 240, 178)),
        (24, 26, 29, 28, (232, 222, 166)),
        (25, 33, 28, 34, (206, 200, 160)),
    ]
    for x0, y0, x1, y1, col in lights:
        px_rect(draw, x0, y0, x1, y1, col)

    px_rect(draw, 6, 30, 13, 66, (44, 112, 118), outline=(20, 22, 34))
    px_rect(draw, 40, 30, 47, 66, (44, 112, 118), outline=(20, 22, 34))
    swing = (frame_idx // 4) % 2
    px_rect(draw, 1, 20, 6 + swing, 68, (54, 132, 138), outline=(18, 20, 32))


def draw_room_night(draw: ImageDraw.ImageDraw, frame_idx: int) -> None:
    px_rect(draw, 0, 0, GRID_W - 1, 68, (96, 62, 42))
    for wx in range(0, GRID_W, 6):
        px_rect(draw, wx, 0, wx + 4, 68, (112, 74, 50))
    px_rect(draw, 0, 69, GRID_W - 1, GRID_H - 1, (76, 48, 32))

    px_rect(draw, 33, 18, 45, 34, (24, 28, 56), outline=(25, 18, 14))
    px_rect(draw, 39, 18, 39, 34, (45, 32, 24))
    px_rect(draw, 33, 26, 45, 26, (45, 32, 24))
    if frame_idx % 5 != 0:
        px_rect(draw, 36, 21, 36, 21, (240, 240, 255))
        px_rect(draw, 42, 29, 42, 29, (240, 240, 255))


def draw_street_day(draw: ImageDraw.ImageDraw, frame_idx: int) -> None:
    px_rect(draw, 0, 0, GRID_W - 1, 34, (118, 194, 245))
    cloud_x = 6 + (frame_idx % 12)
    px_rect(draw, cloud_x, 7, cloud_x + 7, 9, (245, 250, 255))
    px_rect(draw, cloud_x + 2, 5, cloud_x + 5, 7, (245, 250, 255))

    px_rect(draw, 4, 14, 18, 52, (150, 168, 196), outline=(30, 36, 50))
    px_rect(draw, 22, 10, 36, 52, (198, 142, 118), outline=(30, 36, 50))
    px_rect(draw, 39, 18, 52, 52, (124, 152, 184), outline=(30, 36, 50))

    for wy in range(16, 48, 6):
        for wx in [7, 13, 25, 31, 42, 47]:
            px_rect(draw, wx, wy, wx + 2, wy + 3, (255, 236, 150))

    px_rect(draw, 0, 53, GRID_W - 1, 60, (176, 178, 188))
    px_rect(draw, 0, 61, GRID_W - 1, 74, (68, 72, 82))
    for rx in range(2, GRID_W, 8):
        px_rect(draw, rx, 67, rx + 4, 68, (240, 215, 90))
    px_rect(draw, 0, 75, GRID_W - 1, GRID_H - 1, (86, 168, 76))


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
        eye_c = (130, 150, 230)
    else:
        hair_c = (36, 36, 48)
        skin_c = (242, 196, 156)
        shirt_c = shirt_color
        pants_c = (48, 56, 86)
        eye_c = (20, 20, 28)

    px_rect(draw, cx - 4, cy - 16, cx + 4, cy - 8, skin_c, outline=outline)
    px_rect(draw, cx - 4, cy - 17, cx + 4, cy - 13, hair_c, outline=outline)

    if mood == "shocked":
        px_rect(draw, cx - 3, cy - 12, cx - 2, cy - 10, (255, 255, 255))
        px_rect(draw, cx + 2, cy - 12, cx + 3, cy - 10, (255, 255, 255))
        px_rect(draw, cx - 2, cy - 11, cx - 2, cy - 11, eye_c)
        px_rect(draw, cx + 2, cy - 11, cx + 2, cy - 11, eye_c)
        px_rect(draw, cx - 1, cy - 9, cx + 1, cy - 8, (40, 20, 24))
    elif mood == "surprised":
        px_rect(draw, cx - 2, cy - 12, cx - 2, cy - 10, eye_c)
        px_rect(draw, cx + 2, cy - 12, cx + 2, cy - 10, eye_c)
        px_rect(draw, cx, cy - 9, cx, cy - 8, (40, 20, 24))
    elif mood == "happy":
        px_rect(draw, cx - 3, cy - 11, cx - 1, cy - 11, eye_c)
        px_rect(draw, cx + 1, cy - 11, cx + 3, cy - 11, eye_c)
        px_rect(draw, cx - 2, cy - 9, cx + 2, cy - 9, (185, 78, 78))
    else:
        px_rect(draw, cx - 2, cy - 11, cx - 2, cy - 11, eye_c)
        px_rect(draw, cx + 2, cy - 11, cx + 2, cy - 11, eye_c)
        px_rect(draw, cx - 1, cy - 9, cx + 1, cy - 9, (90, 58, 52))

    px_rect(draw, cx - 4, cy - 7, cx + 4, cy - 1, shirt_c, outline=outline)

    if action == "point":
        px_rect(draw, cx - 6, cy - 7, cx - 5, cy - 2, shirt_c, outline=outline)
        px_rect(draw, cx + 5, cy - 9, cx + 8, cy - 8, shirt_c, outline=outline)
    elif action == "wave":
        w = (step_phase % 2) * -1
        px_rect(draw, cx - 6, cy - 7, cx - 5, cy - 2, shirt_c, outline=outline)
        px_rect(draw, cx + 5, cy - 10 + w, cx + 6, cy - 5 + w, shirt_c, outline=outline)
    elif action == "alert":
        px_rect(draw, cx - 6, cy - 9, cx - 5, cy - 5, shirt_c, outline=outline)
        px_rect(draw, cx + 5, cy - 9, cx + 6, cy - 5, shirt_c, outline=outline)
    else:
        px_rect(draw, cx - 6, cy - 7, cx - 5, cy - 2, shirt_c, outline=outline)
        px_rect(draw, cx + 5, cy - 7, cx + 6, cy - 2, shirt_c, outline=outline)

    left_y = cy if (step_phase % 2 == 0) else cy - 1
    right_y = cy - 1 if (step_phase % 2 == 0) else cy
    px_rect(draw, cx - 3, left_y, cx - 1, cy + 2, pants_c, outline=outline)
    px_rect(draw, cx + 1, right_y, cx + 3, cy + 2, pants_c, outline=outline)


def draw_prop(draw: ImageDraw.ImageDraw, cx: int, cy: int, prop: str, frame_idx: int) -> None:
    bob = (frame_idx % 4) // 2
    py = cy - 24 - bob
    if prop == "exclamation":
        px_rect(draw, cx, py, cx + 1, py + 4, (245, 68, 58), outline=(20, 16, 20))
        px_rect(draw, cx, py + 6, cx + 1, py + 7, (245, 68, 58), outline=(20, 16, 20))
    elif prop == "idea":
        px_rect(draw, cx - 2, py, cx + 3, py + 4, (255, 224, 72), outline=(25, 22, 20))
        px_rect(draw, cx - 1, py + 5, cx + 2, py + 6, (180, 185, 195))
    elif prop == "speech":
        px_rect(draw, cx - 4, py, cx + 5, py + 5, (245, 248, 255), outline=(20, 22, 30))
        for dx in [-2, 0, 2]:
            px_rect(draw, cx + dx, py + 2, cx + dx, py + 3, (35, 38, 52))
    elif prop == "heart":
        px_rect(draw, cx - 2, py + 1, cx + 3, py + 3, (238, 64, 98))
        px_rect(draw, cx - 1, py, cx, py + 1, (238, 64, 98))
        px_rect(draw, cx + 1, py, cx + 2, py + 1, (238, 64, 98))
        px_rect(draw, cx - 1, py + 4, cx + 2, py + 4, (238, 64, 98))
        px_rect(draw, cx, py + 5, cx + 1, py + 5, (238, 64, 98))


def load_arabic_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
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
    caption_raw = str(beat.get("caption", "")).strip()
    highlight_raw = str(beat.get("highlight_word", "")).strip()

    shaped_caption = shape_ar(caption_raw)
    shaped_highlight = shape_ar(highlight_raw or caption_raw)
    font_cap = load_arabic_font(64)
    out_frames: list[Path] = []

    for f_i in range(num_frames):
        img = Image.new("RGB", (W, H), (18, 18, 32))
        draw = ImageDraw.Draw(img)

        if scene_name == "corridor":
            draw_corridor(draw, f_i)
        elif scene_name == "room_night":
            draw_room_night(draw, f_i)
        elif scene_name == "street_day":
            draw_street_day(draw, f_i)
        else:
            draw_alley_night(draw, f_i)

        drift = int(math.sin((f_i / max(num_frames, 1)) * math.pi) * 4)
        char_x = 16 + drift
        char_y = 69
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
            shadow_x = 34 - (drift // 2)
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

        if shaped_caption:
            cap_y = 1360
            hl_bbox = draw.textbbox((0, 0), shaped_highlight, font=font_cap)
            hl_w = max((hl_bbox[2] - hl_bbox[0]) + 56, 200)
            pill_w = min(hl_w, 720)
            pill_x0 = (W - pill_w) // 2 + int(math.sin(f_i * 0.6) * 14)
            pill_y0 = cap_y + 10
            pill_x1 = pill_x0 + pill_w
            pill_y1 = pill_y0 + 108

            for px_step in range(pill_x0, pill_x1, 4):
                ratio = (px_step - pill_x0) / max(pill_w, 1)
                r_c = int(126 + ratio * 54)
                g_c = int(206 - ratio * 56)
                b_c = int(156 + ratio * 74)
                draw.rectangle([px_step, pill_y0, min(px_step + 4, pill_x1), pill_y1], fill=(r_c, g_c, b_c))

            draw.rounded_rectangle(
                [pill_x0, pill_y0, pill_x1, pill_y1],
                radius=22,
                outline=(255, 255, 255),
                width=3,
            )

            bbox = draw.textbbox((0, 0), shaped_caption, font=font_cap)
            tw = bbox[2] - bbox[0]
            tx = (W - tw) // 2
            ty = cap_y + 22

            for ox in range(-4, 5, 2):
                for oy in range(-4, 5, 2):
                    if ox != 0 or oy != 0:
                        draw.text((tx + ox, ty + oy), shaped_caption, font=font_cap, fill=(10, 10, 16))
            draw.text((tx, ty), shaped_caption, font=font_cap, fill=(255, 255, 255))

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


async def generate_voice_for_beats(beats: list[dict], audio_path: Path) -> list[float]:
    durations: list[float] = []
    part_files: list[Path] = []
    voice = "ar-SA-HamedNeural"

    for idx, b in enumerate(beats):
        line = str(b.get("caption", "")).strip() or "ملفات سرية"
        part_p = OUT_DIR / f"voice_part_{idx:02d}.mp3"
        comm = edge_tts.Communicate(text=line, voice=voice, rate="+0%")
        with open(part_p, "wb") as f:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
        part_files.append(part_p)
        durations.append(get_audio_duration(part_p))

    concat_list = OUT_DIR / "audio_parts.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for p in part_files:
            f.write(f"file '{p.resolve()}'\n")

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(audio_path),
        ],
        check=True,
    )
    return durations


def create_thumbnail(first_frame: Path, thumb_text: str, out_thumb: Path) -> None:
    im = Image.open(first_frame).convert("RGB").resize((W, H), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(im)
    draw.rectangle([0, 1520, W, H], fill=(12, 14, 24))
    shaped_thumb = shape_ar(thumb_text)
    font_t = load_arabic_font(78)
    bbox = draw.textbbox((0, 0), shaped_thumb, font=font_t)
    tw = bbox[2] - bbox[0]
    tx = (W - tw) // 2
    draw.text((tx + 4, 1644), shaped_thumb, font=font_t, fill=(0, 0, 0))
    draw.text((tx, 1640), shaped_thumb, font=font_t, fill=(255, 220, 72))
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

    clean_tags = [str(k).lstrip("#").strip()[:30] for k in keywords if str(k).strip()][:15]
    body = {
        "snippet": {
            "title": title[:95],
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
        "title": payload.get("title", ""),
        "description": formatted_description,
        "beats": beats,
    }
    (OUT_DIR / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    audio_path = OUT_DIR / "voice.mp3"
    beat_durations = asyncio.run(generate_voice_for_beats(beats, audio_path))

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
