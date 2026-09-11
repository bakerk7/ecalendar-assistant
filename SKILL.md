---
name: ecalendar
description: >-
  Add events to (or check) the family's eCalendar app from a photo, screenshot, or a
  spoken/typed description — school flyers, party invitations, appointment cards, game
  schedules, "put X on the calendar", "am I free Thursday". Also creates eCalendar Notes.
  eCalendar (com.fujia.ecalendar) is the shared calendar the family uses.
---

# eCalendar

eCalendar is a shared family calendar app (macOS + iOS, vendor "com.fujia.ecalendar").
Everyone on the account sees whatever is added. This skill talks to its private API
(`api.cd.myecalendar.com`) through the tested helper **`ecal.py`** in this folder.

Use it when someone wants to **add a calendar event** from a picture or a description,
**check what's on the calendar**, **add a Note**, or **add to Meals** (a recurring menu
like a school breakfast/lunch calendar, or a meal plan).

> This is the generic template copy of the skill (see `../SETUP.md` in this folder for
> how it got here). Before using it for real, finish setup and edit the **"Which
> calendar category"** section below to name your own family members / category keys —
> everything else works as-is.

## Setup / auth

`ecal.py` needs several things, all read from environment variables (never hardcoded,
never committed to a repo): `ECALENDAR_TOKEN`, `ECALENDAR_DEVICE`, `ECALENDAR_INSTANCE`,
`ECALENDAR_CATEGORIES` are required; `ECALENDAR_TIMEZONE` (defaults to Eastern — check
this is actually right for the account) and `ECALENDAR_MEAL_CATEGORIES` (only if Meals
is used) are optional. See `SETUP.md` for how to find your own values.

Quick check: `python3 ecal.py` prints the token source, the current UTC offset for the
configured timezone, and today's events. If setup is incomplete it'll tell you which
variable is missing.

## Workflow for "add this to the calendar"

1. **Extract** the event from whatever was given (see parsing notes below). Produce:
   title, date, start time (or all-day), end time, location, which family member,
   and any reminder.
