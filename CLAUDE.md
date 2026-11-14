# KNVB Voetbal: notes for working on this repo

Public open-source project (GitHub: sebegerritsen/ha-knvb-voetbal), meant for HACS, a sibling of
ha-knhb-hockey (same structure and conventions; look there for patterns). Everything in this repo is
public: **no personal data** — no account details, no real people, no rosters, no tokens, no details of
the owner's home or club. Use made-up teams/clubs or anonymised data in tests and examples.

## What it is

A Home Assistant custom integration (`custom_components/knvb_voetbal`, domain `knvb_voetbal`) for Dutch
amateur football data from the API behind the official voetbal.nl app (Sportlink, KNVB). How the API
works (OAuth password + refresh grant, required headers incl. the app User-Agent and
`X-Navajo-Version` = `v`, endpoints, data shapes): docs/api.md. `tools/probe.py` calls it.

Decided (Nov 2026):
- The user logs in with their own voetbal.nl account (spectator/parent accounts work). Store only the
  refresh token after setup (it rotates on every refresh: save the new one), never the password;
  implement a reauth flow for when it expires.
- Config flow: log in → pick teams from the account's favourites (`user/favorite/Favorites?v=2`) or search
  a club (`club/Clubs?v=1`) and pick a team (`club/ClubTeams?v=1`). One config entry per account with a
  subentry per team is worth considering, since one login serves many teams.
- Per team: calendar (from `team/TeamProgram?v=3` + results from `pool/PoolCompetitionData?v=2`), sensors
  next match, last result, standing.
- Use ETags (`If-None-Match`); poll gently.
- **Never** call `team/TeamPersons` or other person endpoints; rosters list minors.
- The app's client id and secret are fixed values in the app; where they live in the code (constant vs.
  configurable) is still to decide. Mention clearly in the README that the integration acts as the app.

## Testing locally

Copy `custom_components/knvb_voetbal` into your Home Assistant `custom_components/` folder and restart
Home Assistant. Personal setup notes (paths, test account) belong in `CLAUDE.local.md`, which is
git-ignored.
