#!/usr/bin/env python3
"""Explore the voetbal.nl app API (Sportlink, KNVB) from the command line, with your own account.

    export VOETBAL_EMAIL=you@example.com VOETBAL_PASSWORD=...
    # the app's fixed values, see docs/api.md:
    export SPORTLINK_CLIENT_ID=... SPORTLINK_CLIENT_SECRET=...
    python3 tools/probe.py 'club/Clubs?v=1'
    python3 tools/probe.py 'club/ClubTeams?v=1&ClubId=<club id>'
    python3 tools/probe.py 'team/TeamProgram?v=3&PublicTeamId=<team id>'

Prints the JSON response. The session (access + refresh token) is kept in
~/.cache/competitie-voetbal-probe.json (mode 0600); your password is only used when there is no
valid refresh token. Never use this to fetch rosters or other personal data.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://app-vnl-production.sportlink.com"
DATA_PATH = "/entity/common/memberportal/app/"
# The server answers 500 without an app-style User-Agent (see docs/api.md).
USER_AGENT = "sportlink-app-voetbalnl/6.64.0-2026025447 android generic/android/33  (6.64.0)"
STATE = os.path.expanduser("~/.cache/competitie-voetbal-probe.json")


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Set {name} (see the docstring of this script).")
    return value


def _save(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(state, f)
    os.chmod(STATE, 0o600)


def _token_request(fields: dict) -> dict:
    fields |= {"client_id": _env("SPORTLINK_CLIENT_ID"), "secret": _env("SPORTLINK_CLIENT_SECRET")}
    req = urllib.request.Request(
        BASE_URL + "/oauth/token",
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        token = json.load(resp)
    token["expires_at"] = time.time() + token.get("expires_in", 3600)
    _save(token)
    return token


def access_token() -> str:
    try:
        with open(STATE) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    if state.get("expires_at", 0) > time.time() + 60:
        return state["access_token"]
    if state.get("refresh_token"):
        try:
            return _token_request(
                {"grant_type": "refresh_token", "refresh_token": state["refresh_token"]}
            )["access_token"]
        except urllib.error.HTTPError:
            pass  # refresh token expired: log in again
    return _token_request(
        {
            "grant_type": "password",
            "username": _env("VOETBAL_EMAIL"),
            "password": _env("VOETBAL_PASSWORD"),
        }
    )["access_token"]


def get(path: str):
    version = re.search(r"[?&]v=(\d+)", path)
    headers = {
        "Authorization": "Bearer " + access_token(),
        "X-Navajo-Instance": "KNVB",
        "X-Navajo-Version": version.group(1) if version else "1",  # must match ?v=
        "X-Navajo-Locale": "nl",
        "Accept-Encoding": "gzip",
        "User-Agent": USER_AGENT,
    }
    with urllib.request.urlopen(
        urllib.request.Request(BASE_URL + DATA_PATH + path, headers=headers), timeout=30
    ) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode() or "null")


if __name__ == "__main__":
    if len(sys.argv) != 2 or "TeamPersons" in sys.argv[1]:
        sys.exit(__doc__)
    print(json.dumps(get(sys.argv[1].lstrip("/")), ensure_ascii=False, indent=2))
