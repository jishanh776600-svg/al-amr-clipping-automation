#!/usr/bin/env python3
"""Google Drive OAuth2 Refresh Token Generator for AL AMR Clipping Automation.

This utility script assists operators in generating a valid, long-lived
OAuth2 refresh token for Google Drive integration.

USAGE:
    python scripts/generate_drive_refresh_token.py

REQUIREMENTS:
    - Google Cloud Console OAuth 2.0 Client ID and Client Secret.
    - Set OAuth Consent Screen publishing status to 'In production' (or 'Published')
      in Google Cloud Console to ensure the refresh token never expires in 7 days.
    - Add http://localhost:8085/ (or http://127.0.0.1:8085/) to Authorized Redirect URIs
      in your OAuth 2.0 Client settings.
"""

import argparse
import http.server
import json
import os
import sys
import urllib.parse
import urllib.request
import webbrowser

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
]
REDIRECT_PORT = 8085
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/"


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    auth_code = None

    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        if "code" in params:
            OAuthCallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;text-align:center;padding:50px;'>"
                b"<h2>Authentication Successful!</h2>"
                b"<p>You can close this browser tab and return to the terminal.</p>"
                b"</body></html>"
            )
        else:
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            err = params.get("error", ["Unknown error"])[0]
            self.wfile.write(f"<html><body><h2>Authentication Failed: {err}</h2></body></html>".encode())

    def log_message(self, format, *args):
        pass  # Suppress HTTP server request logging


def exchange_code_for_tokens(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    url = "https://oauth2.googleapis.com/token"
    payload = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "AL-AMR-Auth/1.0"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Generate Google Drive OAuth2 refresh token.")
    parser.add_argument("--client-id", default=os.getenv("GOOGLE_DRIVE_CLIENT_ID"), help="OAuth2 Client ID")
    parser.add_argument("--client-secret", default=os.getenv("GOOGLE_DRIVE_CLIENT_SECRET"), help="OAuth2 Client Secret")
    parser.add_argument("--port", type=int, default=REDIRECT_PORT, help="Local callback port (default: 8085)")
    parser.add_argument("--manual", action="store_true", help="Manual authorization code copy-paste mode")
    args = parser.parse_args()

    client_id = args.client_id
    if not client_id:
        client_id = input("Enter your GOOGLE_DRIVE_CLIENT_ID: ").strip()

    client_secret = args.client_secret
    if not client_secret:
        client_secret = input("Enter your GOOGLE_DRIVE_CLIENT_SECRET: ").strip()

    if not client_id or not client_secret:
        print("[ERROR] Both Client ID and Client Secret are required.")
        sys.exit(1)

    redirect_uri = f"http://localhost:{args.port}/" if not args.manual else "urn:ietf:wg:oauth:2.0:oob"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)

    print("\n" + "=" * 70)
    print("AL AMR GOOGLE DRIVE TOKEN GENERATION")
    print("=" * 70)
    print("1. Ensure your OAuth 2.0 Client ID in Google Cloud Console has:")
    print(f"   Authorized redirect URI: {redirect_uri}")
    print("2. Ensure the OAuth Consent Screen status is set to 'In production' (or Published)")
    print("   so the token does not expire after 7 days.\n")

    auth_code = None

    if args.manual:
        print("Open the following URL in your browser:\n")
        print(auth_url)
        print("\nAfter authorizing, copy the authorization code provided.")
        auth_code = input("\nEnter the authorization code: ").strip()
    else:
        server = http.server.HTTPServer(("localhost", args.port), OAuthCallbackHandler)
        print(f"Opening browser to authorize Google Drive access on port {args.port}...")
        print(f"If browser does not open automatically, visit:\n{auth_url}\n")
        webbrowser.open(auth_url)

        print("Waiting for browser authentication callback...")
        while OAuthCallbackHandler.auth_code is None:
            server.handle_request()
        auth_code = OAuthCallbackHandler.auth_code
        server.server_close()

    if not auth_code:
        print("[ERROR] Failed to obtain authorization code.")
        sys.exit(1)

    print("\nExchanging authorization code for OAuth tokens...")
    try:
        tokens = exchange_code_for_tokens(client_id, client_secret, auth_code, redirect_uri)
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8")
        print(f"[ERROR] Token exchange failed ({err.code}): {err_body}")
        sys.exit(1)

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("\n[WARNING] No refresh_token was returned by Google!")
        print("This typically occurs if 'prompt=consent' was omitted or Google considers the app already approved.")
        print("Try re-running with prompt=consent or revoking app access under myaccount.google.com/permissions.")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("[SUCCESS] Refresh token generated successfully!")
    print("=" * 70)
    print("\nNEXT STEPS:")
    print("1. Copy your new refresh token (do NOT paste it into chat or source code).")
    print("2. Navigate to your GitHub repository secrets:")
    print("   https://github.com/jishanh776600-svg/al-amr-clipping-automation/settings/secrets/actions")
    print("3. Update secret named: GOOGLE_DRIVE_REFRESH_TOKEN")
    print("4. Value to set is the token printed below:\n")
    print("-" * 70)
    print(refresh_token)
    print("-" * 70)
    print("\n[IMPORTANT SECURITY REMINDER]")
    print("Never commit this token to Git or share it publicly.")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
