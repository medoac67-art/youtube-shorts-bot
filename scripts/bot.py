import asyncio
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

import edge_tts
import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
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
            return extract_json(r.json()["choices"][0]["message"]["content"])

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
            return extract_json(r.json()["choices"][0]["message"]["content"])

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
            return extract_json(r.json()["choices"][0]["message"]["content"])

    if google_key:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={google_key}",
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )
        if r.ok:
            return extract_json(r.json()["candidates"][0]["content"]["parts"][0]["text"])

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
    small = (
        base.resize((135, 240), Image.Resampling.BILINEAR)
        .quantize(colors=32, method=Image.Quantize.MEDIANCUT)
        .convert("RGB")
    )
    return small.resize((target_w, target_h), Image.Resampling.NEAREST)


def fetch_pixel_scene(scene_prompt: str, out_path: Path) -> None:
    encoded = quote(f"{scene_prompt}, pixel art, 16-bit, crisp edges")
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=540&height=960&nologo=true"
    try:
        r = requests.get(url, timeout=45)
        if r.ok and r.headers.get("content-type", "").startswith("image"):
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

    Image.alpha_composite(im, overlay).convert("RGB").save(out_thumb)


async def generate_voice_and_srt(text: str, audio_path: Path, srt_path: Path) -> None:
    voice = "ar-SA-HamedNeural"
    communicate = edge_tts.Communicate(text=text, voice=voice, rate="+5%")
    submaker = edge_tts.SubMaker()

    with open(audio_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                submaker.feed(chunk)

    srt_content = submaker.get_srt()
    if srt_content.strip():
        srt_path.write_text(srt_content, encoding="utf-8")
    else:
        srt_path.write_text(
            f"1\n00:00:00,000 --> 00:00:10,000\n{text[:80]}\n",
            encoding="utf-8",
        )


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
    return max(float(res.stdout.strip()), 8.0)


def render_short_video(scene_paths: list[Path], audio_path: Path, srt_path: Path, out_video: Path) -> None:
    duration = get_audio_duration(audio_path)
    per_scene = max(duration / max(len(scene_paths), 1), 2.0)

    concat_list = OUT_DIR / "scenes.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for p in scene_paths:
            f.write(f"file '{p.resolve()}'\n")
            f.write(f"duration {per_scene:.2f}\n")
        f.write(f"file '{scene_paths[-1].resolve()}'\n")

    silent_video = OUT_DIR / "silent_scenes.mp4"
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
            "-vf",
            "scale=1080:1920,format=yuv420p",
            "-r",
            "30",
            "-t",
            f"{duration:.2f}",
            str(silent_video),
        ],
        check=True,
    )

    vf_filter = (
        f"subtitles={srt_path.resolve()}:"
        "force_style='FontName=DejaVu Sans,FontSize=18,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=3,Outline=2'"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(silent_video),
            "-i",
            str(audio_path),
            "-vf",
            vf_filter,
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
    privacy_status: str = "unlisted",
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

    print("Uploaded to YouTube! Video ID:", video_id)
    return video_id


def main() -> None:
    slot = get_slot_number()
    payload = generate_story_payload(slot)

    formatted_description = build_formatted_description(payload)
    meta = {
        "slot": slot,
        "title": payload.get("title", ""),
        "description": formatted_description,
        "translation": payload.get("translation", ""),
        "story": payload.get("story", ""),
    }
    (OUT_DIR / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    scenes = payload.get("scenes", [])[:4]
    scene_files: list[Path] = []
    for idx, sc in enumerate(scenes, start=1):
        p = OUT_DIR / f"scene_{idx}.png"
        fetch_pixel_scene(str(sc), p)
        scene_files.append(p)

    thumb_path = OUT_DIR / "thumbnail.png"
    create_thumbnail(scene_files[0], str(payload.get("thumbnail_text", "PIXEL STORY")), thumb_path)

    audio_path = OUT_DIR / "voice.mp3"
    srt_path = OUT_DIR / "captions.srt"
    asyncio.run(generate_voice_and_srt(str(payload.get("story", "")), audio_path, srt_path))

    video_path = OUT_DIR / "short_final.mp4"
    render_short_video(scene_files, audio_path, srt_path, video_path)

    upload_to_youtube(
        video_path=video_path,
        thumb_path=thumb_path,
        title=str(meta["title"]),
        description=formatted_description,
        keywords=payload.get("keywords", []),
        privacy_status="unlisted",
    )

    print("DONE! Generated:", video_path, thumb_path)
    print("\n--- DESCRIPTION ---\n" + formatted_description)
    print("\n--- TRANSLATION ---\n" + meta["translation"])


if __name__ == "__main__":
    main()
