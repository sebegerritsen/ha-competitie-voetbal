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

Returns a new access token **and a new refresh token**. Refresh tokens are **single use**: the old refresh
token is refused right after (`400 {"error": "invalid_grant"}`) and the old access token too (401), so
store the new one at once and never refresh twice in parallel. A wrong password gives
`400 {"error": "Inloggen niet mogelijk", "description": "onjuiste inloggegevens"}`. Several sessions per
account coexist: logging in elsewhere (the app on a phone) does not end an older session. The client id
and secret are fixed values from the app (field name `secret`, not `client_secret`). `POST /oauth/logout` ends the
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

Responses carry `ETag` and `Cache-Control: max-age=86400, s-maxage=300, private`. Conditional requests
(`If-None-Match`) get 304 on some endpoints (`user/favorite/Favorites`), but not on those with `s-maxage`
(`team/TeamProgram` answered 200 with the same ETag): send them, don't rely on them. A wrong `v` gives
status **603** ("Unknown entity version").

| Request | Returns |
|---|---|
| `club/Clubs?v=1` | All ~2,800 clubs: `ClubId` (e.g. `BBxx00X`), `ClubName`, `City`, `ClubLogo`. |
| `club/ClubTeams?v=1&ClubId=<id>` | The club's teams: `PublicTeamId` (`T…`), `TeamCode` (`O12-1`, `1`, …), `TeamName`, `Gender`, `SportId` (`SOCCER-VE-AL` = field, Saturday/Sunday in `SportTag`), `SportDescription`, `Class[]`. |
| `team/TeamProgram?v=3&PublicTeamId=<id>` | Upcoming matches **of the team's pool, not only the team's own** (own first): `ProgramItemMatch[]` → `Match` with `PublicMatchId`, `MatchDateTime` (`2026-11-14T10:00:00+0100`), `Status` (`SCHEDULED`, …), `Location` (venue name), `DisciplineStatus`, `Pool`, `HomeTeam`, `AwayTeam`. Filter on `PublicTeamId`. |
| `team/TeamCompetitionData?v=2&PublicTeamId=<id>` | The team's pools this season (a youth team typically has one per phase): `PoolId`, `ClassName`, `PoolName`, `CompetitionKind` (`DEFAULT_COMPETITION`, `TROPHY_COMPETITION` = cup), `NextMatchDate`, `PreviousMatchDate`; plus results outside pools (`UnboundMatchResults`, `SportlinkClubMatchResults`, `TournamentMatchResults`, each `{MatchResult: []}`). |
| `team/TeamPoolAssignments?v=2&PublicTeamId=<id>` | Pool assignments over the season. |
| `pool/PoolCompetitionData?v=2&PoolId=<id>` | The pool's results (`MatchResult[]`: `Status` `FINAL`, `StatusDescription`, `HomeResult`/`AwayResult` with `Score`, `ExtensionScore`, `PenaltyScore`) and standing (`PoolStanding.PoolStandingTeam[]`: `Position`, `TotalPoints`, `TotalMatches`, `Won`, `Draw`, `Lost`, `GoalsFor`, `GoalsAgainst`, `GoalsDifference`, `PenaltyPoints`). |
| `team/TeamMatchEventStatistics?v=2&PublicTeamId=<id>` | Match event statistics (goals etc.). |
| `match/MatchFacility?v=2&PublicMatchId=<id>` | Where a match is played: `FacilityName`, `Address`, `ZipCode`, `City`, `SubFacilityName` (pitch, `veld 2`), `HomeDressingRoom`, `AwayDressingRoom`, `DepartureTime` (`09:20:00`), `AssemblyTime` (meet time, often null), `PhoneNumber` (the venue's). |
| `match/MatchInfo?v=1&PublicMatchId=<id>` | Match number and district, and the class rules in `ClassExtraAttributes`, incl. `NetPlayingTime` (minutes on the clock: 90 seniors, 80 O17, 60 O11–O13, 50 O10, 40 O8) and `GrossPlayingTime` (the booked slot: 135 seniors, 105 O13). Same for every match of a class. |
| `club/ClubProgram?v=3&ClubId=<id>`, `club/ClubMatchResults?v=2&ClubId=<id>` | The club's programme (`ProgramItemMatchClub[]` → `Match`, `TeamCode` is `-` there) and its latest 100 results (`MatchResult[]`). On a Saturday morning a mid-sized club had 42 matches in its programme; results of that day appeared in `ClubMatchResults` within hours, while some teams' results took longer. |
| `user/favorite/Favorites?v=2` | The account's followed teams/clubs (`FavoriteTeam[]` with `PublicTeamId`, `Club.ClubId`): handy for a config flow ("pick from your favourites"). |
| `user/User?v=2`, `user/UserPersonalData?v=4` | The account itself (personal data: don't log). |
| `team/TeamPersons?PublicTeamId=<id>` | The team roster: **names of players, often minors. An integration should not fetch or store this.** |

Pool answers also carry `PeriodStanding[]` for senior pools: `{PeriodNumber, PoolStanding: {PoolStandingTeam[]}}`
per period (same rows as the standing), and `Topscorers` (names of players: not used). Teams and clubs carry
`Club.ClubColors.HomeOutfit/AwayOutfit` (`Shirt`, `Shorts`, `Stocking`), and programme items `ExternalMatchId`
(the match number people see in the app). Club logos are public at
`https://logoapi.voetbal.nl/logo.php?clubcode=<ClubId>` (PNG).

Match status: `Status` is `SCHEDULED` or `FINAL` (`StatusDescription` "Uitgespeeld") so far. A called-off
match keeps `SCHEDULED` with empty scores and says why in `DisciplineStatus`/`DisciplineStatusDescription`,
e.g. `ADO` / "Afgelast door vereniging". How a live match, an abandoned one or a later kick-off show up is
still to be seen (issue #9).

More endpoint names are in the app (club news, agenda, match details, officials, transport, presence):
`club/ClubProgram`, `club/ClubMatchResults`, `match/MatchInfo`, `match/MatchFacility`, `pool/PoolProgram`, …;
parameters and versions to be found the same way (the error `603 Unknown entity version` means wrong `v`).

## Caveats for an integration

- Unofficial: we impersonate the app (client id/secret, User-Agent). The KNVB/Sportlink can change or
  block this; an app update can change versions. Say so in the README.
- Store only the refresh token after setup, not the password (re-auth flow when it expires).
- Poll gently and use ETags: the server already allows caching for 5 minutes to a day.
- Never fetch rosters or personal data.
