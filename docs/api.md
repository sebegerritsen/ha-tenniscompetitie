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

What `custom_components/tenniscompetitie/parsers.py` reads (tested against anonymised copies in
`tests/fixtures`). Labels are Dutch; the parsers also accept the English ones.

- **Player header** (every `/player-profile/<guid>…` page): `.page-head .media__title` with the name
  (`.nav-link__value`) and the member number (`.media__title-aside`, in brackets), the club in
  `.media__subheading`.
- **Ratings in the header**: one `span.tag-duo` per discipline. `.tag-duo__title` is the **current**
  playing strength, `.tag-duo__value` the DSS rating (decimal comma, e.g. `6,1234`). The `title`
  attribute (`"Enkel: 5 vanaf 1-1-2027"`, `"Dubbel: …"`, `"Padel Dubbel: …"`) holds the strength that
  applies **from** a date: around the turn of the year next year's strength. An icon after the number
  shows the direction: `caret-up` (better), `caret-down` (worse), `circle` (same). Use the `title` text
  to tell the disciplines apart, not the order; the padel tag has a padel icon before the number.
- **Statistics** (profile overview): tabs `#tabStatsTotal`, `#tabStatsSingles`, `#tabStatsDoubles`,
  `#tabStatsMixed`, each with `.list__value-start` values `"wins / losses (played)"`: career first, then
  this year. The total tab also has the last five results: `span.match__status` (`W`/`V`,
  `tag--success`/`tag--danger`) with the date in `title`, oldest first.
- **Ranking** (profile overview and `/ranking`): a `table` per ranking list, its `caption` with a link
  to the list and the publication date in a `.tag`. Columns differ per list (`Rank`, `Beste` with a
  date tag, `Punten`, `Toernooien`, …): map them by the header titles.
- **Matches**: the `div.match` component. Two `.match__row`s (the sides), players in
  `.match__row-title-value-content`, the team in `.match__row-title-header` (competitions). **The
  followed player's side is the row with the `.match__status` tag** (W/V); a planned match has none,
  then the name tells. Set scores in `.match__result ul.points`, one `li.points__cell` per side, in row
  order. Header items: round and duration (`"1h 26m"`). Footer items: date (`"vr 23-10-2026"`, with a
  time when planned) and venue (`"<venue> - Baan 7"`).
