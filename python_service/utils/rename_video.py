"""
rename_video.py – AI-powered asset renamer for b-roll/image files.

Uses a vision-capable LLM (Ollama llava, OpenAI gpt-4o-mini, etc.) to
analyse frames extracted from each video/image and generate a descriptive
slug filename that makes it easy to pick the right asset when assembling shorts.

Usage (standalone, outside Docker):
  python rename_video.py /data/assets/broll/finance --theme finance --apply
  python rename_video.py /data/assets/broll --dry-run

Env vars (all optional — mirrors .env.example):
  VISION_ENDPOINT   Ollama /api/generate or OpenAI-compat /v1/chat/completions
                    Defaults to http://localhost:11434/api/generate
  VISION_MODEL      Vision model name. Defaults to llava
  LLM_TIMEOUT       Request timeout in seconds. Defaults to 60
"""

from __future__ import annotations

import base64
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

VISION_ENDPOINT: str = os.environ.get(
    "VISION_ENDPOINT", "http://localhost:11434/api/generate"
)
VISION_MODEL: str = os.environ.get("VISION_MODEL", "llava")
TIMEOUT: int = int(os.environ.get("LLM_TIMEOUT", "60"))

SUPPORTED_VIDEO = {".mp4", ".mov", ".mkv", ".webm"}
SUPPORTED_IMAGE = {".jpg", ".jpeg", ".png", ".webp"}

STOPWORDS = {
    "video", "image", "photo", "footage", "clip", "stock",
    "hd", "4k", "1080p", "new", "final", "render", "export",
    "edit", "edited", "copy", "frame", "asset",
}

VISION_PROMPT = (
    "You are a video asset tagging assistant. "
    "Look at these frames and reply with ONLY a JSON object in this exact format:\n"
    '{"keywords": ["word1", "word2", "word3", "word4", "word5"]}\n'
    "Rules:\n"
    "- 3 to 6 short English keywords describing the main subject, action, or setting\n"
    "- All lowercase, no spaces (use underscores if needed)\n"
    "- Specific and visual: prefer 'stock_chart' over 'finance', 'person_counting_money' over 'money'\n"
    "- No generic words like video/image/footage/clip/stock\n"
    "- Return ONLY the JSON object, nothing else"
)


# ─────────────────────────────────────────────────────────────────────────────
# Slug helpers
# ─────────────────────────────────────────────────────────────────────────────

def _slugify(word: str) -> str:
    word = word.strip().lower()
    word = re.sub(r"[^a-z0-9]+", "_", word)
    return re.sub(r"_+", "_", word).strip("_")


def slugify_keywords(words: List[str], max_words: int = 5) -> str:
    seen: set = set()
    result: List[str] = []
    for w in words:
        s = _slugify(w)
        if s and s not in STOPWORDS and s not in seen:
            seen.add(s)
            result.append(s)
    return "_".join(result[:max_words]) or "asset"


def ensure_unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    i = 2
    while True:
        candidate = parent / f"{stem}_v{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


# ─────────────────────────────────────────────────────────────────────────────
# Frame extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_frames(video_path: Path, out_dir: Path, n_frames: int = 3) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True,
    )
    try:
        duration = float(probe.stdout.strip())
    except Exception:
        duration = 0.0

    ts = [duration * 0.2, duration * 0.5, duration * 0.8] if duration > 5 else [1.0, 2.0, 3.0]

    frames: List[Path] = []
    for idx in range(n_frames):
        t = ts[min(idx, len(ts) - 1)]
        out_file = out_dir / f"frame_{idx + 1}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", str(video_path),
             "-frames:v", "1", "-q:v", "2", str(out_file)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if out_file.exists():
            frames.append(out_file)
    return frames


# ─────────────────────────────────────────────────────────────────────────────
# Vision LLM callers
# ─────────────────────────────────────────────────────────────────────────────

def _images_to_b64(paths: List[Path]) -> List[str]:
    return [base64.b64encode(p.read_bytes()).decode() for p in paths[:3]]


