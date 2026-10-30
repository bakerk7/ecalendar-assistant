# Family Calendar v1 (replacement for com.fujia.calendar)

**New here? See [SETUP.md](SETUP.md).** All family-specific settings live in `config.local.json` (copy of
`config.example.json`, gitignored); the server reads it as `/data/config.json`, `android/build.sh` reads its `app` section.

* `server/` is the FastAPI helper (container `familycal`, port **8091**, ~40 MiB). It handles profiles, multi-person
  tags + auto-rules, chores/stars/rewards, lists, weather (Open-Meteo, location from config), a phone web page at `/`,
  and a gateway-compatible `/events` `/profiles` `/categories` API so routines can switch by base URL.
  Event source is behind `CalendarSource`: `FixtureSource` (now) and `GoogleSource` (after GOOGLE-SETUP.md (service account primary, OAuth fallback)).
  * Tests: `cd server && python -m pytest`
  * Import: `python -m famcal.importer calendar_db --data /data`
  * Deploy: `~/docker-stacks/familycal` (with `data/config.json`) (`docker compose up -d --build`), with `.env` holding `FAMCAL_API_KEY`.
* `android/` is the native Kotlin app `com.bakerk7.familycalendar` (Views only, ~130 KB APK). Build it with `./build.sh`
  (kotlinc + R8 + aapt2, no Gradle). Configure it once with
  `am start -n com.bakerk7.familycalendar/.MainActivity --es url http://<server>:8091 --es key <key>`.
  Long-press the clock to open settings (server, key, sleep hours). Tap the night screen to wake it for 60 s.
* Not switched yet (on purpose): the tablet home app, the stock app, and the teacher-email/soccer routines (still on ecal-gateway :8090).
