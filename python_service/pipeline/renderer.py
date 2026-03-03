"""
FFmpeg-based video renderer.

Output spec:
  - Resolution : 1080x1920 (9:16 vertical)
  - Frame rate : 30 fps
  - Video codec: libx264, preset fast, CRF 23
  - Audio codec: AAC 128k (mix: voice + background music at low volume)
  - Subtitles  : SRT burn-in via subtitles filter

Input assets:
  - voice.wav        : voice-over (required)
  - captions.srt     : subtitle file (required)
  - broll files      : list of video (.mp4) and/or image (.jpg/.png) files (required)
  - music file       : optional background music (.mp3/.wav)
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

W, H, FPS = 1080, 1920, 30
MUSIC_VOLUME = 0.08          # 8 % — barely audible under voice
VOICE_VOLUME = 1.0
FONT_NAME    = "DejaVu-Sans"        # available in Debian; change if using custom font
FONT_SIZE    = 34
FONT_COLOR   = "white"
OUTLINE_SIZE = 2
VIDEO_EXTS   = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
IMAGE_EXTS   = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

FONTS_DIR = Path(os.environ.get("DATA_ROOT", "/data")) / "assets" / "fonts"


def _ffprobe_duration(path: Path) -> float:
    """Get duration of a media file in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=15)
        return float(out.strip())
    except Exception:
        return 0.0


def _build_background_from_videos(
    asset_paths: list[Path],
    total_duration: float,
    output_path: Path,
) -> Path:
    """
    Concatenate video files (looping if needed) to fill total_duration.
    Scales and crops each clip to 1080x1920.
    Returns output_path.
    """
    scale_filter = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},"
        f"setsar=1,"
        f"fps={FPS}"
    )

    # Build a list file for concat demuxer
    list_file = output_path.parent / "concat_list.txt"
    written_duration = 0.0
    lines: list[str] = []

    while written_duration < total_duration:
        for ap in asset_paths:
            dur = _ffprobe_duration(ap)
            if dur <= 0:
                dur = 5.0
            lines.append(f"file '{ap}'\n")
            written_duration += dur
            if written_duration >= total_duration:
                break

    list_file.write_text("".join(lines), encoding="utf-8")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-vf", scale_filter,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-an",
        "-t", str(total_duration),
        str(output_path),
    ]
    _run(cmd, "build background (video)")
    return output_path


def _build_background_from_images(
    asset_paths: list[Path],
    total_duration: float,
    output_path: Path,
) -> Path:
    """
    Create a slideshow from images.
    Each image is shown for an equal slice of total_duration (Ken Burns off).
    """
    if not asset_paths:
        raise ValueError("No image assets provided for slideshow")

    num_images    = len(asset_paths)
    secs_per_img  = max(2.0, total_duration / num_images)

    scale_filter = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},"
        f"setsar=1"
    )

    # Build individual clips then concat
    clips: list[Path] = []
    for i, img_path in enumerate(asset_paths):
        clip_path = output_path.parent / f"img_clip_{i:03d}.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", str(img_path),
            "-vf", f"{scale_filter},fps={FPS}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-t", str(secs_per_img),
            "-pix_fmt", "yuv420p",
            "-an",
            str(clip_path),
        ]
        _run(cmd, f"image clip {i}")
        clips.append(clip_path)

    # Concat clips
    list_file = output_path.parent / "img_concat.txt"
    list_file.write_text(
        "".join(f"file '{c}'\n" for c in clips), encoding="utf-8"
    )
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy",
        "-t", str(total_duration),
        str(output_path),
    ]
    _run(cmd, "concat image slideshow")
    return output_path


def _build_audio_mix(
    voice_path: Path,
    music_path: Optional[Path],
    total_duration: float,
    output_path: Path,
) -> Path:
    """Mix voice + optional background music into a single AAC track."""
    if music_path and music_path.exists():
        # amix: voice at full volume + music at MUSIC_VOLUME, duration = voice length
        filter_complex = (
            f"[0:a]volume={VOICE_VOLUME}[voice];"
            f"[1:a]volume={MUSIC_VOLUME},aloop=loop=-1:size=2e+09[music];"
            f"[voice][music]amix=inputs=2:duration=first:dropout_transition=3[out]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(voice_path),
            "-i", str(music_path),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-c:a", "aac", "-b:a", "128k",
            "-t", str(total_duration),
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(voice_path),
            "-c:a", "aac", "-b:a", "128k",
            "-t", str(total_duration),
            str(output_path),
        ]
    _run(cmd, "audio mix")
    return output_path


