# Setting this up for your own eCalendar account

This gives Claude the same eCalendar capability on **your own account** with **your own
credentials**. Nothing is shared between accounts — you can't reuse anyone else's token,
device id, instance id, or category ids; they're tied to a sign-in.

`ecal.py` reads five values from environment variables (never hardcode them, never commit
them):

| Variable | What it is |
|---|---|
| `ECALENDAR_TOKEN` | Bearer token for your eCalendar account. **Full read/write to every event, note, and task on the account.** Treat it like a password. Doesn't expire on a timer, but **any new sign-in to the account (on any device) replaces it** — the old one then fails with `Account logged in on another device`. |
| `ECALENDAR_DEVICE` | The numeric `deviceId` the app sends with every request. |
| `ECALENDAR_INSTANCE` | The app's `clientInstanceId` (a UUID) — only used for Notes. |
| `ECALENDAR_CATEGORIES` | JSON mapping short names to your calendar category ids, e.g. `{"family":"123","kid_a":"456"}`. |
| `ECALENDAR_DEFAULT_CATEGORY` | Optional — which key above to use when none is given. Defaults to `family`/`home` if present, else the first entry. |

---

## The easy path — you're signed into eCalendar on a Mac

Every value lives in the macOS app's own preferences file. One script pulls all of them.

1. Open the **eCalendar** app on your Mac, sign in, and view your calendar once (so it
   writes its state to disk).
2. Clone this repo (or just download `extract_ecal_config.py`) and run it:

   ```bash
   python3 extract_ecal_config.py
   ```

3. It prints the five `ECALENDAR_*` lines ready to paste, plus a list of your category
   keys and what each one is in the app. Copy that output — the token is
   password-equivalent, so don't paste it into anything but the secret store in Step 4.

That's it for capturing values. Skip to **Step 4**.

> How it works: the script reads
> `~/Library/Containers/com.fujia.ecalendar/Data/Library/Preferences/com.fujia.ecalendar.plist`
> — `flutter.user` → `.token`, `flutter.deviceMemory` → device id,
> `flutter.notesSyncClientInstanceId` → instance id, and
> `flutter.allEventCategories_<deviceId>` → the category list (it keeps the ones you can
> write to and drops external synced feeds like Google/iCloud/TeamSnap).

---

## The hard path — iOS only, no Mac signed into eCalendar

You'll capture the values from the app's own network traffic with a local HTTPS proxy
such as [Proxyman](https://proxyman.io) or [mitmproxy](https://mitmproxy.org).

1. Install the proxy tool and trust its root certificate on the device running eCalendar
   (each tool documents this — required to see HTTPS bodies/headers).
2. Point the device's Wi-Fi at the proxy.
3. In the app: view the calendar, **add a test event**, then open **Notes** (the Notes
   sync request is the one that carries `clientInstanceId`).
4. In the proxy's capture list, find requests to `api.cd.myecalendar.com`:
   - request header `authorization: Bearer <token>` → `ECALENDAR_TOKEN`
   - request body `"deviceId": "..."` → `ECALENDAR_DEVICE`
   - on `/app/note/sync/push`, body `"clientInstanceId": "..."` → `ECALENDAR_INSTANCE`
5. **Category ids:** capture `/app/note/sync/full` (open Notes) — or any request whose
   response includes your category list — and read each category's
   `userCalendarCategoryId` + `categoryName`. Build the JSON, picking your own short
   keys: `{"family":"<id>","kid_a":"<id>",...}`.
   *(Do not try to read category ids from an `/app/event/list` response — that endpoint
   returns `userCalendarCategoryIds: null`.)*
6. **Remove the proxy config from the device when you're done**, and delete the saved
   capture. If the app signed in while you were capturing, the `/app/user/login` request
   has your **password in plain text**.

---

## Step 4 — your own repo + Claude Code project

1. Create your **own private** GitHub repo. Copy `ecal.py` and `SKILL.md` into
   `.claude/skills/ecalendar/` in it. (Copy the files — don't fork this repo; a fork
   carries this repo's identity and is tied to a different account's setup.)
2. Edit the **"Which calendar category"** section of `SKILL.md` to name your own people
   and category keys (from the script output / Step 5 above).
3. At [claude.ai/code](https://claude.ai/code), add your repo as a project.
4. In the project's **cloud environment → Environment variables**, add all five
   `ECALENDAR_*` values. None go in the repo.
5. **Network access:** the skill calls `api.cd.myecalendar.com`. If the environment
   offers a custom allow-list, add that host. If it only has presets ("Trusted" etc.),
   "Trusted" is **not** enough — it blocks the host with a 403 — so pick the most
   permissive option available.

## Step 7 — verify

Start a session in that project and run:

```bash
python3 .claude/skills/ecalendar/ecal.py
```

It prints the token source and who it's signed in as, today's UTC offset for your
`ECALENDAR_TIMEZONE`, and your events for today. If a variable is missing it says which
one. If you get a 403 / tunnel error, fix the network access setting (Step 4.5).

## If it stops working: "Account logged in on another device"

The token doesn't expire on its own, but signing in to eCalendar again — on any phone,
Mac, or the wall display — issues a new token and invalidates the old one. `ecal.py`
raises `TokenReplacedError` when that happens. Re-run `extract_ecal_config.py` on a Mac
signed into the app (or re-capture) and update `ECALENDAR_TOKEN` wherever you set it.
The device id and category ids don't change.

Then try it for real: *"what's on my calendar next week?"*, then
*"add a test event tomorrow at 2pm called hello, then delete it."*