def _call_ollama_vision(images_b64: List[str]) -> List[str]:
    """POST to Ollama /api/generate with images field (llava, moondream, etc.)."""
    payload = {
        "model": VISION_MODEL,
        "prompt": VISION_PROMPT,
        "images": images_b64,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2, "num_predict": 200},
    }
    r = requests.post(VISION_ENDPOINT, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    raw = r.json().get("response", "")
    return _parse_keywords(raw)


def _call_openai_vision(images_b64: List[str]) -> List[str]:
    """POST to any OpenAI-compatible /v1/chat/completions with vision support."""
    content = [{"type": "text", "text": VISION_PROMPT}]
    for b64 in images_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
        })
    payload = {
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
        "max_tokens": 200,
    }
    r = requests.post(VISION_ENDPOINT, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    raw = r.json()["choices"][0]["message"]["content"]
    return _parse_keywords(raw)


def _parse_keywords(raw: str) -> List[str]:
    raw = raw.strip()
    try:
        data = json.loads(raw)
        return [str(k).lower() for k in (data.get("keywords") or []) if k]
    except Exception:
        pass
    m = re.search(r"\{.*?\}", raw, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group())
            return [str(k).lower() for k in (data.get("keywords") or []) if k]
        except Exception:
            pass
    # last resort: grab quoted tokens
    return [w.lower() for w in re.findall(r'"([^"]{2,30})"', raw)][:6]


def _select_vision_caller():
    if "chat/completions" in VISION_ENDPOINT.lower():
        return _call_openai_vision
    return _call_ollama_vision


# ─────────────────────────────────────────────────────────────────────────────
# Fallback (no AI)
# ─────────────────────────────────────────────────────────────────────────────

def keywords_from_path(file_path: Path) -> List[str]:
    """Derive keywords from folder names + current filename (no AI)."""
    parts = list(file_path.parts[-3:-1])
    name_tokens = re.sub(r"[^a-zA-Z0-9]+", " ", file_path.stem).split()
    return [t.lower() for t in (parts + name_tokens) if t]


# ─────────────────────────────────────────────────────────────────────────────
# Core rename logic
# ─────────────────────────────────────────────────────────────────────────────

def _already_has_good_name(path: Path) -> bool:
    """Skip files whose name already looks like a descriptive slug."""
    ext = path.suffix.lower()
    return bool(re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+){2,}" + re.escape(ext), path.name))


def rename_assets(
    root_dir: str,
    theme_hint: Optional[str] = None,
    dry_run: bool = True,
    skip_existing: bool = True,
    n_frames: int = 3,
) -> None:
    root = Path(root_dir)
    if not root.exists():
        raise FileNotFoundError(f"Directory not found: {root}")

    caller = _select_vision_caller()
    rename_log = root / "rename_map.csv"
    rows: List[tuple] = []

    candidates = [
        p for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in (SUPPORTED_VIDEO | SUPPORTED_IMAGE)
        and p.name != "rename_map.csv"
    ]

    if skip_existing:
        candidates = [p for p in candidates if not _already_has_good_name(p)]

    print(f"Found {len(candidates)} file(s) to process.")

    for idx, path in enumerate(candidates, 1):
        ext = path.suffix.lower()
        print(f"[{idx}/{len(candidates)}] {path.name}", end=" ... ", flush=True)

        keywords: List[str] = []
        tmp_dir = root / ".tmp_frames" / path.stem

        try:
            if ext in SUPPORTED_VIDEO:
                frames = extract_frames(path, tmp_dir, n_frames)
            else:
                # Image: copy as frame directly
                tmp_dir.mkdir(parents=True, exist_ok=True)
                frame_copy = tmp_dir / "frame_1.jpg"
                shutil.copy(path, frame_copy)
                frames = [frame_copy]

            if frames:
                keywords = caller(_images_to_b64(frames))

        except Exception as e:
            print(f"[vision error: {e}]", end=" ", flush=True)

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        if not keywords:
            keywords = keywords_from_path(path)
            print("[fallback]", end=" ", flush=True)

        if theme_hint:
            keywords = [theme_hint.lower()] + keywords

        new_stem = slugify_keywords(keywords, max_words=5)
        new_path = ensure_unique_path(path.with_name(new_stem + ext))
        rows.append((str(path), str(new_path)))
        print(f"-> {new_path.name}")

        if not dry_run:
            path.rename(new_path)

    # Save rename log
    with open(rename_log, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["old_path", "new_path"])
        w.writerows(rows)

    mode = "DRY-RUN (no changes made)" if dry_run else "APPLIED"
    print(f"\n[{mode}] {len(rows)} file(s) processed.")
    print(f"Rename map saved to: {rename_log}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="AI-powered video/image renamer for b-roll assets.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview (dry-run) on the finance b-roll folder:
  python rename_video.py /data/assets/broll/finance

  # Apply renaming with a theme prefix:
  python rename_video.py /data/assets/broll/finance --theme finance --apply

  # Use a different Ollama vision model:
  VISION_MODEL=llava-llama3 python rename_video.py /data/assets/broll --apply

  # Use an OpenAI-compatible endpoint (e.g. LM Studio or gpt-4o-mini):
  VISION_ENDPOINT=http://localhost:1234/v1/chat/completions \\
  VISION_MODEL=local-llava python rename_video.py /data/assets/broll --apply

  # Re-analyse ALL files, even those that already look like slugs:
  python rename_video.py /data/assets/broll --no-skip --apply
        """,
    )
    parser.add_argument("root_dir", help="Folder to scan recursively (e.g. /data/assets/broll)")
    parser.add_argument("--theme", default=None, help="Prefix for every slug, e.g. 'finance'")
    parser.add_argument("--apply", action="store_true", help="Perform the actual rename (default: dry-run preview)")
    parser.add_argument("--no-skip", action="store_true", help="Re-analyse files that already look like slugs")
    parser.add_argument("--frames", type=int, default=3, metavar="N", help="Frames to extract per video (default: 3)")
    args = parser.parse_args()

    print(f"Vision endpoint : {VISION_ENDPOINT}")
    print(f"Vision model    : {VISION_MODEL}")
    print(f"Theme hint      : {args.theme or '(none)'}")
    print(f"Mode            : {'APPLY' if args.apply else 'DRY-RUN'}\n")

    try:
        rename_assets(
            root_dir=args.root_dir,
            theme_hint=args.theme,
            dry_run=not args.apply,
            skip_existing=not args.no_skip,
            n_frames=args.frames,
        )
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
