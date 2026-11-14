#!/usr/bin/env python3
"""Check the test pages in tests/fixtures for personal data that was left in.

Every page there must be made up. This script fails when it finds something that looks
real:

- a profile, tournament or league id that is not of the made-up form
  `00000000-0000-4000-8000-…`;
- a member number (8 digits) that does not start with 9999;
- an email address outside example.com/.org/.net, or a phone number;
- any line of a private blocklist: a file with real names, member numbers and clubs (one
  per line) that you keep outside the repository, given with `--blocklist FILE` or the
  KNLTB_FIXTURE_BLOCKLIST environment variable. Build it from the pages you anonymised.

    python3 tools/check_fixtures.py [--blocklist ~/my-real-names.txt]
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
FAKE_GUID = re.compile(r"^00000000-0000-4000-8000-[0-9a-fA-F]{12}$")
MEMBER_NUMBER = re.compile(r"(?<![\d.,-])(\d{8})(?![\d.,-])")
EMAIL = re.compile(r"[\w.+-]+@([\w-]+\.)+[\w-]+")
FAKE_EMAIL = re.compile(r"@example\.(com|org|net)$")
PHONE = re.compile(r"(\+31|0031|\b06)[\s-]?\d[\d\s-]{7,}")


def check_text(name: str, text: str, blocklist: list[str]) -> list[str]:
    """Return the problems found in one fixture."""
    problems = []
    for guid in set(GUID.findall(text)):
        if not FAKE_GUID.match(guid):
            problems.append(f"{name}: id {guid} is not of the made-up form")
    for number in set(MEMBER_NUMBER.findall(text)):
        if not number.startswith("9999"):
            problems.append(f"{name}: member number {number} does not start with 9999")
    for match in EMAIL.finditer(text):
        if not FAKE_EMAIL.search(match.group()):
            problems.append(f"{name}: email address {match.group()}")
    for match in PHONE.finditer(text):
        problems.append(f"{name}: phone number {match.group().strip()}")
    for entry in blocklist:
        # Whole words only, so that a short club name does not match inside another word.
        if re.search(rf"(?<!\w){re.escape(entry)}(?!\w)", text, re.IGNORECASE):
            problems.append(f"{name}: contains a blocklisted value")
    return problems


def load_blocklist(path: str | None) -> list[str]:
    """Read the blocklist: one value per line, at least 4 characters."""
    if not path:
        return []
    lines = Path(path).expanduser().read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if len(line.strip()) >= 4 and not line.startswith("#")]


def main() -> int:
    """Check every fixture; print the problems."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--blocklist", default=os.environ.get("KNLTB_FIXTURE_BLOCKLIST"))
    args = parser.parse_args()
    blocklist = load_blocklist(args.blocklist)
    problems: list[str] = []
    files = sorted(path for path in FIXTURES.rglob("*") if path.is_file())
    for path in files:
        problems += check_text(path.name, path.read_text(encoding="utf-8"), blocklist)
    for problem in problems:
        print(problem)
    print(
        f"{len(files)} fixtures checked{' with a blocklist' if blocklist else ''}: "
        f"{'OK' if not problems else f'{len(problems)} problem(s)'}"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
