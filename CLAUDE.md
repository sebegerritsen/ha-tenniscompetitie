# Tenniscompetitie: notes for working on this repo

Public open-source project (GitHub: sebegerritsen/ha-tenniscompetitie), meant for HACS, a sibling of
ha-hockeycompetitie and ha-competitie-voetbal (same structure and conventions; look there for patterns).
Everything in this repo is public: **no personal data** — no names, member numbers, ratings or results
of real people, no account details, no club of the owner. Test fixtures are saved pages with all
personal data replaced by made-up values.

**Name:** "Tenniscompetitie", domain `tenniscompetitie` (until 14 Nov 2026 "KNLTB Tennis",
`knltb_tennis`). The name, domain and icon must not suggest an official KNLTB product; the KNLTB and
MijnKNLTB are named only as the source of the data and in the disclaimer.

## What it is

A Home Assistant custom integration (`custom_components/tenniscompetitie`, domain `tenniscompetitie`) for Dutch
tennis/padel player data from MijnKNLTB (mijnknltb.toernooi.nl, Tournament Software). No API: login +
HTML scraping. How the site works (cookie wall, login form with anti-forgery token, 7-day auth cookie,
pages, markup): docs/api.md. `tools/probe.py` fetches pages.

Decided (Nov 2026):
- One config entry per MijnKNLTB account; a subentry per followed player (found by search, or the
  account's own profile). The account's credentials are stored in the entry (no refresh token on this
  site; the auth cookie lasts 7 days, so log in again when a request lands on /user/login).
- Per player: sensors for singles/doubles (and padel) playing strength + DSS rating (state = rating,
  strength and "from date" as attributes), win-loss stats, last results, competition team + position,
  ranking; a calendar for upcoming matches once published.
- Parsers in their own module, tested against anonymised saved pages; fail softly (an entity goes
  unavailable rather than the whole entry failing) and raise a repair issue when the markup changed.
- Polling: profile every few hours (ratings change weekly at most), schedules more often around
  match days. Reuse one session for all players of an account.
- Diagnostics must redact names, member numbers and profile ids.

## Structure

- `api.py`: the client (cookie wall, login with the anti-forgery token, log in again when a page lands
  on /user/login; one session and cookie jar per account, one request at a time). The session comes
  from HA's `async_create_clientsession` with its own `CookieJar`: **detach it, never close it** (HA
  shares the connection pool and warns).
- `parsers.py`: one function per page, BeautifulSoup (`html.parser`), returning dataclasses from
  `models.py`; raise `KnltbParseError(page, …)` only when the page's structure is gone. The followed
  player's side of a match is the row with the W/V status tag (by name for planned matches).
- `coordinator.py`: a coordinator per player subentry. Only the profile page is required; rating,
  competitions, team pages and competition matches may fail alone (part = None, repair
  `markup_changed` listing the pages; cleared after a clean update). 3 h, 30 min on/before match days.
- For the account's own player only (entries are private): `/user/entries` → for entered tournaments
  that run or start within 14 days, the player's page in the tournament (found by name on the event
  page, cached per tournament) → planned matches with times. Profiles show tournament matches only
  once played; `PlayerData.matches` merges both (one match counts once, the tournament page wins).
- `sensor.py`, `calendar.py`, `config_flow.py` (account + `PlayerSubentryFlow`), `diagnostics.py`
  (shape only: no names, member numbers or profile ids).

## Tests

`pip install -r requirements_test.txt`, `pytest`, `ruff check`, `ruff format`. Fixtures in
`tests/fixtures` are made-up pages with the real markup; regenerate or extend them by hand, never by
copying a real page. `python3 tools/check_fixtures.py` enforces made-up ids (`00000000-0000-4000-8000-…`)
and member numbers (`9999…`) and takes a private `--blocklist` of real names.

## Testing locally

Copy `custom_components/tenniscompetitie` into your Home Assistant `custom_components/` folder and restart
Home Assistant. Personal setup notes (paths, test account) belong in `CLAUDE.local.md`, git-ignored.
