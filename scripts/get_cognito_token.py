import base64
import hashlib
import json
import secrets
import sys
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

CLIENT_ID = "30bv65eumkbqei9l7p31q9ctvj"
COGNITO_DOMAIN = "https://odoo-pm-pilot-354298.auth.eu-north-1.amazoncognito.com"
CALLBACK_PATH = "/callback/AvYszWqvA9eg"
REDIRECT_URI = f"http://127.0.0.1:8765{CALLBACK_PATH}"
SCOPES = "openid email https://mcp.example.com/mcp:tools"

auth_code = None
server_instance = None


def generate_pkce():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == CALLBACK_PATH:
            qs = urllib.parse.parse_qs(parsed.query)
            if "code" in qs:
                auth_code = qs["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body style='font-family:system-ui;text-align:center;padding:50px'>"
                    b"<h1 style='color:#16a34a'>Login successful!</h1>"
                    b"<p>You can close this window and return to your terminal / OPM.</p>"
                    b"</body></html>"
                )
            else:
                err = qs.get("error_description", qs.get("error", ["Unknown error"]))[0]
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<html><body><h1>Auth Error</h1><p>{err}</p></body></html>".encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress HTTP server stdout logs


def get_token():
    verifier, challenge = generate_pkce()

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    authorize_url = f"{COGNITO_DOMAIN}/oauth2/authorize?{urllib.parse.urlencode(params)}"

    print("=" * 70)
    print("COGNITO OAUTH 2.0 PKCE LOGIN")
    print("=" * 70)
    print("\nStarting local listener on http://127.0.0.1:8765 ...")
    server = HTTPServer(("127.0.0.1", 8765), OAuthCallbackHandler)

    print("\nOpening browser for Cognito login...")
    print(f"If your browser did not open automatically, visit:\n\n  {authorize_url}\n")
    webbrowser.open(authorize_url)

    # Wait for the single callback request
    while not auth_code:
        server.handle_request()

    server.server_close()
    print("\nReceived authorization code from Cognito! Exchanging for tokens...")

    token_url = f"{COGNITO_DOMAIN}/oauth2/token"
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    }).encode("utf-8")

    req = urllib.request.Request(
        token_url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            tokens = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"\nToken exchange failed: {e.read().decode('utf-8')}")
        sys.exit(1)

    access_token = tokens["access_token"]
    print("\n" + "=" * 70)
    print("SUCCESS: COGNITO ACCESS TOKEN RETRIEVED")
    print("=" * 70)
    print("\nBearer Token:\n")
    print(access_token)
    print("\n" + "=" * 70)

    # Save to a convenient scratch file
    with open("cognito_token.txt", "w", encoding="utf-8") as f:
        f.write(access_token)
    print("Token saved to: cognito_token.txt\n")
    return access_token


if __name__ == "__main__":
    get_token()
