# KNLTB Tennis: notes for working on this repo

Public open-source project (GitHub: sebegerritsen/ha-knltb-tennis), meant for HACS, a sibling of
ha-knhb-hockey and ha-knvb-voetbal (same structure and conventions; look there for patterns).
Everything in this repo is public: **no personal data** — no names, member numbers, ratings or results
of real people, no account details, no club of the owner. Test fixtures are saved pages with all
personal data replaced by made-up values.

## What it is

A Home Assistant custom integration (`custom_components/knltb_tennis`, domain `knltb_tennis`) for Dutch
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

## Testing locally

Copy `custom_components/knltb_tennis` into your Home Assistant `custom_components/` folder and restart
Home Assistant. Personal setup notes (paths, test account) belong in `CLAUDE.local.md`, git-ignored.
