# Google setup — service account (primary)

1. Google Cloud console → (any project) → enable **Google Calendar API**.
2. IAM → Service accounts → Create → Keys → Add key → JSON. Keep the file private.
3. In Google Calendar (as you@example.com) → *Shared Calendar* → Settings → Share with specific people →
   add the service-account email (`…@….iam.gserviceaccount.com`) with **Make changes to events**.
4. On the box: `export GOOGLE_SA_JSON="$(cat key.json)"` (or a path to the file), then run
   `/workspace/familycalendar/install-sa.sh` (`MIGRATE=1` to also move native tablet events into Google).
   The script never prints the key: it streams it into the helper's `/data/google_sa.json` (mode 600),
   restarts the helper, lists calendars visible to the SA and runs the extendedProperties survival test.
5. Shared calendar id is auto-discovered from the import (`xxxxxxx…@group.calendar.google.com`);
   override with `FAMCAL_SHARED_CAL_ID` in the helper `.env`.
6. Soccer isn't shared with the SA; it is read (read-only) from its ICS URL via `FAMCAL_SOCCER_ICS` in `.env`.

If `google_sa.json` is absent, the helper falls back to the OAuth token flow below.

# Connect Google Calendar (one time, ~10 minutes)

Until this is done the helper runs on a **fixture** copy of the events imported from the
stock app (`data/fixture_calendar.json`). Everything works, but nothing syncs with Google.
The tablet has no Google Play Services, so the bakerserver helper talks to Google. The tablet
never signs in.

## A. Create the OAuth client (Google Cloud console, signed in as you@example.com)
1. https://console.cloud.google.com/ → create the project **Family Calendar** (or reuse one).
2. *APIs & Services → Library* → enable **Google Calendar API**.
3. *APIs & Services → OAuth consent screen*: User type **External**, app name "Family Calendar",
   your email as support/developer contact. Add yourself as a test user.
   Then click **Publish app → In production**. (In "Testing" mode Google expires the refresh
   token every 7 days. Unverified apps in production still work for you; you'll just
   click through the "Google hasn't verified this app" warning.)
4. *APIs & Services → Credentials → Create credentials → OAuth client ID* →
   type **Desktop app**, name "familycal". Leave the dialog open. You need the Client ID + secret.

## B. Sign in (phone or laptop on the home network/tailnet)
1. Open `http://192.0.2.10:8091/?key=<FAMCAL_API_KEY>` (the key is in
   `~/docker-stacks/familycal/.env` on bakerserver: `grep FAMCAL_API_KEY ~/docker-stacks/familycal/.env`).
2. **Settings → Connect Google**. Paste the Client ID and secret → Save.
3. Tap **Sign in with Google**, choose you@example.com → Advanced → Go to Family Calendar →
   Allow (calendar access).
4. The browser lands on `http://127.0.0.1:8765/?state=…&code=…`, which will show "can't connect".
   That's expected. Copy the **whole address** from the address bar, paste it into the box, then tap **Finish**.
5. Pick **Shared calendar** = the shared family calendar, **Soccer** = the "Boys U9 Academy 2 White"
   feed (or leave it blank) → **Connect**.

What happens next: the token is saved to `data/google_token.json` (mode 600). The 109 events that only
existed in the eCalendar cloud are copied into the shared Google calendar. All people tags are written
into each event's `extendedProperties.private.famcalProfiles`, and a sync runs every 60 s from then on.
The stock app and the ecal-gateway are not touched.

Undo: delete `data/google_token.json` and restart the container. It goes back to fixture mode.

### CLI alternative (no browser on the server needed)
```
KEY=$(grep -oP '(?<=FAMCAL_API_KEY=).*' ~/docker-stacks/familycal/.env)
curl -s -H "X-API-Key: $KEY" -X POST localhost:8091/oauth/client -d '{"client_id":"…","client_secret":"…"}' -H 'Content-Type: application/json'
curl -s -H "X-API-Key: $KEY" localhost:8091/oauth/start          # open the "url" it prints, sign in
curl -s -H "X-API-Key: $KEY" -X POST localhost:8091/oauth/finish -H 'Content-Type: application/json' -d '{"url":"<pasted 127.0.0.1 address>"}'
curl -s -H "X-API-Key: $KEY" -X POST localhost:8091/oauth/calendars -H 'Content-Type: application/json' -d '{"shared":"<id>","soccer":"<id>"}'
```
