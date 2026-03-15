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
import re
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

W, H, FPS = 1080, 1920, 30
MUSIC_VOLUME = 0.08          # 8 % — barely audible under voice
VOICE_VOLUME = 1.0
VIDEO_EXTS   = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
IMAGE_EXTS   = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

FONTS_DIR = Path(os.environ.get("DATA_ROOT", "/data")) / "assets" / "fonts"

# ── Subtitle style (values are real pixels at PlayResY=1920) ─────────────────
# Tweak these to change the look without touching any FFmpeg flags.
SUB_FONT        = "Ariali.ttf"       # font family (Arial is embedded via libass fallback)
SUB_SIZE        = 42           # px — real pixel height on the 1920-tall frame
SUB_BOLD        = True
SUB_OUTLINE     = 1.8           # px — thin outline for readability on any background
SUB_SHADOW      = 0.0           # px — 0 = no shadow
SUB_ALIGNMENT   = 2             # 2=bottom-center  5=middle-center  8=top-center
SUB_MARGIN_V    = 180           # px from the aligned edge (bottom when Alignment=2)


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


def _srt_to_ass(srt_path: Path) -> Path:
    """
    Convert an SRT file to a fully-styled ASS file.

    PlayResX/PlayResY are set to the actual output resolution (1080×1920) so
    SUB_SIZE is always in real screen pixels — no hidden scaling surprises.
    """
    ass_path = srt_path.with_suffix(".ass")

    font_file = FONTS_DIR / "Arial.ttf"
    fontname   = SUB_FONT
    extra_font = f"FontFile={font_file}," if font_file.exists() else ""

    bold_flag  = -1 if SUB_BOLD else 0

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {W}\n"
        f"PlayResY: {H}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{fontname},{SUB_SIZE},"
        f"&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        f"{bold_flag},0,0,0,100,100,0,0,"
        f"1,{SUB_OUTLINE:.2f},{SUB_SHADOW:.2f},"
        f"{SUB_ALIGNMENT},10,10,{SUB_MARGIN_V},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    srt_text = srt_path.read_text(encoding="utf-8")
    events: list[str] = []

    for block in re.split(r"\n\n+", srt_text.strip()):
        lines = [l for l in block.strip().splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        tc_line = next((l for l in lines if "-->" in l), None)
        if tc_line is None:
            continue
        tc_idx = lines.index(tc_line)
        text   = r"\N".join(lines[tc_idx + 1:])

        m = re.match(
            r"(\d+):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2}),(\d{3})",
            tc_line,
        )
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = m.groups()
        start = f"{int(h1)}:{m1}:{s1}.{ms1[:2]}"
        end   = f"{int(h2)}:{m2}:{s2}.{ms2[:2]}"

        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    logger.debug("ASS subtitle written: %s (%d events)", ass_path, len(events))
    return ass_path


def _burn_subtitles(
    video_path: Path,
    audio_path: Path,
    srt_path: Path,
    output_path: Path,
    total_duration: float,
) -> Path:
    """Combine background video + mixed audio and burn ASS subtitles."""
    ass_path = _srt_to_ass(srt_path)
    ass_str  = str(ass_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-vf", f"ass='{ass_str}'",
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


# ── Quote video settings ──────────────────────────────────────────────────────
QUOTE_OUTLINE      = 3.0
QUOTE_SHADOW       = 1.0
QUOTE_ALIGNMENT    = 5      # middle-center
QUOTE_MUSIC_VOLUME = 0.70   # music is the only audio — keep it audible
QUOTE_MAX_LINE     = 15     # chars per wrapped line


def _wrap_phrase(text: str, max_chars: int = QUOTE_MAX_LINE) -> str:
    """Wrap phrase at word boundaries; returns ASS \\N-separated lines."""
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        width = sum(len(w) for w in current) + len(current)
        if current and width + len(word) > max_chars:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return r"\N".join(lines)


def _phrase_to_ass(phrase: str, duration: float, ass_path: Path) -> Path:
    """Write an ASS file displaying the phrase centred for the full video duration."""
    font_size = 90 if len(phrase) <= 20 else (80 if len(phrase) <= 40 else 70)
    bold_flag = -1

    def _tc(secs: float) -> str:
        h = int(secs // 3600)
        m = int((secs % 3600) // 60)
        s = secs % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {W}\n"
        f"PlayResY: {H}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Quote,Arial,{font_size},"
        "&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        f"{bold_flag},0,0,0,100,100,4,0,"
        f"1,{QUOTE_OUTLINE:.1f},{QUOTE_SHADOW:.1f},"
        f"{QUOTE_ALIGNMENT},60,60,60,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    wrapped = _wrap_phrase(phrase)
    event   = f"Dialogue: 0,{_tc(0.3)},{_tc(duration - 0.2)},Quote,,0,0,0,,{wrapped}\n"
    ass_path.write_text(header + event, encoding="utf-8")
    return ass_path


def _build_music_only_audio(
    music_path: Optional[Path],
    total_duration: float,
    output_path: Path,
) -> Optional[Path]:
    """Build audio track from music only (no voice). Returns None if no music available."""
    if not music_path or not music_path.exists():
        return None
    cmd = [
        "ffmpeg", "-y",
        "-i", str(music_path),
        "-filter_complex",
        f"[0:a]volume={QUOTE_MUSIC_VOLUME},aloop=loop=-1:size=2e+09[out]",
        "-map", "[out]",
        "-c:a", "aac", "-b:a", "128k",
        "-t", str(total_duration),
        str(output_path),
    ]
    _run(cmd, "music-only audio")
    return output_path


def render_quote_video(
    job_dir: Path,
    phrase: str,
    broll_assets: list,
    music_path: Optional[Path],
    total_duration: float,
) -> Path:
    """
    Render a silent quote video:
      1. Build background (video b-roll, image slideshow, or black)
      2. Music-only audio track
      3. Burn centred phrase text → final.mp4
    """
    video_files = [a.path for a in broll_assets if a.kind == "video"]
    image_files = [a.path for a in broll_assets if a.kind == "image"]

    bg_path    = job_dir / "background.mp4"
    audio_path = job_dir / "audio_mix.aac"
    ass_path   = job_dir / "phrase.ass"
    final_path = job_dir / "final.mp4"

    # Step 1: Background
    if video_files:
        logger.info("Building background from %d video files", len(video_files))
        _build_background_from_videos(video_files, total_duration, bg_path)
    elif image_files:
        logger.info("Building slideshow from %d images", len(image_files))
        _build_background_from_images(image_files, total_duration, bg_path)
    else:
        logger.warning("No b-roll assets; using solid black background")
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-t", str(total_duration),
            "-pix_fmt", "yuv420p",
            str(bg_path),
        ]
        _run(cmd, "black background")

    # Step 2: Music only
    has_audio = _build_music_only_audio(music_path, total_duration, audio_path)

    # Step 3: Phrase overlay + final encode
    _phrase_to_ass(phrase, total_duration, ass_path)
    ass_str = str(ass_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

    if has_audio:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(bg_path),
            "-i", str(audio_path),
            "-vf", f"ass='{ass_str}'",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "copy",
            "-r", str(FPS), "-s", f"{W}x{H}", "-pix_fmt", "yuv420p",
            "-t", str(total_duration), "-movflags", "+faststart",
            str(final_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(bg_path),
            "-vf", f"ass='{ass_str}'",
            "-map", "0:v:0",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-an",
            "-r", str(FPS), "-s", f"{W}x{H}", "-pix_fmt", "yuv420p",
            "-t", str(total_duration), "-movflags", "+faststart",
            str(final_path),
        ]
    _run(cmd, "quote overlay + final render")

    size_mb = final_path.stat().st_size / (1024 * 1024)
    logger.info("Quote video: %s (%.1f MB)", final_path, size_mb)
    return final_path


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
