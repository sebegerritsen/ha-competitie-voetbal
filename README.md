# KNVB Voetbal for Home Assistant

A [Home Assistant](https://www.home-assistant.io/) integration, installable through [HACS](https://hacs.xyz/),
for Dutch amateur football: the fixtures, results and standings of any team of any KNVB club.

> **Status: early development.** Nothing to install yet. The data source has been investigated and
> works (see [docs/api.md](docs/api.md)); the integration itself is being built. Contributions welcome,
> see [Contributing](#contributing).

## What it will do

Log in with your own (free) [voetbal.nl](https://www.voetbal.nl) account, then pick teams: the ones you
already follow in the voetbal.nl app, or any team of any club. Per team you get:

- **A calendar** with every match: opponent, home or away, venue, and the score once it has been played.
- **Sensors**: *next match* (start time, opponent, home/away, venue), *last result* (score, win/draw/loss)
  and *standing* (position, points, played, goals).

An account doesn't need to belong to a club member: a spectator or parent account can follow any team.

## How it works, and the catch

The data comes from the same API the official voetbal.nl app uses (run by Sportlink for the KNVB). There
is no public API without a login: the voetbal.nl website and its data require an account, and the
official alternative for clubs (Sportlink Club.Dataservice) needs a key per club.

So this integration **logs in the way the app does**: with your email and password once, after which it
only keeps a refresh token. That means it presents itself as the app. It is **not affiliated with or
endorsed by the KNVB or Sportlink**, and they can change or block this at any time; an app update may
also require an update here. If the KNVB or Sportlink asks us to change or stop something, we will.

**Privacy.** The integration only reads competition data: clubs, teams, matches, results, standings.
It never fetches team rosters or other personal data (rosters often list minors), and it doesn't store
your password.

## Contributing

Issues and pull requests are welcome, in English or Dutch.

- Read [docs/api.md](docs/api.md) for how the data source works.
- [tools/probe.py](tools/probe.py) is a small standalone script to explore the API from the command line,
  with your own account (see the script for the environment variables it needs).
- Planned work is tracked in the [issues](../../issues).

Please don't commit personal data (names, rosters, tokens, account details) in examples or tests; use
made-up or anonymised data.

## License

[MIT](LICENSE)