def _burn_subtitles(
    video_path: Path,
    audio_path: Path,
    srt_path: Path,
    output_path: Path,
    total_duration: float,
) -> Path:
    """Combine background video + mixed audio and burn SRT subtitles."""
    # Escape srt path for FFmpeg subtitles filter
    srt_str = str(srt_path).replace(":", "\\:").replace("'", "\\'")

    # Movie-style subtitles: white text, thin outline, soft shadow, no background box
    font_file = FONTS_DIR / "Arial.ttf"
    if font_file.exists():
        force_style = (
            f"FontFile={font_file},"
            f"FontSize={FONT_SIZE},"
            f"PrimaryColour=&H00FFFFFF&,"
            f"OutlineColour=&H00000000&,"
            f"BorderStyle=1,"
            f"Outline={OUTLINE_SIZE},"
            f"Shadow=1,"
            f"Bold=0,"
            f"Alignment=2,"
            f"MarginV=100"
        )
    else:
        force_style = (
            f"FontName={FONT_NAME},"
            f"FontSize={FONT_SIZE},"
            f"PrimaryColour=&H00FFFFFF&,"
            f"OutlineColour=&H00000000&,"
            f"BorderStyle=1,"
            f"Outline={OUTLINE_SIZE},"
            f"Shadow=1,"
            f"Bold=0,"
            f"Alignment=2,"
            f"MarginV=100"
        )

    subtitle_filter = f"subtitles='{srt_str}':force_style='{force_style}'"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-vf", subtitle_filter,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "copy",
        "-r", str(FPS),
        "-s", f"{W}x{H}",
        "-pix_fmt", "yuv420p",
        "-t", str(total_duration),
        "-movflags", "+faststart",
        str(output_path),
    ]
    _run(cmd, "burn subtitles + final render")
    return output_path


def _run(cmd: list[str], step_label: str) -> None:
    logger.info("[FFmpeg:%s] %s", step_label, " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        logger.error("[FFmpeg:%s] stderr:\n%s", step_label, result.stderr[-3000:])
        raise RuntimeError(
            f"FFmpeg failed at step '{step_label}' (exit {result.returncode}).\n"
            f"Command: {' '.join(cmd)}\n"
            f"Stderr (last 3000 chars):\n{result.stderr[-3000:]}"
        )
    logger.debug("[FFmpeg:%s] OK", step_label)


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────

def render_video(
    job_dir: Path,
    voice_path: Path,
    srt_path: Path,
    broll_assets: list,       # list of Asset (NamedTuple) from assets.py
    music_path: Optional[Path],
    total_duration: float,
) -> Path:
    """
    Full render pipeline:
      1. Build background (video b-roll or image slideshow)
      2. Mix audio (voice + music)
      3. Burn subtitles → final.mp4

    Returns the path to final.mp4.
    """
    video_files = [a.path for a in broll_assets if a.kind == "video"]
    image_files = [a.path for a in broll_assets if a.kind == "image"]

    bg_path    = job_dir / "background.mp4"
    audio_path = job_dir / "audio_mix.aac"
    final_path = job_dir / "final.mp4"

    # Step 1: Background
    if video_files:
        logger.info("Building background from %d video files", len(video_files))
        _build_background_from_videos(video_files, total_duration, bg_path)
    elif image_files:
        logger.info("Building slideshow from %d images", len(image_files))
        _build_background_from_images(image_files, total_duration, bg_path)
    else:
        # No assets: generate a solid black background
        logger.warning("No b-roll assets available; using solid black background")
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-t", str(total_duration),
            "-pix_fmt", "yuv420p",
            str(bg_path),
        ]
        _run(cmd, "black background")

    # Step 2: Audio mix
    logger.info("Mixing audio (voice + music)")
    _build_audio_mix(voice_path, music_path, total_duration, audio_path)

    # Step 3: Subtitle burn-in + final encode
    logger.info("Burning subtitles and encoding final video")
    _burn_subtitles(bg_path, audio_path, srt_path, final_path, total_duration)

    size_mb = final_path.stat().st_size / (1024 * 1024)
    logger.info("Final video: %s (%.1f MB)", final_path, size_mb)
    return final_path
