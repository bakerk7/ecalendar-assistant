# eCalendar Assistant

A [Claude Code](https://claude.ai/code) skill that gives Claude read/write access to
your family's **eCalendar** (`com.fujia.ecalendar`) account — the shared calendar,
task/chore list, notes, and meal planner behind the Dragon Touch digital calendar and
its iOS/macOS companion apps.

Point a phone photo of a school flyer at it and say "add this" — Claude reads the
date, time, and location off the image, picks the right family member, checks for
duplicates, and creates the event. Same idea for "what's on the calendar Thursday?",
"give Sam a chore to take out the trash, 2 stars", "put this lunch menu in Meals",
or "drop a note in the app with this photo attached."

There's no public eCalendar API — this repo talks to the same private endpoints the
official apps use, reverse-engineered from real device traffic. `SETUP.md` walks
through pointing it at your own account.

## Contents

- [Supported devices](#supported-devices)
- [What this can do](#what-this-can-do)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [`ecal.py` API](#ecalpy-api)
  - [Events](#events)
  - [Tasks / chores](#tasks--chores)
  - [Notes](#notes)
  - [Meals](#meals-recipes--meal-plan)
- [How it works](#how-it-works)
- [API quirks](#api-quirks-already-handled-by-ecalpy)
- [Project layout](#project-layout)
- [Security](#security)
- [Contributing](#contributing)

## Supported devices

eCalendar (`com.fujia.ecalendar`) is the account/app this repo talks to. It runs on:

- **Dragon Touch Digital Calendar** — the wall-mounted family hub display
- iOS
- macOS

All three share one account through the same private API, so this template works
identically no matter which device you set it up against. The capture steps in
`SETUP.md` were written against the phone/Mac app's UI — if something doesn't line
up on a Dragon Touch specifically, open an issue or PR.

## What this can do

| Area | Examples |
|---|---|
| **Events** | timed, all-day/deadline, and recurring events; anniversaries; edit/delete (including a single instance of a recurring series) |
| **Tasks / chores** | assign a chore to a family member, with an optional star reward, one-off or recurring |
| **Notes** | freeform notes, optionally with photos attached |
| **Meals** | import a recurring menu (school breakfast/lunch, meal prep) into the app's recipe + meal-plan system |

Claude does the fuzzy part — pulling structured data out of a photo or a sentence,
resolving relative dates, picking the right person's calendar, deduping before it
writes, and asking when something's genuinely ambiguous — via the `ecalendar` skill
in `SKILL.md`. `ecal.py` is a plain Python module too, so you can call it directly
without the skill if you just want a scripting interface to your calendar.

## Quick start

1. **Get your account values.** On a Mac signed into eCalendar, this is one command:

   ```bash
   python3 extract_ecal_config.py
   ```

   It prints everything below, ready to paste. iOS-only? See `SETUP.md` for the
   HTTPS-proxy capture route instead.

2. **Create your own private repo.** Copy `ecal.py` and `SKILL.md` into
   `.claude/skills/ecalendar/` in it — don't fork this one, a fork carries this
   account's identity. Edit the *"Which calendar category"* (and, if you're using
   Meals, *"Which meal category"*) section of `SKILL.md` with your own family
   members and category keys.

3. **Add the repo as a project** at [claude.ai/code](https://claude.ai/code) and set
   the values from Step 1 as the project's environment variables. If the environment
   restricts outbound network access, allow `api.cd.myecalendar.com` — the "Trusted"
   preset alone isn't enough and will 403 it.

4. **Verify:**

   ```bash
   python3 .claude/skills/ecalendar/ecal.py
   ```

   Prints the token source, today's UTC offset for your configured timezone, and
   today's events — or tells you which variable is missing.

5. **Try it.** In a Claude Code session on that project: *"what's on my calendar next
   week?"*, then *"add a test event tomorrow at 2pm called hello, then delete it."*

Full walkthrough, including the iOS-only path: [`SETUP.md`](SETUP.md).

## Configuration

Everything is read from environment variables at import time. Nothing is hardcoded,
and nothing belongs in the repo.

| Variable | Required | What it is |
|---|---|---|
| `ECALENDAR_TOKEN` | ✅ | Bearer token for your account. Full read/write to every event, note, task, and meal on the account — treat it as a password. Doesn't expire on a timer, but **any new sign-in to the account (on any device) replaces it** — the old one then fails with `Account logged in on another device`. |
| `ECALENDAR_DEVICE` | ✅ | The app's numeric `deviceId`, sent with every request. |
| `ECALENDAR_INSTANCE` | Notes only | The app's `clientInstanceId` (a UUID). |
| `ECALENDAR_CATEGORIES` | ✅ | JSON mapping short names to your calendar category ids, e.g. `{"family":"123...","kid_a":"456..."}`. |
| `ECALENDAR_DEFAULT_CATEGORY` | optional | Which key above to use when `category=` is omitted. Defaults to `family`/`home` if present, else the first entry. |
| `ECALENDAR_TIMEZONE` | optional | IANA zone your account actually uses, e.g. `America/Chicago`. **Defaults to `America/New_York` — check yours, most accounts aren't Eastern.** |
| `ECALENDAR_MEAL_CATEGORIES` | Meals only | JSON mapping short names to your meal category ids, e.g. `{"breakfast":"123...","lunch":"456..."}`. |

See `SETUP.md` for exactly how to find each value.

## `ecal.py` API

No dependencies beyond the standard library.

### Events

```python
import ecal

# timed event; end defaults to start + 1h
ecal.create_event("Dentist", "2026-03-14 15:30:00",
                  category="kid_a", location="Some Dental Office", reminders_min=(30,))

# all-day / deadline event
ecal.create_event("Picture Day", "2026-10-03", all_day=True,
                  category="kid_a", description="wear the green shirt")

# recurring event (ecal.DAILY / WEEKLY / MONTHLY / YEARLY; week_days 0=Sun..6=Sat)
ecal.create_event("Piano lesson", "2026-09-15 16:00:00", category="kid_b",
                  recur=ecal.recurrence(ecal.WEEKLY, week_days=[1], end="2026-12-15"))

# read the calendar (range is auto-widened; single days are fine)
ecal.events_on("2026-09-11")
ecal.list_events("2026-09-07", "2026-09-13")
ecal.find_duplicate("Picture Day", "2026-10-03")     # -> row or None; call before create

# edit (same fields as create; needs the eventId)
ecal.edit_event(event_id, "Dentist (moved)", "2026-03-14 16:00:00", category="kid_a")
#   for one instance of a recurring series, also pass update_method (0 this / 1 all /
#   2 this+future) + origin_event_id / origin_start / origin_end from the row

ecal.delete_event(event_id)                          # one event / instance
ecal.delete_event(series_root_id, series=True)       # whole recurring series

# anniversary (all-day, yearly-recurring, shows in the Anniversaries tab)
ecal.create_anniversary("Our anniversary", "2020-10-22", category="family", pinned=True)
```

### Tasks / chores

```python
ecal.create_task("Wash dishes", category="kid_a", stars=2, emoji="BOWL WITH SPOON")
ecal.create_task("Feed the dog", category="kid_b", emoji="DOG FACE",
                 recur=ecal.recurrence(ecal.DAILY))             # repeating chore

ecal.list_tasks("2026-09-11", categories=["kid_a", "kid_b"])
ecal.delete_task(task_event_id)                                 # series=True if recurring
```

A **task** is a chore assigned to a person, living in the app's Tasks tab. Stars
default to **0** (no reward); pass `stars=` to give one. A dated deadline or
appointment with no assignee is an **event** instead — use `create_event`.

A **routine** is a task pinned to a time of day (morning / afternoon / evening) that
repeats daily. The app creates one with `taskMode: 1` and `routinePeriods`, and gets back
one series per period:

```python
ecal.create_routine("Brush teeth", category="kid_a", periods=["morning", "evening"])
#   -> [morning_event_id, evening_event_id]; delete_task(id, series=True) should remove one (not yet verified)
```

### Account & summary (read-only)

```python
ecal.whoami()                  # signed-in user -- quick "is the token still good?" check
ecal.family()                  # families + paired devices (e.g. the wall display)
ecal.summary()                 # today's event / open-task counts, like the app's home screen
ecal.category_stats()          # each person's star balance + today's task progress
```

### Notes

Needs `ECALENDAR_INSTANCE`.

```python
ecal.create_note("Title", "plain body\nsecond line",
                 image_paths=["/path/to/flyer.jpg"])   # images optional, max 8
ecal.update_note(note_id, content="revised body")
ecal.delete_note(note_id)                              # or archive_only=True
```

### Meals (recipes + meal plan)

Needs `ECALENDAR_MEAL_CATEGORIES`. This is a separate feature from events/notes, with
its own two-step data model: a **recipe** (the food item) is created once, then a
**meal-plan entry** attaches it to a date under a category (Breakfast/Lunch/Dinner/
Snack).

```python
ecal.add_meal("Pancakes", "2026-09-10", category="breakfast")   # create-or-reuse + attach
ecal.add_meal("Pancakes", "2026-09-24", category="breakfast")   # reuses the same recipe

ecal.list_meals("2026-09-01", "2026-09-30")             # all configured categories
ecal.list_meals("2026-09-01", "2026-09-30", ["lunch"])  # just lunch

# lower-level, if you need them:
ecal.find_recipe("Pancakes")                             # -> mealRecipeId or None
ecal.create_recipe("Pancakes", "syrup, butter")           # add_meal calls this for you
ecal.create_meal(recipe_id, "2026-09-10", category="breakfast")
```

**The one thing to know**: recipe names must be unique **account-wide**, not per
category — a second recipe called "Pancakes" fails even under a different category,
or against a name the app already seeded. This is the norm, not an edge case, when
importing a recurring menu where the same dishes repeat weekly — `add_meal`/
`create_recipe` handle it by looking up an existing recipe by exact name before
creating, so always go through them rather than the raw `POST /app/v2/recipe` call.

Full endpoint reference — every request/response body this module sends, including
recurrence rules and the task model — is in [`ecalendar-api.md`](ecalendar-api.md).

## How it works

```
             ┌──────────────┐        HTTPS         ┌─────────────────────────┐
 you  ─────▶ │  Claude Code │ ───────────────────▶  │ api.cd.myecalendar.com  │
 (photo,     │  + ecalendar │   same private API    │ (the private mobile API │
  text)      │     skill    │   the real app uses   │  backing eCalendar)     │
             └──────┬───────┘                        └────────────┬─────────┘
                     │ ecal.py (bearer token auth)                 │
                     ▼                                             ▼
              your Claude Code                          Dragon Touch display /
              project's env vars                        iOS app / macOS app
              (ECALENDAR_TOKEN etc.)                     — same account, live
```

- **`ecal.py`** is the tested client for that API: request/response shapes,
  auth, timezone handling, pagination quirks, and dedup logic all live here.
- **`SKILL.md`** is what makes Claude *good* at using it — resolving "next Thursday,"
  deciding all-day vs. timed, picking the right family member, confirming before a
  write only when something was actually inferred.
- **`ecalendar-api.md`** is the raw endpoint reference underneath `ecal.py`, useful if
  you're extending the module or debugging a request by hand.

## API quirks (already handled by `ecal.py`)

- `/app/event/list` **rejects any range narrower than 7 days** — `list_events`
  fetches a widened window and filters locally.
- `/app/event/add` returns `{"code":200,"data":null}`, **no event id** — dedupe with
  `find_duplicate`/`events_on` rather than trusting the response.
- Event/meal times are sent as local wall time plus a numeric `zone`/`addZone`
  offset, auto-picked per date for `ECALENDAR_TIMEZONE`. Times read back may be local
  (app-created) or UTC (externally synced feeds), so comparisons account for both.
- All-day events accept exactly one reminder, and it must be minute-based
  (`create_event` uses a 9 AM popup).
- Deleting a whole recurring series needs the series-root `eventId`
  (`eventRecurrenceRule.eventRecurrenceRulesId` / `routineSourceEventId` on a row).
- Notes need `ECALENDAR_INSTANCE`; events, tasks, and meals don't.
- **A new sign-in anywhere replaces the token.** The old one starts failing with
  `code: 401` "Account logged in on another device" (inside an HTTP 200). `ecal.py`
  raises `TokenReplacedError`; refresh `ECALENDAR_TOKEN` (see `SETUP.md`).
- Recipe names must be unique **account-wide** — see [Meals](#meals-recipes--meal-plan)
  above.
- `mealPlan/list` **silently omits any meal category disabled in the app's Meals
  settings** — `code: 200`, just no rows for it, no error. If entries you know exist
  aren't showing up, check that setting before assuming the write failed.

## Project layout

| File | What it is |
|---|---|
| `SKILL.md` | The skill definition — tells Claude when to use eCalendar and how to resolve dates, pick a category, dedupe, and confirm. Copy into `.claude/skills/ecalendar/` in your own repo. |
| `ecal.py` | The client. No dependencies beyond the standard library. |
| `ecalendar-api.md` | Full private-API reference behind `ecal.py`. |
| `extract_ecal_config.py` | Run on a Mac signed into eCalendar — prints your `ECALENDAR_*` values with no proxy needed. |
| `SETUP.md` | Full setup walkthrough: the Mac-script path, the iOS-only proxy path, and where each value goes. |

## Security

The token is a password-equivalent bearer credential with full read/write access to
every event, note, task, and meal on the account.

- Keep it in environment variables or a secret store — **never** in a committed file.
- Use a **private** repo for your own copy.
- If you capture it with an HTTPS proxy during setup, remove the proxy configuration
  from the device again once you're done, and delete the capture — if the app signed
  in while recording, it contains your account **password** in plain text.
- If the token leaks, change your eCalendar password and sign in again; the new
  sign-in invalidates the old token.

## Contributing

This is a living template — PRs are welcome for new endpoints, additional device
support, or setup fixes. When adding a new `ecal.py` function, please include the
real request/response JSON in its docstring (see any existing function for the
pattern) so the next person doesn't have to re-capture it from scratch.
