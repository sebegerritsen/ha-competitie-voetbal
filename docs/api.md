# The voetbal.nl app API (Sportlink, KNVB)

Findings from November 2026, by watching the official voetbal.nl Android app (`com.xs2theworld.voetballNL`
6.64.0, built by Dexels/Sportlink) through a proxy and calling the API directly. Not officially documented;
it may change. `tools/probe.py` calls it from the command line.

## Summary

Logging in with a normal voetbal.nl account works without captcha (the website has reCAPTCHA, the app
doesn't). It is standard OAuth 2 (password grant + refresh token). With the token, every club, team,
programme, result and standing in Dutch amateur football can be read. A spectator/parent account is
enough: an account does not need to be a club member, and can follow any team.

## Authentication

```
POST https://app-vnl-production.sportlink.com/oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=password&username=<email>&password=<password>&client_id=<app client id>&secret=<app secret>
```

Response: `{"access_token", "token_type": "bearer", "expires_in": 3600, "refresh_token"}`. Tokens are opaque.

Renew without the password:

```
grant_type=refresh_token&refresh_token=<refresh token>&client_id=<app client id>&secret=<app secret>
```

Returns a new access token **and a new refresh token** (rotate and store it). The client id and secret
are fixed values from the app (field name `secret`, not `client_secret`). `POST /oauth/logout` ends the
session (the app calls it when you back out of registration).

A new account must complete a short registration once (name, initials, date of birth, gender; address
optional) before the data endpoints work; the app does this on first login (`PUT .../user/UserPersonalData?v=4`).

## Data requests

Base: `https://app-vnl-production.sportlink.com/entity/common/memberportal/app/`

Required headers (without the right User-Agent the server answers **500**):

| Header | Value |
|---|---|
| `Authorization` | `Bearer <access token>` |
| `X-Navajo-Instance` | `KNVB` |
| `X-Navajo-Version` | must equal the `v=` query parameter of the request |
| `X-Navajo-Locale` | `nl` |
| `User-Agent` | the app's format: `sportlink-app-voetbalnl/<version>-<build> android <device>/<os>  (<version>)` |
| `Accept-Encoding` | `gzip` (responses are gzip) |

Responses carry `ETag` and `Cache-Control: max-age=86400, s-maxage=300, private`; conditional requests
(`If-None-Match`) get 304.

| Request | Returns |
|---|---|
| `club/Clubs?v=1` | All ~2,800 clubs: `ClubId` (e.g. `BBxx00X`), `ClubName`, `City`, `ClubLogo`. |
| `club/ClubTeams?v=1&ClubId=<id>` | The club's teams: `PublicTeamId` (`T…`), `TeamCode` (`O12-1`, `1`, …), `TeamName`, `Gender`, `SportId` (`SOCCER-VE-AL` = field, Saturday/Sunday in `SportTag`), `SportDescription`, `Class[]`. |
| `team/TeamProgram?v=3&PublicTeamId=<id>` | Upcoming matches: `ProgramItemMatch[]` → `Match` with `PublicMatchId`, `MatchDateTime`, `Status` (`SCHEDULED`, …), `Location` (venue name), `Pool`, `HomeTeam`, `AwayTeam`. |
| `team/TeamCompetitionData?v=2&PublicTeamId=<id>` | The team's pools: `PoolId`, `ClassName`, `PoolName`, `NextMatchDate`, `PreviousMatchDate`. |
| `team/TeamPoolAssignments?v=2&PublicTeamId=<id>` | Pool assignments over the season. |
| `pool/PoolCompetitionData?v=2&PoolId=<id>` | The pool's results (`MatchResult[]`: `Status` `FINAL`, `StatusDescription`, `HomeResult`/`AwayResult` with `Score`, `ExtensionScore`, `PenaltyScore`) and standing (`PoolStanding.PoolStandingTeam[]`: `Position`, `TotalPoints`, `TotalMatches`, `Won`, `Draw`, `Lost`, `GoalsFor`, `GoalsAgainst`, `GoalsDifference`, `PenaltyPoints`). |
| `team/TeamMatchEventStatistics?v=2&PublicTeamId=<id>` | Match event statistics (goals etc.). |
| `user/favorite/Favorites?v=2` | The account's followed teams/clubs (`FavoriteTeam[]` with `PublicTeamId`, `Club.ClubId`): handy for a config flow ("pick from your favourites"). |
| `user/User?v=2`, `user/UserPersonalData?v=4` | The account itself (personal data: don't log). |
| `team/TeamPersons?PublicTeamId=<id>` | The team roster: **names of players, often minors. An integration should not fetch or store this.** |

More endpoint names are in the app (club news, agenda, match details, officials, transport, presence):
`club/ClubProgram`, `club/ClubMatchResults`, `match/MatchInfo`, `match/MatchFacility`, `pool/PoolProgram`, …;
parameters and versions to be found the same way (the error `603 Unknown entity version` means wrong `v`).

## Caveats for an integration

- Unofficial: we impersonate the app (client id/secret, User-Agent). The KNVB/Sportlink can change or
  block this; an app update can change versions. Say so in the README.
- Store only the refresh token after setup, not the password (re-auth flow when it expires).
- Poll gently and use ETags: the server already allows caching for 5 minutes to a day.
- Never fetch rosters or personal data.
