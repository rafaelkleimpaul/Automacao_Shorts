#!/usr/bin/env python3
"""
Instagram Long-Lived Token Setup — run this on the host machine.

What it does:
  1. Exchanges a short-lived token for a long-lived token (60 days)
  2. Retrieves your Instagram Business Account ID
  3. Prints the values to add to your .env file

Prerequisites (run on the HOST):
  pip install requests

Before running:
  1. Go to https://developers.facebook.com/
  2. My Apps → Create App → Consumer (or Business)
  3. Add product: "Instagram Graph API"
  4. Go to Tools → Graph API Explorer
  5. Select your app → Generate token
  6. Request permissions:
       instagram_basic
       instagram_content_publish
       pages_show_list
       pages_read_engagement
  7. Copy the generated access token (it's short-lived, ~1 hour)

Usage:
  python scripts/auth_instagram.py
"""

import sys

def main() -> None:
    try:
        import requests
    except ImportError:
        print("❌ 'requests' not found. Run: pip install requests")
        sys.exit(1)

    print("=" * 60)
    print("Instagram Long-Lived Token Setup")
    print("=" * 60)
    print("\nYou need:")
    print("  • A Facebook App (from developers.facebook.com)")
    print("  • A short-lived access token (from Graph API Explorer)")
    print("  • An Instagram Business or Creator account linked to a Facebook Page")
    print()

    short_token = input("Paste the short-lived access token: ").strip()
    app_id      = input("Your App ID: ").strip()
    app_secret  = input("Your App Secret: ").strip()

    # ── Step 1: Exchange for long-lived token (60 days) ────────────────────
    print("\n🔄 Exchanging for long-lived token…")
    resp = requests.get(
        "https://graph.facebook.com/v21.0/oauth/access_token",
        params={
            "grant_type":        "fb_exchange_token",
            "client_id":         app_id,
            "client_secret":     app_secret,
            "fb_exchange_token": short_token,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"\n❌ Token exchange failed: {resp.json()}")
        sys.exit(1)

    long_token = resp.json()["access_token"]
    print("✅ Long-lived token obtained (valid for ~60 days)")

    # ── Step 2: List Facebook Pages ────────────────────────────────────────
    print("\n📋 Fetching your Facebook Pages…")
    pages_resp = requests.get(
        "https://graph.facebook.com/v21.0/me/accounts",
        params={"access_token": long_token},
        timeout=15,
    )
    pages_resp.raise_for_status()
    pages = pages_resp.json().get("data", [])

    if not pages:
        print("\n❌ No Facebook Pages found on this account.")
        print("   Make sure your account manages at least one Facebook Page,")
        print("   and that the Page is connected to an Instagram Business account.")
        sys.exit(1)

    print("\nYour Facebook Pages:")
    for i, page in enumerate(pages):
        print(f"  [{i}] {page['name']}  (Page ID: {page['id']})")

    idx = int(input("\nSelect the Page number: ").strip())
    if idx < 0 or idx >= len(pages):
        print("❌ Invalid selection.")
        sys.exit(1)

    selected_page  = pages[idx]
    page_token     = selected_page["access_token"]

    # ── Step 3: Get Instagram Business Account ID ───────────────────────────
    print("\n🔍 Fetching Instagram Business Account…")
    ig_resp = requests.get(
        f"https://graph.facebook.com/v21.0/{selected_page['id']}",
        params={
            "fields":       "instagram_business_account",
            "access_token": page_token,
        },
        timeout=15,
    )
    ig_resp.raise_for_status()
    ig_data    = ig_resp.json().get("instagram_business_account", {})
    ig_user_id = ig_data.get("id")

    if not ig_user_id:
        print("\n❌ No Instagram Business Account connected to this Page.")
        print("   Go to Instagram settings → Link your account to the Facebook Page,")
        print("   then switch to a Business or Creator profile.")
        sys.exit(1)

    # ── Output ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("✅ SUCCESS — Add these to your .env file:")
    print("=" * 60)
    print(f"\nINSTAGRAM_ENABLED=true")
    print(f"INSTAGRAM_USER_ID={ig_user_id}")
    print(f"INSTAGRAM_ACCESS_TOKEN={long_token}")
    print()
    print("⚠️  IMPORTANT:")
    print("   • This token expires in 60 days.")
    print("   • Run this script again before it expires to renew.")
    print("   • For token renewal without re-login, call:")
    print("     GET https://graph.facebook.com/v21.0/oauth/access_token")
    print("         ?grant_type=fb_exchange_token&client_id=APP_ID")
    print("         &client_secret=APP_SECRET&fb_exchange_token=CURRENT_TOKEN")
    print()
    print("   • Set PUBLIC_BASE_URL to your ngrok / public domain URL.")
    print("     Example with ngrok:  ngrok http 8000")
    print("     Then set:  PUBLIC_BASE_URL=https://xxxx.ngrok-free.app")


if __name__ == "__main__":
    main()
