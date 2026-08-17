# Journey Church reskin, notes

## What changed

| Area | Before | After |
|------|--------|-------|
| Logo | Between Sundays SVG wordmark | The Journey Church reversed logo, cropped and embedded as base64 |
| Chrome | `#0B1026` near-black navy | `#2F3E24` Journey deep green |
| Accent | `#2563FF` blue | `#485B38` Journey green |
| Emphasis | none | `#F6C14B` Journey gold |
| Page background | `#F5F7FC` cool white | `#F2F0E7` bone |
| Body text | `#0B1026` | `#1A1A1A` |
| Display face | Poppins | Montserrat 600/700 |
| Body face | Poppins | Inter 400/500/600 |
| Numerals | IBM Plex Mono | Montserrat, tabular figures |
| Tenant record | Between Sundays, Chicago IL | The Journey Church, Jackson MO |
| App domain | derived `app.bschurch.org` | explicit `app.thejourneychurchsemo.com` |

Every color is a token. No template markup was edited to change the look.

## Gold rule

Gold measures 1.66:1 against white. It cannot carry text and cannot sit
behind a label. It is used in exactly three places:

1. The status dot in the demo bar, on deep green
2. The left rule on the active sidebar item, on deep green
3. The Journey rail stage bars, where the count is printed directly above
   the bar, so the bar is duplicative rather than load-bearing

Everything that carries white text uses `#485B38`, which measures 7.42:1.

## Files

- `index.html` — single file, self contained, logo embedded. Send as is.
- `app/brand.py` — the token module for the Flask build. Drop in at
  increment 0. Includes `assert_accent_readable()` so a pastor pasting a
  yellow hex into Settings is stopped in the form rather than discovered
  in a lobby.
- `app/static/img/journey-logo-white.png` — cropped reversed logo, 404x135,
  transparent. The source file was 500x500 with 73 percent empty margin,
  which would have rendered as a small logo in a large box.

## Flags, not changed without your call

1. **Scale.** The seeded data shows 400 people and $38,420 monthly giving.
   Journey runs about 60. A pastor will notice on slide one. Rescaling
   touches `STAGE_COUNTS`, the metrics block, the giving charts, and the
   reports, so it is a separate pass. Say the word and I will do it.
2. **Cost comparison.** Settings still shows the old `$404 replaced,
   $304 saved` math. Spec v3 section C.6 rules that to `$294 replaced,
   $194 saved`. That is open item 1 awaiting your sign-off.
3. **Kiosk PIN copy.** The kiosk still asks for the last four digits of a
   phone number. Spec v3 decision 3 replaced that with an auto-generated
   household PIN. Copy for the replacement screen is open item 2a.
4. **Church health score.** The dashboard still shows a composite
   `74 of 100`. Spec v3 section E.5 drops it in favor of the four ratios,
   which are already on the card below it.

None of the four are brand issues. All four are demo-versus-spec drift.
