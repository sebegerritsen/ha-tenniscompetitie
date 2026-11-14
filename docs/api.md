# MijnKNLTB (mijnknltb.toernooi.nl): how the data can be read

Findings from November 2026. MijnKNLTB is the KNLTB's player site, running on the Tournament Software
platform (toernooi.nl, by Visual Reality). There is **no JSON API**: pages are rendered on the server
and must be read as HTML. Not officially documented; markup may change. `tools/probe.py` fetches pages.

Since 2026 **MijnKNLTB is only accessible after logging in** ("Voortaan is MijnKNLTB alleen toegankelijk
na inloggen"). Any MijnKNLTB account (a KNLTB member's) can view other players' profiles.

## Session

1. **Cookie wall.** Every first request redirects to `/cookiewall/`. Accept only what is necessary:

   ```
   POST /cookiewall/Save
   ReturnUrl=/&SettingsOpen=false&CookiePurposes=1&CookiePurposes=2
   ```

   (1 = necessary, 2 = preferences; the others are statistics/marketing.) Sets the `st` cookie (1 year).

2. **Log in.** `GET /user/login?ReturnUrl=%2f`, take the hidden `__RequestVerificationToken` from the form, then

   ```
   POST /user
   Login=<login name>&Password=<password>&ReturnUrl=/&__RequestVerificationToken=<token>
   ```

   No captcha. On success the page shows "Log uit"/"Mijn profiel"; on failure
   "Ongeldige gebruikersnaam of wachtwoord". The login name is the member number or an email address.

3. **Cookies.** `.ASPX_TOURNAMENT_WEBSITE` (the login, valid for 7 days), `ASP.NET_SessionId`,
   `__RequestVerificationToken`, `st`. There is no refresh token: when a request redirects to
   `/user/login`, log in again. An integration therefore has to keep the credentials.

## Pages

| Page | Contents |
|---|---|
| `/` (after login) | Links to the logged-in member's own profile (`/player-profile/<guid>`), club and leagues. |
| `/find/player?q=<name>` | Player search; results link to `/player-profile/<guid>`. (`/find/player/DoSearch` is the XHR behind it.) `/find/club` searches clubs. |
| `/player-profile/<guid>` | Overview. Header: name, member number in brackets, club, and the **ratings** (see below). Statistics: wins/losses total, singles, doubles, mixed (career and this year), last five results (W/V). Excerpt of the KNLTB ranking (category, rank, best, points). Recent tournaments with their matches. Competitions. |
| `/player-profile/<guid>/Rating` | **KNLTB DSS** (Dynamische Speelsterkte) per season: for singles and doubles the playing strength (1–9), start rating, number of matches and current rating, and the rated matches. A separate **KNLTB Padel DSS** block. |
| `/player-profile/<guid>/leagues` | Competitions per year (e.g. *KNLTB voorjaar/najaar Tennis*): category, class and division, team, final/current position, the player's win-loss. Links to `/league/<guid>/team/<id>`, `/league/<guid>/draw/<id>` (the pool: standing and matches) and `/league/<guid>/player/<id>`. |
| `/player-profile/<guid>/tournaments` | Tournaments with events (*onderdelen*), rounds and matches. |
| `/player-profile/<guid>/ranking` | The KNLTB ranking lists (e.g. junior ranking per category). |
| `/user/entries` | The logged-in member's tournament entries. |

## Markup to parse

- **Ratings in the header**: `span.tag-duo` with `title="Enkel: 7 vanaf 1-1-2027"` (playing strength and the
  date it applies from — around the turn of the year this already shows next year's strength),
  `.tag-duo__title` (the strength) and `.tag-duo__value` (the rating with a decimal comma, e.g. `7,6295`).
  The order is singles, doubles, padel doubles; use the `title` text rather than the order.
- **Matches**: the `match` component (`match__header`, `match__body`, `match__footer`, `match-group`),
  with W/V per side, set scores, round, venue and court ("<venue> - Baan 7"), duration ("1h 44m"), and an H2H link.
- **Dates**: `<time datetime="YYYY-MM-DD HH:MM">`.
- Numbers use a decimal comma.

## Caveats for an integration

- HTML scraping: markup changes break it. Keep the parsers small, tested against saved (anonymised)
  pages, and fail softly.
- Everything here is personal data of real people, often minors (names, member numbers, ratings,
  results). An integration should only fetch the players the user explicitly adds, and never put real
  data in the repository, logs or diagnostics.
- Poll gently: ratings change at most weekly (DSS updates), tournament draws a few times a day.
- The same platform serves other federations (badminton, squash, …), which a generic parser could reuse later.
