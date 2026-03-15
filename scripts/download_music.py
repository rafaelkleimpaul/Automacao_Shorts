"""
download_music.py – Download audio from YouTube and save as MP3.

Usage:
  python download_music.py <url> [url2 ...] [--profile PROFILE]

Examples:
  # Download to data/assets/music/ (default)
  python download_music.py https://www.youtube.com/watch?v=XXXXX

  # Download to data/assets/music/mindset/
  python download_music.py https://www.youtube.com/watch?v=XXXXX --profile mindset

  # Multiple links at once
  python download_music.py https://youtu.be/AAA https://youtu.be/BBB --profile finance

Requirements:
  pip install yt-dlp
  brew install ffmpeg  (macOS)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    print("ERROR: yt-dlp not installed. Run: pip install yt-dlp")
    sys.exit(1)

DATA_ROOT  = Path(__file__).parent.parent / "data"
MUSIC_ROOT = DATA_ROOT / "assets" / "music"


def download(urls: list[str], profile: str | None) -> None:
    out_dir = MUSIC_ROOT / profile if profile else MUSIC_ROOT
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Output folder : {out_dir}")
    print(f"Tracks        : {len(urls)}\n")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "quiet": False,
        "no_warnings": False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for url in urls:
            print(f"→ Downloading: {url}")
            try:
                ydl.download([url])
            except Exception as exc:
                print(f"  ERROR: {exc}")

    print(f"\nDone. Files saved to: {out_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download YouTube audio as MP3 for the pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("urls", nargs="+", help="YouTube URL(s) to download")
    parser.add_argument(
        "--profile", "-p",
        default=None,
        metavar="PROFILE",
        help="Music sub-folder (e.g. mindset, finance). Defaults to root music folder.",
    )
    args = parser.parse_args()
    download(args.urls, args.profile)
