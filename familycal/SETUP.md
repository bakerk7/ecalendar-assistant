# Set up your own Family Calendar

A wall-tablet family calendar styled like the Dragon Touch eCalendar: a native Android app (`android/`) plus a
small helper server (`server/`) that talks to Google Calendar, keeps per-person tags, chores, stars and rewards.

## What you need
* **An Android tablet** (Android 8+/API 26) with USB/network debugging (`adb`). No Google Play Services needed.
* **A Docker host** on the same network (a NAS, mini PC, Raspberry Pi 4…). The tablet talks to it on port 8091.
* **A Google service account** with access to your family calendar (see `GOOGLE-SETUP.md`). Optional: until it
  is set up the server runs on local fixture data (`FAMCAL_SOURCE=fixture`).
* To build the APK: JDK 17, Android build-tools 34, platform android-29 and `kotlinc` under `~/android`
  (see the header of `android/build.sh`). No Gradle.

## 1. Configure your family
```
cp config.example.json config.local.json      # gitignored, never committed
```
Edit `config.local.json`:

| key | what it does |
| --- | --- |
| `family` | name, colour, emoji and the single `letter` shown on the shared "everyone" avatar |
| `profiles` | people/pets in order: `id`, `name`, `color`, `emoji`, `aliases` (words in an event title that tag that person) |
| `calendars` | the writable shared Google calendar and an optional read-only ICS feed; `env` names the env var that holds its id/URL |
| `rules` | auto-tagging, e.g. `{"kind":"keyword","pattern":"\\bsoccer\\b","profiles":["alex"]}`; `calendar` rules tag everything in a feed |
| `weather` | `place`, `lat`, `lon` for the Open-Meteo forecast |
| `importer` | only for migrating from the stock eCalendar app's database |
| `app.server_url` | default helper URL baked into the APK (can be changed later in the app's settings) |
| `app.profile_order` | left-to-right order of people on the tablet (empty = order of `profiles`) |
| `app.color_overrides` | tablet-only colour per profile id |
| `app.avatar_dir` | optional folder (relative to the config file) with `av_<id>.png` photos; otherwise coloured initials |
| `app.camera` | optional camera button next to "+": `package` + `receiver` of an app that shows a camera when it gets an explicit broadcast (e.g. a doorbell overlay). Leave `package` empty to hide the button |
| `app.signing` | your keystore and its password file |

Profiles, calendars and rules only seed an **empty** database; after the first start edit them in the app or web page.

## 2. Run the helper server
```
scp -r server/ you@dockerhost:familycal/      # or git clone on the host
cp config.local.json  <host>:familycal/data/config.json
```
On the host, create `familycal/.env` (never commit it):
```
FAMCAL_API_KEY=<long random string>      # the tablet and web page use this
FAMCAL_SOURCE=fixture                    # switch to google after GOOGLE-SETUP.md
TZ=America/New_York
TZ_NAME=America/New_York
FAMCAL_SHARED_CAL_ID=<calendar id>@group.calendar.google.com   # when using Google
FAMCAL_SOCCER_ICS=<public ICS URL>                             # optional feed
```
then `docker compose up -d --build` in `familycal/` and check `http://<host>:8091/health`.
Tests: `cd server && pip install -r requirements.txt pytest httpx && python -m pytest`.

## 3. Google Calendar
Follow `GOOGLE-SETUP.md`: create a service account, share your family calendar with its email ("Make changes to
events"), install the key with `GOOGLE_SA_JSON=key.json FAMCAL_HOST=you@dockerhost ./install-sa.sh` (never commit
the key), set `FAMCAL_SOURCE=google` and restart.

## 4. Build and sign the app with your own key
```
mkdir -p ~/.familycal
keytool -genkeypair -keystore ~/.familycal/release.keystore -alias familycal -keyalg RSA -keysize 2048 -validity 10000
printf '%s' '<the password>' > ~/.familycal/release.keystore.pass && chmod 600 ~/.familycal/release.keystore.pass
cd android && ./build.sh            # -> build/FamilyCalendar.apk
```
`build.sh` reads `../config.local.json` (or `FAMCAL_APP_CONFIG=...`, `FAMCAL_URL=...`). Keep the keystore: updates
must be signed with the same key or Android refuses to install them over the old app.

## 5. Install on the tablet
```
adb connect <tablet-ip>:5555
adb install -r android/build/FamilyCalendar.apk          # -r keeps the app's data on updates
adb shell am start -n com.bakerk7.familycalendar/.MainActivity --es url http://<host>:8091 --es key <FAMCAL_API_KEY>
```
Long-press the clock for settings (server, key, sleep hours, default view). To make it the home screen, pick it
as the launcher in Android settings (optional; it does not replace or remove the stock app).

## Optional extras (config.json)

- **Morning glance** – `app.morning_glance {enabled, start, end}`: a summary page (today's events, weather, each person's open chores and next reward) shown when the screen wakes inside the window. Tap to dismiss. Also in Settings → Sleep.
- **Night mode** – `app.night {evening_dim, evening_start, evening_level, sleep_start, sleep_end}`: the app dims its own window from `evening_start` until sleep starts, then shows the big clock. A touch wakes it. Settings → Sleep overrides these.
- **Rewards** – `rewards [{title, emoji, cost, profile_id?}]` seed an empty database only; manage them in Settings → Rewards.
- **Door alerts** – `doors {threshold_min, sensors:[{name, entity}]}` with Home Assistant `binary_sensor` door contacts. Put `HA_URL` and `HA_TOKEN` (a long-lived token) in the server's `.env`; the helper only reads `/api/states/<entity>`, and the tablet never sees the token. A pill such as "Garage open 12m" appears above the + button for every door (green "closed", orange "open 2m"; `threshold_min` only affects the server `alerts` list, default 0), refreshed every ~3 s. Test without opening doors: `POST /api/doors/fake {"name":"Garage","minutes":12}`, clear with `{"clear_all":true}`.
