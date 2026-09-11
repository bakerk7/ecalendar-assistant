#!/usr/bin/env python3
"""Extract every eCalendar value the skill needs, straight from the macOS app's own
stored preferences. Run this on a Mac that is signed into the eCalendar app.
No proxy, no packet capture.

Prints the environment variables ready to paste into a Claude Code project's
environment settings. The token is a full-account credential -- treat this output
like a password.

iOS-only (no Mac signed into eCalendar)? This script can't help -- use the
proxy-capture route in SETUP.md instead.
"""
import plistlib, json, os, sys

PLIST = os.path.expanduser(
    "~/Library/Containers/com.fujia.ecalendar/Data/Library/Preferences/"
    "com.fujia.ecalendar.plist")

if not os.path.exists(PLIST):
    sys.exit("eCalendar macOS app preferences not found.\n"
             "Open the eCalendar app, sign in, view your calendar once, then re-run.")

p = plistlib.load(open(PLIST, "rb"))

try:
    token = json.loads(p["flutter.user"])["token"]
except (KeyError, ValueError):
    sys.exit("Found the app but no saved login -- open eCalendar and sign in, then re-run.")

device = p.get("flutter.deviceMemory")
instance = p.get("flutter.notesSyncClientInstanceId", "")

raw = p.get(f"flutter.allEventCategories_{device}", "[]")
cats = json.loads(raw) if isinstance(raw, str) else raw

# Keep only the categories you can actually write events into: the app's own
# profiles (Family, each person). Anything with a syncCalenderId is an external
# feed (Google / iCloud / TeamSnap / a team schedule) and is read-only via this API.
writable = [(c["categoryName"], c["userCalendarCategoryId"])
            for c in cats if not c.get("syncCalenderId")]

def slug(name):
    return "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")

mapping = {slug(n): i for n, i in writable}
default = next((k for k in mapping if k in ("family", "home")), next(iter(mapping), None))

print("# ---- paste into your Claude Code project's Environment variables ----")
print("# the token is password-equivalent -- never commit it anywhere\n")
print(f"ECALENDAR_TOKEN={token}")
print(f"ECALENDAR_DEVICE={device}")
print(f"ECALENDAR_INSTANCE={instance}")
print(f"ECALENDAR_CATEGORIES={json.dumps(mapping, separators=(',', ':'))}")
if default:
    print(f"ECALENDAR_DEFAULT_CATEGORY={default}")

print("\n# category keys -> what they are in the app (use as category= when adding events):")
for n, i in writable:
    print(f"#   {slug(n):20} = {n}")
if not writable:
    print("#   (none found -- open the app and make sure your calendar profiles exist)")
