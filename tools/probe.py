#!/usr/bin/env python3
"""Fetch a MijnKNLTB page with your own account and print its visible text (or its HTML).

    export KNLTB_LOGIN=... KNLTB_PASSWORD=...
    python3 tools/probe.py /                         # after login: links to your own profile
    python3 tools/probe.py '/find/player?q=<name>'
    python3 tools/probe.py /player-profile/<guid>/Rating
    python3 tools/probe.py --html /player-profile/<guid>

Accepts only the necessary cookies on the cookie wall. The session cookies are kept in
~/.cache/tenniscompetitie-probe.txt (mode 0600). See docs/api.md. Everything you fetch is personal
data of real people: don't paste it into issues or commit it.
"""

from __future__ import annotations

import contextlib
import html
import http.cookiejar
import os
import re
import sys
import urllib.parse
import urllib.request

BASE_URL = "https://mijnknltb.toernooi.nl"
COOKIES = os.path.expanduser("~/.cache/tenniscompetitie-probe.txt")
USER_AGENT = "Mozilla/5.0 (compatible; tenniscompetitie-probe)"

os.makedirs(os.path.dirname(COOKIES), exist_ok=True)
jar = http.cookiejar.MozillaCookieJar(COOKIES)
with contextlib.suppress(OSError, http.cookiejar.LoadError):
    jar.load(ignore_discard=True, ignore_expires=True)
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Set {name} (see the docstring of this script).")
    return value


def request(path: str, form: dict | None = None) -> tuple[str, str]:
    data = urllib.parse.urlencode(form, doseq=True).encode() if form else None
    req = urllib.request.Request(BASE_URL + path, data=data, headers={"User-Agent": USER_AGENT})
    with opener.open(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", "replace")
        url = resp.geturl()
    jar.save(ignore_discard=True, ignore_expires=True)
    os.chmod(COOKIES, 0o600)
    return url, body


def login() -> None:
    # Cookie wall: necessary (1) and preference (2) cookies only.
    request(
        "/cookiewall/Save",
        {"ReturnUrl": "/", "SettingsOpen": "false", "CookiePurposes": ["1", "2"]},
    )
    _, body = request("/user/login?ReturnUrl=%2f")
    token = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', body)
    if not token:
        sys.exit("Login form not found: the site has changed.")
    _, body = request(
        "/user",
        {
            "Login": _env("KNLTB_LOGIN"),
            "Password": _env("KNLTB_PASSWORD"),
            "ReturnUrl": "/",
            "__RequestVerificationToken": token.group(1),
        },
    )
    if "/user/LogOff" not in body:
        sys.exit("Login failed (wrong login name or password?).")


def get(path: str) -> str:
    url, body = request(path)
    if "/cookiewall" in url or "/user/login" in url:
        login()
        _, body = request(path)
    return body


def visible_text(page: str) -> str:
    page = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", page)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", page))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--html"]
    if len(args) != 1 or not args[0].startswith("/"):
        sys.exit(__doc__)
    page = get(args[0])
    print(page if "--html" in sys.argv else visible_text(page))