- **Tournaments** (profile overview, `/tournaments/<year>`): an `li.list__item` per tournament (name in
  the media title link's `title`, location, `<time>` start and end), then per event an
  `h4.module-divider` ("Onderdeel: …"), per draw an `h5.module-divider`, and an
  `ol.match-group[id^=TournamentMatchList_]` of matches.
- **Competitions** (`/leagues`, `/leagues/<year>`; the newest first; early in the year the current year
  can be empty): a `.module--card` per competition (name, `<time>` start and end), an `li.list__item`
  per team (category in `b`, then class and division; team link `/league/<guid>/team/<n>`; `.stats` with
  *Stand* = position and *GS* = played), the link `/league/<guid>/player/<n>` to the player's matches,
  and the player's *Winst-Verlies* `"7-3 (10)"`.
- **Competition team** (`/league/<guid>/team/<n>`): `.standing-status` (position), *Gespeeld*, *Punten*,
  and the schedule as `div.match--team-match`: header `"Ronde 1 • <time datetime='2026-09-06 11:30'>"`,
  the team's W/V once played, `.team-match__name.is-team-1|2`, `.score .is-team-1|2`. The page also
  links an iCalendar feed of the team's matches (`/external/matchcalendarhandler.ashx?code=<guid>&id=<n>`),
  not used yet.
- **The player in a competition** (`/league/<guid>/player/<n>`): per team match an
  `li.match-group__item` with the `div.match--team-match` followed by the player's own `div.match`es
  (header = match type such as `HE3`, `HD1`).
- **Search** (`/find/player?q=<name>`): `a.media__link[href^="/player-profile/"]` per player, with the
  member number in `.media__title-aside` and `"Club | TOWN"` in `.media__subheading`. The account's own
  profile is the first `a.dropdown-list__link` to `/player-profile/<guid>` in the account menu.
- **Entries** (`/user/entries`; only the logged-in member's own; another year as a page part via
  `/user/entries/GetUserEntryListByYear/<year>`): per tournament an `li` with the media block (name in
  the link's `title`, `/sport/tournament?id=<guid>`, location, `<time>` start and end, a `.tag-duo` such
  as *Open toernooi* or *Junioren Tour Brons*), then a `.module-divider` with the events entered: open
  tournaments link `/sport/event.aspx?id=<guid>&event=<n>` and show the status as a button in
  `.media__aside` (*Ingeschreven*); Junior Tour entries are grouped per week, link
  `/tournament/<guid>/entry|acceptance/<n>` and show `.priority-status-text` (*Geaccepteerd*, *Niet
  geaccepteerd*, or nothing yet) and `.priority-status` (the preference).
- **Planned tournament matches** are not on the player's profile until played. They are on the player's
  page in the tournament, `/sport/player.aspx?id=<guid>&player=<n>` (redirects to
  `/tournament/<guid>/player/<n>`): the same `div.match` component, with the round and the draw
  (`a[href*=draw]`) in the header and `"zo 15-11-2026 20:15"` plus the venue in the footer (and a
  calendar-export link). No result tag yet, so the player's side is found by name; names may carry a
  seeding (`"[2]"`). `<n>` differs per tournament: find it on the event page (the entrants table links
  `player.aspx?…&player=<n>` with the names). The tournament's `players` page loads its list by script.
- **Junior Tour entries** link no event page. The tournament's events page,
  `/sport/events.aspx?id=<guid>`, links every event (`event.aspx?id=<guid>&event=<n>`) by name: find the
  entered event by name there.
- **Pool** (`/league/<guid>/draw/<n>`, the link of the team's category on the competitions page): a
  `table` with `.standing-status` (position) per row and columns named by the `title` of each header
  (*Gespeeld*, *Winst*, *Gelijk*, *Verloren*, *Punten*, *Wedstrijden*, *Sets*, *Games*, *Historie*); the
  form as `.standing-form .match__status` (W, V, G = draw). Below it the pool's whole schedule.
- **Team match** (`/league/<guid>/team-match/<n>`): `.team-match-header` with the round and
  `<time datetime>`, `.team-match__name.is-team-1|2` (the `title` is the team), the score; the rubbers
  as `.match-group div.match` (header = HE1, HD2, …; rows = home and away players, `has-won`), once the
  captains have set the line-up; the venue (*Speellocatie*) as an h-card (`.p-street-address`,
  `.p-postal-code`, `.p-locality`), the surface as the `.tag` without `tag--placeholder`, and the
  coordinates in the route link (`/link/route.aspx?…&lat=…&lng=…`). The page also lists the captains'
  phone numbers and email addresses: leave those alone.
- **Rating history**: the Rating page's season tabs (`.page-nav__item a[data-href]`, the active one is on
  the page itself) load a season's summary from `…/rating/<guid>/SnapshotSummary?RatingCode=…` —
  only for the page's own script: send `X-Requested-With: XMLHttpRequest`, or the answer is a 404. The
  summary has the same blocks as the Rating page, with *Eindrating* (and the season's change next to
  it) instead of *Huidige rating*, and per discipline a `form` whose `action` is
  `…/RatingMatchList?RatingCode=…&ranktype=1|2` (singles, doubles; GET, also as a script request): the
  season's rated matches, newest first. Per match the change in `.match__header-aside .tag`
  (`-0,1231`: four decimals; a whole number there is something else) and each player's rating before
  the match in brackets in `.match__row-title-aside`. In mixed doubles that rating includes the mixed
  correction (+1); at the end of a season juniors get a correction too (−0,2), visible only in the next
  season's start rating. Seasons before 2021 show other numbers there.
- **Dates**: `<time datetime="YYYY-MM-DD HH:MM">` (Dutch local time), else `d-m-yyyy` in text.
- Numbers use a decimal comma.

## Caveats for an integration

- HTML scraping: markup changes break it. Keep the parsers small, tested against saved (anonymised)
  pages, and fail softly.
- Everything here is personal data of real people, often minors (names, member numbers, ratings,
  results). An integration should only fetch the players the user explicitly adds, and never put real
  data in the repository, logs or diagnostics.
- Poll gently: ratings change at most weekly (DSS updates), tournament draws a few times a day.
- The same platform serves other federations (badminton, squash, …), which a generic parser could reuse later.
