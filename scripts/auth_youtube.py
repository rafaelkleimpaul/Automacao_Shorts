#!/usr/bin/env python3
"""
YouTube OAuth2 Setup — run this ONCE on the host machine.

What it does:
  1. Opens a browser window for Google OAuth authorization
  2. Saves the access + refresh token to ./data/credentials/youtube_token.json

Prerequisites (run on the HOST, not in Docker):
  pip install google-auth-oauthlib google-api-python-client

Before running:
  1. Go to https://console.cloud.google.com/
  2. Create or select a project
  3. APIs & Services → Library → enable "YouTube Data API v3"
  4. APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID
     → Application type: Desktop app
     → Download the JSON file
  5. Rename it to  youtube_client_secrets.json
  6. Place it at:  ./data/credentials/youtube_client_secrets.json

Usage:
  python scripts/auth_youtube.py
"""

import json
import os
import sys
from pathlib import Path

CREDENTIALS_DIR = Path("data/credentials")
SECRETS_FILE    = CREDENTIALS_DIR / "youtube_client_secrets.json"
TOKEN_FILE      = CREDENTIALS_DIR / "youtube_token.json"
SCOPES          = ["https://www.googleapis.com/auth/youtube.upload"]


def main() -> None:
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("YouTube OAuth2 Setup")
    print("=" * 60)

    # Check for required libraries
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError:
        print("\n❌ Required packages not found.")
        print("Run:  pip install google-auth-oauthlib google-api-python-client")
        sys.exit(1)

    # Check for client secrets file
    if not SECRETS_FILE.exists():
        print(f"\n❌ Client secrets file not found: {SECRETS_FILE.absolute()}")
        print("\nTo create it:")
        print("  1. Go to https://console.cloud.google.com/")
        print("  2. APIs & Services → Library → enable 'YouTube Data API v3'")
        print("  3. APIs & Services → Credentials → Create OAuth 2.0 Client ID")
        print("     → Desktop app → Download JSON")
        print(f"  4. Rename to 'youtube_client_secrets.json'")
        print(f"  5. Place at: {SECRETS_FILE.absolute()}")
        sys.exit(1)

    # Check if token already exists
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        if creds.valid:
            print(f"\n✅ Existing valid token found at {TOKEN_FILE.absolute()}")
            print("No action needed. Delete the file to re-authenticate.")
            return
        if creds.expired and creds.refresh_token:
            print("\n🔄 Token expired — refreshing…")
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
            print(f"✅ Token refreshed: {TOKEN_FILE.absolute()}")
            return

    # Run OAuth flow
    print("\n🌐 Opening browser for Google OAuth authorization…")
    print("   If the browser doesn't open, copy the URL shown below.\n")

    flow  = InstalledAppFlow.from_client_secrets_file(str(SECRETS_FILE), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    TOKEN_FILE.write_text(creds.to_json())
    print(f"\n✅ Token saved to: {TOKEN_FILE.absolute()}")
    print("\nNext step: set YOUTUBE_ENABLED=true in your .env file.")
    print("The worker Docker container will read the token from the mounted volume.")


if __name__ == "__main__":
    main()
