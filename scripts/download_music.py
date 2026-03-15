"""
download_music.py – Download audio from YouTube (videos or playlists) and save as MP3.

Usage:
  python scripts/download_music.py <url> [url2 ...] [--profile PROFILE] [options]

Examples:
  # Single video
  python scripts/download_music.py https://www.youtube.com/watch?v=XXXXX --profile mindset

  # Full playlist
  python scripts/download_music.py https://www.youtube.com/playlist?list=XXXXX --profile mindset

  # Only first 10 tracks of a playlist
  python scripts/download_music.py https://www.youtube.com/playlist?list=XXXXX --profile mindset --items 1-10

  # Video URL that belongs to a playlist — download only that video, not the whole playlist
  python scripts/download_music.py "https://www.youtube.com/watch?v=XXX&list=YYY" --no-playlist

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


def download(
    urls: list[str],
    profile: str | None,
    no_playlist: bool = False,
    items: str | None = None,
) -> None:
    out_dir = MUSIC_ROOT / profile if profile else MUSIC_ROOT
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Output folder : {out_dir}")
    if no_playlist:
        print("Mode          : single video (playlist ignored)")
    elif items:
        print(f"Playlist items: {items}")
    else:
        print("Mode          : full download (playlists expanded)")
    print()

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
        "noplaylist": no_playlist,
        "quiet": False,
        "no_warnings": False,
        "ignoreerrors": True,   # skip unavailable videos in a playlist
    }

    if items:
        ydl_opts["playlist_items"] = items

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for url in urls:
            print(f"→ {url}")
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
    parser.add_argument("urls", nargs="+", help="YouTube URL(s) — video or playlist")
    parser.add_argument(
        "--profile", "-p",
        default=None,
        metavar="PROFILE",
        help="Music sub-folder (e.g. mindset, finance). Defaults to root music folder.",
    )
    parser.add_argument(
        "--no-playlist",
        action="store_true",
        help="When URL contains a playlist ID, download only the single video.",
    )
    parser.add_argument(
        "--items",
        default=None,
        metavar="RANGE",
        help="Playlist items to download, e.g. '1-10', '1,3,5' or '1-5,7'. "
             "Ignored if --no-playlist is set.",
    )
    args = parser.parse_args()
    download(args.urls, args.profile, no_playlist=args.no_playlist, items=args.items)
