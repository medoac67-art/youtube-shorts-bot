import re
import subprocess
from pathlib import Path


def _probe_duration(media_path: Path) -> float:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(media_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(res.stdout.strip())


def validate_short_package(video_path: Path, audio_path: Path, title: str, description: str, beats: list[dict]) -> tuple[bool, list[str]]:
    issues: list[str] = []

    if not video_path.exists() or video_path.stat().st_size < 100_000:
        issues.append("ملف الفيديو غير موجود أو حجمه صغير جداً.")
    if not audio_path.exists() or audio_path.stat().st_size < 2048:
        issues.append("ملف الصوت غير موجود أو حجمه صغير جداً.")

    if video_path.exists():
        try:
            dur = _probe_duration(video_path)
            if dur < 10.0 or dur > 180.0:
                issues.append(f"مدة الفيديو غير مناسبة للشورتس ({dur:.1f} ثانية).")
        except Exception as e:
            issues.append(f"تعذر قراءة مدة الفيديو: {e}")

    title_tags = re.findall(r"#[^\s#]+", title or "")
    if len(title_tags) > 3:
        issues.append(f"عدد الهاشتاغات في العنوان أكبر من 3 ({len(title_tags)}).")

    if not description or "1. عنوان المقطع:" not in description or "5. الهاشتاغات:" not in description:
        issues.append("الوصف يفتقد لأحد الأقسام الخمسة المطلوبة.")

    if len(beats) < 8:
        issues.append(f"عدد مقاطع القصة قليل جداً ({len(beats)}).")

    return (len(issues) == 0), issues
