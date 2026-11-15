# Voetbalcompetitie: notes for working on this repo

Public open-source project (GitHub: sebegerritsen/ha-voetbalcompetitie), meant for HACS, a sibling of
ha-knhb-hockey (same structure and conventions; look there for patterns). Everything in this repo is
public: **no personal data** — no account details, no real people, no rosters, no tokens, no details of
the owner's home or club. Use made-up teams/clubs or anonymised data in tests and examples.

## What it is

A Home Assistant custom integration (`custom_components/voetbalcompetitie`, domain `voetbalcompetitie`) for Dutch
amateur football data from the API behind the official voetbal.nl app (Sportlink, KNVB). How the API
works (OAuth password + refresh grant, required headers incl. the app User-Agent and
`X-Navajo-Version` = `v`, endpoints, data shapes): docs/api.md. `tools/probe.py` calls it.

Decided (Nov 2026):
- **Name: "Voetbalcompetitie"** (domain `voetbalcompetitie`, repo ha-voetbalcompetitie), like its siblings
  Hockeycompetitie and Tenniscompetitie. Was "KNVB Voetbal" and then "Competitie Voetbal" (14 Nov 2026), renamed
  on 15 Nov 2026 before the first release. No "KNVB" or "voetbal.nl" in the name, domain, repo or icon, and
  never their logo: the project must not look official. Mentioning them as the data source and in the
  disclaimer is fine.
- The user logs in with their own voetbal.nl account (spectator/parent accounts work). Store only the
  refresh token, never the password. **Refresh tokens are single use**: every refresh returns a new one
  and the old one dies at once, so the client serialises refreshes behind a lock and the new token is
  written to the entry immediately (`on_refresh_token`). invalid_grant → `ConfigEntryAuthFailed` → reauth.
- **One config entry per account, a subentry (`team`) per team**: one login serves all teams. Initial flow:
  log in → tick favourites. Subentry flow "Add team": a favourite, or search club → pick team. The entry
  reloads only when its set of subentries changes (token updates also fire the update listener).
- Per team (one coordinator): `team/TeamProgram?v=3` (the whole pool's upcoming matches: filter on the
  team), `team/TeamCompetitionData?v=2` (pools of the season + friendlies), `pool/PoolCompetitionData?v=2`
  per pool (finished pools twice a day), `match/MatchFacility?v=2` for the coming week (address, pitch,
  dressing rooms, departure), `match/MatchInfo?v=1` once a day per pool (playing time → event length).
- Entities per team, matching ha-knhb-hockey and more: calendar; sensors next match, next opponent (club
  logo via `logoapi.voetbal.nl/logo.php?clubcode=<ClubId>`), home/away, meet time (the club's DepartureTime,
  else kick-off minus the team's setting), weather at next match (only with a weather entity set; hourly,
  else the daily item of the match date), last result, standing (+ form, points to leader, season over all
  pools); binary sensor match today; event match update (changes.py, snapshot in a Store per team). Team
  settings live in the subentry data (Reconfigure); a changed subentry reloads the entry. Blueprints in
  blueprints/automation/voetbalcompetitie mirror hockey's. Also: statistics sensors (points, goal difference,
  goals, played; state_class measurement), period standings and last round on the standing.
- Second subentry type `club` ("Add club"): KnvbClubCoordinator on club/ClubProgram + ClubMatchResults
  (2 h, 30 min on match days, 15 min while results come in): calendar, matches today, home matches
  today, latest results. Event length without MatchInfo comes from the age group in the team code.
- Actions `get_matches` / `get_standing` (services.py, response only, by device). A 603 from the API
  raises KnvbApiChangedError → repair issue `api_changed`, cleared on the next good update.
- HA 2026.x: devices belong to one config entry (`DeviceEntry.config_entry_id`); `config_entries` and
  `async_get_device(identifiers=)` are deprecated — services.py handles both old and new.
- Send ETags (`If-None-Match`); only some endpoints answer 304. Poll gently (30 min, 5 min around matches).
- **Never** call `team/TeamPersons` or other person endpoints; rosters list minors. The client refuses
  any path containing "person".
- The app's client id/secret and User-Agent are constants in `api.py` (they are in the public app anyway);
  an app update may need a release. The README says clearly that the integration acts as the app.

## Development

Tests and lint as in the sibling project: a Python 3.14 venv with `requirements_test.txt` + ruff, then
`pytest` and `ruff check . && ruff format --check .` (CI runs the same, plus hassfest and HACS). CI also runs
the tests on the oldest supported Home Assistant, 2025.12 (`hacs.json`), with Python 3.13 and
pytest-homeassistant-custom-component 0.13.298 (+ `pycares<5`; locally also `lru-dict==1.4.1`, which has a
wheel). Raise `homeassistant` in hacs.json together with that job, never just one of them.
Fixtures in tests/fixtures are made up in the API's exact shapes.

## Testing locally

Copy `custom_components/voetbalcompetitie` into your Home Assistant `custom_components/` folder and restart
Home Assistant. Personal setup notes (paths, test account) belong in `CLAUDE.local.md`, which is
git-ignored.
