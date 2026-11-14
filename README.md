# KNLTB Tennis for Home Assistant

A [Home Assistant](https://www.home-assistant.io/) integration, installable through [HACS](https://hacs.xyz/),
for Dutch tennis (and padel): the KNLTB rating and playing strength, results, competition teams and
tournament matches of the players you follow.

> **Status: early development.** Nothing to install yet. The data source has been investigated and
> works (see [docs/api.md](docs/api.md)); the integration itself is being built. Contributions welcome,
> see [Contributing](#contributing).

## What it will do

Log in with a MijnKNLTB account (any KNLTB member's), then add players: yourself, your children, a
partner. Per player you get:

- **Rating sensors**: singles and doubles playing strength (*speelsterkte*, 1–9) and the exact KNLTB DSS
  rating (e.g. 7.6295), padel too, with the playing strength that applies from next season once the
  KNLTB publishes it.
- **Results**: wins and losses (this year and career, singles/doubles/mixed), the last five results, the
  latest match with score and opponent.
- **Competition**: the player's team(s), position in the pool, win-loss this season.
- **Ranking**: position in the KNLTB ranking lists (for example the junior ranking).
- **A calendar** with upcoming competition and tournament matches, once draws and schedules are published.

## How it works, and the catch

The data comes from [MijnKNLTB](https://mijnknltb.toernooi.nl), the KNLTB's player site. It has no API
and since 2026 requires a login, so the integration logs in with your account and reads the pages, as
a browser would. It is **not affiliated with or endorsed by the KNLTB or Tournament Software**; when
the site changes, the integration may need an update. If the KNLTB asks us to change or stop something,
we will.

**Privacy.** Ratings and results are personal data, often of minors. The integration only fetches the
players you add, keeps their data in your Home Assistant, and never sends it anywhere else.

## Contributing

Issues and pull requests are welcome, in English or Dutch.

- Read [docs/api.md](docs/api.md) for how the site works and what to parse.
- [tools/probe.py](tools/probe.py) fetches a page with your own account, without Home Assistant.
- Planned work is tracked in the [issues](../../issues).

Never commit or paste real names, member numbers, ratings or results; use made-up or anonymised pages
in tests and examples.

## License

[MIT](LICENSE)
