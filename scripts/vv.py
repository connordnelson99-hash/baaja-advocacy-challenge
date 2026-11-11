"""Minimal VoterVoice API client (standard library only).

The API key comes from the VOTERVOICE_API_KEY environment variable (GitHub Actions secret),
or, for local runs, from key.env in the project folder. key.env may hold either the bare key
on one line or a VOTERVOICE_API_KEY=... line. The key is never printed.
"""
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = "https://www.votervoice.net/api"


def load_key():
    key = os.environ.get("VOTERVOICE_API_KEY", "").strip()
    if key:
        return key
    for name in ("key.env", ".env"):
        path = ROOT / name
        if not path.exists():
            continue
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8-sig").splitlines() if ln.strip() and not ln.strip().startswith("#")]
        for ln in lines:
            if ln.startswith("VOTERVOICE_API_KEY="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
        if len(lines) == 1:
            return lines[0]
    raise SystemExit("No API key found. Set VOTERVOICE_API_KEY or put the key in key.env.")


def get(path, params=None, raw_query="", retries=4):
    """GET a VoterVoice endpoint. raw_query is appended as-is (for the API's date=>... operators).
    Returns (status, parsed JSON or None). Never raises on HTTP errors; never prints the key."""
    query = urllib.parse.urlencode(params or {})
    if raw_query:
        query = (query + "&" if query else "") + raw_query
    url = BASE + path + ("?" + query if query else "")
    req = urllib.request.Request(url, headers={
        "Authorization": load_key(),
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "User-Agent": "advocacy-leaderboard/1.0",
    })
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(body) if body.strip() else None)
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", "replace")
            if err.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            try:
                return err.code, json.loads(body)
            except ValueError:
                return err.code, {"error": body[:300]}
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
            # Includes a read that stalls mid-response (a bare TimeoutError, not a URLError),
            # which failed the 2026-11-11 11:31 UTC run before this was retried.
            if attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            return 0, {"error": str(getattr(err, "reason", err))}
    return 0, None