2. **Resolve** ambiguity:
   - No year given → assume the next occurrence of that month/day (this year unless
     it's already past, then next year).
   - No time given, or wording like "day", "all day", "due", a deadline, picture day,
     a holiday → **all-day event**.
   - Time but no end → default **1 hour** (`create_event` does this automatically).
   - Vague date ("sometime next week", "the fall concert") → **ask**, don't guess.
   - Can't read a date/time confidently off a photo → **ask**, show what you did read.
3. **Confirm before writing** *only if* something was inferred or is fuzzy. If the
   request was explicit ("add dentist Tuesday March 3 at 2pm"), just create it and
   report what you added.
4. **Dedupe**: call `ecal.find_duplicate(title, date)`. If it returns a row, don't
   create — say it's already there.
5. **Create** with `ecal.create_event(...)`.
6. **Report** back: title, resolved date + time (spell out the weekday), category,
   reminder. The API returns no event id, so confirm by reading it back if it matters.

## Workflow for "add this to Meals"

Meals is a **separate feature** from events — a recipe (the food item) attached to a
date under a meal category (Breakfast/Lunch/Dinner/Snack). Use it for a school
breakfast/lunch calendar, a meal-prep plan, or "put spaghetti on the menu Thursday" —
not `create_event`.

1. **Extract** each meal: date, meal category (breakfast/lunch/dinner/snack — infer
   from a menu's own headers, e.g. "Elementary School Lunch"), the main dish name, and
   any sides/description. For a flyer with multiple weeks/months, do the whole thing in
   one pass rather than asking for each day separately.
2. **Confirm the parsed list before writing anything** if it came from a photo/scan —
   OCR misreads happen, and this writes to a shared family calendar. Show the
   date → dish table and let the user catch errors first, *especially* for a multi-week
   import (many entries, all visible to everyone on the account).
3. **Create with `ecal.add_meal(name, date, description=..., category=...)`** — never
   call the recipe/mealPlan API directly. `add_meal` looks up an existing recipe by
   exact name and reuses it instead of erroring, which matters a lot here: a recurring
   menu repeats the same dish (Pizza, Pancakes, ...) across different weeks, and recipe
   names must be unique **account-wide** — the API rejects a second recipe with the
   same name even under a different category, or against a name that already exists on
   the account (the app seeds some common ones).
4. **Report back**: how many entries created, the date range, which category. If any
   name collided with an existing recipe (routine for a recurring menu), that's
   expected, not an error to flag.

**If a meal category's entries don't show up when listing** (`ecal.list_meals`
returns fewer rows than expected, or zero for a category you know has entries): that
category is very likely toggled off in the app's Meals settings, not a real bug. Ask
the user to check there before assuming something's broken — the API gives no error
for this, it just silently omits the category.

## Parsing notes

**Photos / screenshots** (flyers, invites, appointment cards, a text message, a school
newsletter, a sports schedule): read the image directly. Pull the event name, date,
time, location. Watch for: multiple dates on one flyer (make one event each, or ask),
"rain date", start vs. arrival time, AM/PM, and a year that's only implied by context.
For a birthday party the title should name the kid and it's a party ("Nora's 6th
birthday party"). For sports, include team + home/away if shown.

**Voice / typed** ("add lunch with Sarah next Thursday at noon", "dentist appt on the
14th at 3:30", "put the fall festival on the calendar Oct 18"): resolve relative dates
against **today** (check `date` / the system date). "next Thursday" = the Thursday of
next week, not the coming one, unless context says otherwise.

## Which calendar category

<!-- EDIT THIS SECTION after setup: replace with your own ECALENDAR_CATEGORIES keys
     and the people/situations each one is for. Example: -->

`ecal.create_event(..., category=...)` accepts a key from `ECALENDAR_CATEGORIES`
(e.g. `"family"`, `"kid_a"`, `"kid_b"`) or a raw category id. If `category` is omitted,
it uses `ECALENDAR_DEFAULT_CATEGORY`.

- A kid is clearly the subject (their appointment, their game, their class trip) →
  that kid's category.
- Something the whole family needs → the shared/family category.
- Unsure → the shared/family category.

## Reminders

`reminders_min` is minutes-before for a timed event: `(0,)` = at start time (default),
`(30,)` = 30 min before, `(1440,)` = 1 day before, `()` = none. All-day events always
get a 9:00 AM popup that morning (the API only allows one, minute-based).

Default: a timed event gets `(0,)` unless asked otherwise or it's the kind of thing
you'd want lead time for (leave-the-house appointments → offer `(30,)`).

## Which meal category

<!-- EDIT THIS SECTION after setup: replace with your own ECALENDAR_MEAL_CATEGORIES
     keys, if you're using Meals. Example: -->

`ecal.add_meal(..., category=...)` accepts a key from `ECALENDAR_MEAL_CATEGORIES`
(e.g. `"breakfast"`, `"lunch"`, `"dinner"`) or a raw meal category id. Infer the
category from context — a school lunch menu's entries are `"lunch"`, its breakfast
page is `"breakfast"`, etc. Ask if it's genuinely ambiguous.

## `ecal.py` reference

```python
import ecal

# --- events ---
# timed event (local wall time)
ecal.create_event("Dentist", "2026-03-14 15:30:00",
                  category="kid_a", location="Some Dental Office", reminders_min=(30,))

# all-day / deadline event
ecal.create_event("Picture Day", "2026-10-03", all_day=True,
                  category="kid_a", description="wear the green shirt")

# recurring event  (ecal.DAILY / WEEKLY / MONTHLY / YEARLY; week_days 0=Sun..6=Sat)
ecal.create_event("Piano lesson", "2026-09-15 16:00:00", category="kid_b",
                  recur=ecal.recurrence(ecal.WEEKLY, week_days=[1], end="2026-12-15"))

# read the calendar  (rows: eventId, title, startDatetime, endDatetime, isAllDay,
#                     isRecurring, routineSourceEventId, ...). Range auto-widened.
ecal.events_on("2026-09-11")
ecal.list_events("2026-09-07", "2026-09-13")
ecal.find_duplicate("Picture Day", "2026-10-03")     # -> row or None (dedupe before create)

# edit  (all fields, like create; needs the eventId)
ecal.edit_event(event_id, "Dentist (moved)", "2026-03-14 16:00:00", category="kid_a")
#   recurring instance: also pass update_method (0 this / 1 all / 2 this+future) +
#   origin_event_id / origin_start / origin_end from the row.

ecal.delete_event(event_id)                          # one event / instance
ecal.delete_event(series_root_id, series=True)       # whole recurring series

# anniversary  (all-day, yearly, shows in the Anniv tab)
ecal.create_anniversary("Our anniversary", "2020-10-22", category="family", pinned=True)

# --- tasks / chores  (Tasks tab, assigned to a person, optional star rewards) ---
ecal.create_task("Wash dishes", category="kid_a", stars=2, emoji="BOWL WITH SPOON")
ecal.create_task("Math homework", category="kid_a", emoji="MEMO")  # stars default to 0
ecal.create_task("Feed the dog", category="kid_b", emoji="DOG FACE",
                 recur=ecal.recurrence(ecal.DAILY))            # repeating chore
ecal.list_tasks("2026-09-11", categories=["kid_a", "kid_b"])   # -> rows
ecal.delete_task(task_event_id)                                # series=True if recurring

# --- notes ---  (need ECALENDAR_INSTANCE set)
ecal.create_note("Title", "plain body\nsecond line",
                 image_paths=["/path/to/flyer.jpg"])   # images optional, max 8
ecal.update_note(server_note_id, content="revised body")
ecal.delete_note(server_note_id)                        # or archive_only=True
```

Full endpoint spec (recurrence units, task model, every request body): `ecalendar-api.md`.

## API quirks (already handled by `ecal.py`, FYI)

- `/app/event/list` **rejects any range narrower than 7 days** (HTTP 200 body
  `code:500`). `list_events` fetches a widened window and filters locally.
- `/app/event/add` returns `{"code":200,"data":null}` — **no event id**. Dedupe via
  `find_duplicate` / `events_on`.
- Event times: `create_event` sends local wall time + a `zone` offset (auto-picked per
  date). Times read back from `list_events` may be local (app-created) or UTC
  (externally-synced calendars), so `find_duplicate` matches on title within ±1 day
  rather than on exact time.
- All-day events accept exactly one reminder, `timeUnit` must be `minute`
  (`create_event` uses a 9 AM popup).
- Whole-series delete uses the series-root `eventId`
  (`eventRecurrenceRule.eventRecurrenceRulesId` / `routineSourceEventId` on a row).
- Notes (create/update/delete/attach) need `ECALENDAR_INSTANCE`; events/tasks don't.
- **Task vs event:** a **task/chore** (`create_task`) is a to-do assigned to a person,
  in the Tasks tab — "give kid_a a chore to…". A dated **deadline or appointment** with
  no assignee is an **event** (`create_event`). `create_task` needs a non-empty `emoji`.
- **Task stars default to 0.** Only pass `stars=` when the user asks for a reward
  ("2 stars"). When you report tasks you created without stars, say they have **no
  stars** and that the user can ask for them. Things like homework shouldn't earn stars.
