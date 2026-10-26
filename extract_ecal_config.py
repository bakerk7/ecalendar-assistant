#!/usr/bin/env python3
"""Extract every eCalendar value the skill needs, straight from the macOS app's own
stored preferences. Run this on a Mac that is signed into the eCalendar app.
No proxy, no packet capture.

Prints the environment variables ready to paste into a Claude Code project's
environment settings. The token is a full-account credential -- treat this output
like a password.

    python3 extract_ecal_config.py --write ./ECALENDAR_INFO.bin

writes the same text to that path with mode 0600 (and does not print the token).
`ecal.load_auth_file()` reads that file. It is gitignored; never commit it.

iOS-only (no Mac signed into eCalendar)? This script can't help -- use the
proxy-capture route in SETUP.md instead.
"""
import argparse
import json
import os
import plistlib
import stat
import sys

PLIST = os.path.expanduser(
    "~/Library/Containers/com.fujia.ecalendar/Data/Library/Preferences/"
    "com.fujia.ecalendar.plist")


def slug(name):
    return "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")


def build_lines(plist):
    """Env-file lines (no trailing commentary about missing categories) from a plist dict."""
    try:
        token = json.loads(plist["flutter.user"])["token"]
    except (KeyError, TypeError, ValueError):
        raise SystemExit(
            "Found the app but no saved login -- open eCalendar and sign in, then re-run."
        )

    device = plist.get("flutter.deviceMemory")
    instance = plist.get("flutter.notesSyncClientInstanceId", "")

    raw = plist.get(f"flutter.allEventCategories_{device}", "[]")
    cats = json.loads(raw) if isinstance(raw, str) else raw

    # Keep only the categories you can actually write events into: the app's own
    # profiles (Family, each person). Anything with a syncCalenderId is an external
    # feed (Google / iCloud / TeamSnap / a team schedule) and is read-only via this API.
    writable = [(c["categoryName"], c["userCalendarCategoryId"])
                for c in cats if not c.get("syncCalenderId")]

    mapping = {slug(n): i for n, i in writable}
    default = next((k for k in mapping if k in ("family", "home")), next(iter(mapping), None))

    lines = [
        "# ---- paste into your Claude Code project's Environment variables ----",
        "# the token is password-equivalent -- never commit it anywhere",
        "",
        f"ECALENDAR_TOKEN={token}",
        f"ECALENDAR_DEVICE={device}",
        f"ECALENDAR_INSTANCE={instance}",
        f"ECALENDAR_CATEGORIES={json.dumps(mapping, separators=(',', ':'))}",
    ]
    if default:
        lines.append(f"ECALENDAR_DEFAULT_CATEGORY={default}")
    lines.append("")
    lines.append("# category keys -> what they are in the app (use as category= when adding events):")
    if writable:
        for n, _i in writable:
            lines.append(f"#   {slug(n):20} = {n}")
    else:
        lines.append("#   (none found -- open the app and make sure your calendar profiles exist)")
    lines.append("")
    return "\n".join(lines)


def write_private(path, text):
    """Create or replace `path` with mode 0600. Returns the absolute path."""
    path = os.path.expanduser(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    # O_CREAT honors umask, and an existing file keeps its old mode. Force 0600.
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--write", metavar="PATH",
        help="write the env lines to PATH with mode 0600 instead of printing the token",
    )
    parser.add_argument(
        "--plist", default=PLIST,
        help=argparse.SUPPRESS,  # tests pass a fixture; not part of the public help
    )
    args = parser.parse_args(argv)

    if not os.path.exists(args.plist):
        sys.exit("eCalendar macOS app preferences not found.\n"
                 "Open the eCalendar app, sign in, view your calendar once, then re-run.")

    with open(args.plist, "rb") as fh:
        plist = plistlib.load(fh)
    text = build_lines(plist)
    if args.write:
        dest = write_private(args.write, text)
        print(f"wrote {dest} (mode 0600). Do not commit this file.", file=sys.stderr)
    else:
        print(text, end="" if text.endswith("\n") else "\n")


if __name__ == "__main__":
    main()
