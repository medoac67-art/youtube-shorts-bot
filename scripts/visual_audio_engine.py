import base64
import hashlib
import math
import os
import random
import re
import subprocess
import urllib.parse
from pathlib import Path

import edge_tts
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from story_engine import (
    fix_arabic_spelling,
    format_tts_for_provider,
    normalize_brackets_in_speech_text,
    strip_emotes_for_caption,
)

OUT_DIR = Path("output")
ART_CACHE_DIR = OUT_DIR / "art_cache"
OUT_DIR.mkdir(parents=True, exist_ok=True)
ART_CACHE_DIR.mkdir(parents=True, exist_ok=True)

W, H = 1080, 1920
GRID_W, GRID_H = 270, 480
PX = 4

DEFAULT_FISH_KEYS = [
    "sk-fish-6YUfu-lSRlfppfamSTY-NrEAM1Y-D6fp-hMFWbOIfzI",
    "sk-fish-6iO-qu3Rvu4riNr2yY321BirGup5LPvsu3_pwJNgApE",
    "sk-fish-3Oojt9SvyaueZKY-Tm38z6i6BT4wRCneQdcWHGL4HUg",
    "sk-fish-LGMwCDbpMZwCYU_bBXAYOD0V-UKy16Ut3iEfXZ9T3s8",
]


def px_rect(draw: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int, fill, outline=None) -> None:
    if x1 < x0 or y1 < y0:
        return
    draw.rectangle([x0 * PX, y0 * PX, (x1 + 1) * PX - 1, (y1 + 1) * PX - 1], fill=fill, outline=outline)


def draw_dither_band(draw: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int, c1: tuple[int, int, int], c2: tuple[int, int, int], step: int = 2) -> None:
    for y in range(y0, y1 + 1, step):
        for x in range(x0, x1 + 1, step):
            col = c1 if ((x + y) // step) % 2 == 0 else c2
            px_rect(draw, x, y, min(x1, x + step - 1), min(y1, y + step - 1), col)


def draw_procedural_pixel_art(env: str, time_of_day: str, has_shadow: bool, coat_color: tuple[int, int, int], seed: int) -> Image.Image:
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
    env = str(beat.get("env", "mountain_pass")).strip()
    time_of_day = str(beat.get("time", "fog")).strip()
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

    try:
        encoded = urllib.parse.quote(prompt_text)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width={W}&height={H}&seed={story_seed + beat_idx}&nologo=true"
        resp = requests.get(url, timeout=25)
        if resp.ok and resp.content and len(resp.content) > 4096:
            tmp_raw = ART_CACHE_DIR / f"{cache_key}_raw.bin"
            tmp_raw.write_bytes(resp.content)
            art = Image.open(tmp_raw).convert("RGB").resize((W, H), Image.Resampling.LANCZOS)
            small = art.resize((GRID_W, GRID_H), Image.Resampling.BILINEAR)
            pixelated = small.resize((W, H), Image.Resampling.NEAREST)
            pixelated.save(cached_file)
            return pixelated
    except Exception:
        pass

    fallback_art = draw_procedural_pixel_art(
        env=env,
        time_of_day=time_of_day,
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
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True,
        text=True,
        check=True,
    )
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
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "model": "s2.1-pro-free"},
                json={"text": fish_script, "format": "mp3"},
                timeout=90,
            )
            if resp.ok and resp.content and len(resp.content) > 1024:
                audio_path.write_bytes(resp.content)
                print(f"TTS succeeded using Fish Audio key #{idx}.")
                return True
        except Exception as e:
            print(f"Fish Audio key #{idx} error: {e}")
    return False


def try_google_ai_studio_tts(google_script: str, audio_path: Path) -> bool:
    google_key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not google_key:
        return False
    for model_name in ["gemini-3.8-flash-tts", "gemini-2.5-flash-preview-tts"]:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={google_key}"
            prompt_text = (
                f"اقرأ النص التالي باللغة العربية بنبرة هادئة وغامضة ومتزنة، مع التزام الفواصل وعلامات الترقيم والتعبيرات داخل الأقواس الزاوية مثل <whispering> و<sighing> دون حماس مفرط:\n\n{google_script}"
                if "3.8" in model_name
                else f"اقرأ هذه القصة بنبرة هادئة وغامضة وواضحة مع الوقف عند الفواصل وعلامات الترقيم:\n\n{strip_emotes_for_caption(google_script)}"
            )
            payload = {
                "contents": [{"parts": [{"text": prompt_text}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Puck"}}},
                },
            }
            r = requests.post(url, json=payload, timeout=90)
            if r.ok:
                parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                for p in parts:
                    inline = p.get("inlineData") or p.get("inline_data")
                    if inline and inline.get("data"):
                        raw_audio = base64.b64decode(inline["data"])
                        tmp_audio = OUT_DIR / "google_tts_raw.bin"
                        tmp_audio.write_bytes(raw_audio)
                        mime = str(inline.get("mimeType") or inline.get("mime_type") or "")
                        cmd = (
                            ["ffmpeg", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", str(tmp_audio), "-c:a", "libmp3lame", "-b:a", "192k", str(audio_path)]
                            if ("pcm" in mime.lower() or "l16" in mime.lower())
                            else ["ffmpeg", "-y", "-i", str(tmp_audio), "-c:a", "libmp3lame", "-b:a", "192k", str(audio_path)]
                        )
                        subprocess.run(cmd, check=True)
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
            comm = edge_tts.Communicate(text=edge_script, voice="ar-SA-HamedNeural", rate="-4%")
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
    tx = (W - (bbox[2] - bbox[0])) // 2
    draw_outlined_text(draw, (tx, 1650), clean_t, font=font_t, fill=(255, 220, 72), stroke_fill=(10, 12, 20), stroke_width=4)
    im.save(out_thumb)


def render_video_from_beats(beats: list[dict], beat_durations: list[float], audio_path: Path, story_seed: int, out_video: Path) -> None:
    fps = 12
    all_frames: list[Path] = []
    for idx, (beat, dur) in enumerate(zip(beats, beat_durations)):
        all_frames.extend(render_beat_frames(beat, idx, dur, story_seed=story_seed, fps=fps))

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
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(frames_txt), "-i", str(audio_path),
            "-vf", "scale=1080:1920:flags=neighbor,eq=contrast=1.06:saturation=1.12,format=yuv420p",
            "-r", "24", "-c:v", "libx264", "-preset", "fast", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out_video),
        ],
        check=True,
    )
