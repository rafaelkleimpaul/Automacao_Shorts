#!/usr/bin/env python3
"""
TikTok OAuth2 (PKCE) Setup — run this ONCE on the host machine.

What it does:
  1. Opens a browser for TikTok OAuth authorization
  2. Exchanges the auth code for access + refresh tokens (PKCE flow)
  3. Saves tokens to ./data/credentials/tiktok_token.json

Prerequisites (run on the HOST):
  pip install requests

Before running:
  1. Go to https://developers.tiktok.com/
  2. Manage Apps → Create app → select "Content Posting API"
  3. Request scopes: video.publish  video.upload  user.info.basic
  4. Under "Login Kit":
     → Add redirect URI: http://localhost:8180/callback
  5. Copy your Client Key and Client Secret

Usage:
  python scripts/auth_tiktok.py
  # or pass credentials via env:
  TIKTOK_CLIENT_KEY=xxx TIKTOK_CLIENT_SECRET=yyy python scripts/auth_tiktok.py
"""

import hashlib
import json
import os
import secrets
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

CREDENTIALS_DIR = Path("data/credentials")
TOKEN_FILE      = CREDENTIALS_DIR / "tiktok_token.json"
REDIRECT_PORT   = 8180
REDIRECT_URI    = f"http://localhost:{REDIRECT_PORT}/callback"
SCOPES          = "video.publish,video.upload,user.info.basic"


def _generate_pkce() -> tuple[str, str]:
    code_verifier  = secrets.token_urlsafe(64)
    digest         = hashlib.sha256(code_verifier.encode()).digest()
    import base64
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def _receive_auth_code() -> str | None:
    """Start a local HTTP server and wait for the OAuth callback."""
    received: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            params = parse_qs(urlparse(self.path).query)
            received["code"]  = params.get("code",  [None])[0]
            received["error"] = params.get("error", [None])[0]
            self.send_response(200)
            self.end_headers()
            if received["error"]:
                self.wfile.write(b"<h2>Authorization failed. Check terminal.</h2>")
            else:
                self.wfile.write(b"<h2>Authorization complete! Close this tab.</h2>")

        def log_message(self, *args):
            pass   # suppress access logs

    server = HTTPServer(("localhost", REDIRECT_PORT), Handler)
    server.handle_request()
    server.server_close()

    if received.get("error"):
        print(f"\n❌ OAuth error: {received['error']}")
        return None
    return received.get("code")


def main() -> None:
    try:
        import requests
    except ImportError:
        print("❌ 'requests' not found. Run: pip install requests")
        sys.exit(1)

    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("TikTok OAuth2 Setup")
    print("=" * 60)

    client_key    = os.getenv("TIKTOK_CLIENT_KEY")    or input("Client Key:    ").strip()
    client_secret = os.getenv("TIKTOK_CLIENT_SECRET") or input("Client Secret: ").strip()

    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_hex(16)

    auth_url = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode({
        "client_key":             client_key,
        "scope":                  SCOPES,
        "response_type":          "code",
        "redirect_uri":           REDIRECT_URI,
        "state":                  state,
        "code_challenge":         code_challenge,
        "code_challenge_method":  "S256",
    })

    print(f"\n🌐 Opening TikTok authorization page…")
    print(f"   Redirect URI set to: {REDIRECT_URI}")
    print(f"   (Make sure this URI is registered in your TikTok app settings)\n")
    webbrowser.open(auth_url)
    print("Waiting for authorization callback…")

    auth_code = _receive_auth_code()
    if not auth_code:
        sys.exit(1)

    print("\n🔄 Exchanging authorization code for tokens…")
    resp = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key":    client_key,
            "client_secret": client_secret,
            "code":          auth_code,
            "grant_type":    "authorization_code",
            "redirect_uri":  REDIRECT_URI,
            "code_verifier": code_verifier,
        },
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"\n❌ Token exchange failed ({resp.status_code}): {resp.text}")
        sys.exit(1)

    token_data = resp.json().get("data", resp.json())
    token_data["expires_at"]    = time.time() + token_data.get("expires_in", 86400)
    token_data["client_key"]    = client_key
    token_data["client_secret"] = client_secret

    TOKEN_FILE.write_text(json.dumps(token_data, indent=2))

    print(f"\n✅ TikTok tokens saved to: {TOKEN_FILE.absolute()}")
    print(f"\nToken details:")
    print(f"  Access token expires in: {token_data.get('expires_in', '?')} seconds")
    print(f"  Refresh token expires in: {token_data.get('refresh_expires_in', '?')} seconds")
    print(f"\nNext step: set TIKTOK_ENABLED=true in your .env file.")
    print("Tokens are auto-refreshed by the worker when they expire.")


if __name__ == "__main__":
    main()
