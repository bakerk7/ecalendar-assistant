# eCalendar private API — reference

`api.cd.myecalendar.com` — the API the eCalendar app (`com.fujia.ecalendar`, macOS/iOS,
vendor "Fujia") talks to. Reverse-engineered from mitmproxy captures of the real app.
Not a public/documented API — no stability guarantees. `ecal.py` in this repo wraps the
common paths; this doc is the spec behind it and covers the rest.

Placeholders below (`<DEVICE_ID>`, `<CATEGORY_ID>`, …) are the per-account values you
supply via the `ECALENDAR_*` environment variables — see `SETUP.md`.

---

## Auth

- **Base:** `https://api.cd.myecalendar.com`
- **Token:** JWT, **no expiry**. Full-account bearer credential (read+write every note /
  event / task / reward on the account). `ECALENDAR_TOKEN`, or read from the macOS app
  plist `~/Library/Containers/com.fujia.ecalendar/Data/Library/Preferences/com.fujia.ecalendar.plist`
  → `plistlib.load` → key `flutter.user` (a JSON string) → `.token`.
- **Per-account IDs:** `<DEVICE_ID>` = `flutter.deviceMemory` in the plist;
  `<CLIENT_INSTANCE_ID>` = `flutter.notesSyncClientInstanceId` (needed only for Notes).
- **Headers on every call:**
  ```
  user-agent: Dart/3.9 (dart:io)
  key: <token>
  authorization: Bearer <token>
  x-time-zone: America/New_York      # the app hardcodes this; change if your account is elsewhere
  x-zone-id: America/New_York
  x-client-capabilities: routine-v1
  resource: app
  x-language: en
  versionname: 571
  content-type: application/json
  ```
- **Responses:** all POST, `{"code":200|500, "msg":…, "data":…}` (some list endpoints
  also put `total`/`rows` at top level). May be gzip-encoded — handle `Content-Encoding: gzip`.
  `code:500` + a `msg` = validation error (auth was fine). `401` = bad token.

## Calendar categories ("profiles")

`POST /app/user/event/category/list` `{"deviceId":"<DEVICE_ID>","pageSize":1000,"returnHiddenCategory":1}`
→ rows with `userCalendarCategoryId`, `categoryName`, `categoryType` (0 = shared, 1 = person),
`syncCalenderId` (non-null ⇒ an external read-only feed — Google/iCloud/team schedule).
Put the writable ones (no `syncCalenderId`) in `ECALENDAR_CATEGORIES`.

---

## Events

### `POST /app/event/list` — read
```json
{"deviceId":"<DEVICE_ID>","startDatetime":"YYYY-MM-DD 00:00:00","endDatetime":"YYYY-MM-DD 23:59:59"}
```
→ `{total, rows:[{eventId,title,eventType,isAllDay,startDatetime,endDatetime,isRecurring,
isAnniversary,routineSourceEventId,routineInstanceDate,eventRecurrenceRule,description,
locationInfo,…}]}`
**Rejects any range < 7 days** (200 body `code:500`). Fetch a wider window, filter client-side.
Times in rows are local for app-created events, **UTC** for externally-synced calendars —
don't match on exact time.

### `POST /app/event/add` — create
```json
{
  "deviceId": "<DEVICE_ID>",
  "title": "…", "description": "",
  "eventType": 0,
  "isAllDay": 0,                          // 1 = all-day
  "startDatetime": "2026-09-10 14:00:00", // local wall time (see zone)
  "endDatetime":   "2026-09-10 15:00:00", // all-day: "<date> 00:00:00" .. "<date> 23:59:59"
  "isRecurring": 0, "eventRecurrenceRule": null,
  "userCalendarCategoryIds": ["<CATEGORY_ID>"],
  "zone": -4,                             // target date's UTC offset (e.g. US-Eastern: -4 EDT / -5 EST)
  "apiVersion": "2.0",
  "isAnniversary": 0, "isAnniversaryPinned": 0,
  "reminderPipelineVersion": 1,
  "timeReminders": [{"method":"popup","timeUnit":"minute","value":0}],
  "locationInfo": null, "weatherInfo": null, "currentCity": null, "googleCalendarId": null,
  "addCalendarId": null                   // optional: a CalDAV calendarId to also write into
}
```
→ `{"code":200,"data":null}` — **no event id returned.** Dedupe via `/app/event/list`.

- **Reminders:** timed events → `timeUnit:"minute"`, `value` = minutes before start
  (`0` at start, `30`, `1440` = 1 day; `[]` = none). **All-day events accept exactly one
  reminder and `timeUnit` must be `minute`** (`day`/`hour` rejected); `540` = 9:00 AM that day.
- **Recurring:** `isRecurring:1` +
  ```json
  "eventRecurrenceRule": {
    "eventRecurrenceRulesId": null,
    "recurrenceValue": 1,              // interval: every N units
    "recurrenceUnit": 4,              // 1=daily 2=weekly 3=monthly 4=yearly
    "weekDays": [3,4],               // weekly only; ints 0=Sun..6=Sat (else null)
    "recurrenceMonthOption": null,    // monthly only (else null)
    "recurrenceEndDatetime": null    // "YYYY-MM-DD HH:MM:SS" or null = forever
  }
  ```
- **Anniversary:** `isAnniversary:1` (optionally `isAnniversaryPinned:1`); usually all-day
  + yearly recurrence. (`/app/anniversary/list` + `/app/anniversary/pin` are the only
  dedicated anniversary endpoints — creation is just an event.)

### `POST /app/event/edit/selective` — update
Same body as `event/add` **plus**:
```json
"eventId": "<id>",
"updateMethod": null,           // non-recurring: null. recurring instance: 0=this / 1=all / 2=this+future
"originEventId": null,          // recurring: the series' eventId
"originStartDatetime": null,    // recurring: the instance's original start
"originEndDatetime": null
```
→ `{"code":200,"data":{"eventId","resultEvents":[…]}}`.

### `POST /app/event/delete/selective` — delete
```json
{"eventId":"<id>","deleteMethod":0,"originEventId":null,"originStartDatetime":null,"originEndDatetime":null}
```
- non-recurring / single instance: `deleteMethod:0`
- whole series: `deleteMethod:2` with the series-root eventId (a row's
  `eventRecurrenceRule.eventRecurrenceRulesId` or `routineSourceEventId`)
- `deleteMethod:1` = this-and-following (from within a series, with the origin fields)

---

## Notes  (operational-transform sync)

### `POST /app/note/sync/full` — read all
`{"deviceId":"<DEVICE_ID>"}` → `{data:{serverChangeId,hasMore,notes:[…],deletedNoteIds,deletedNotes}}`
Note row: `noteId, localNoteId, title, content, contentRichHtml, userCreatedAt, userEditedAt,
isPinned, isArchived, archivedTime, wasPinnedBeforeArchive, isDeleted, version, sort,
attachments[], profileIds[]`.

### `POST /app/note/sync/push` — create / update / delete
```json
{
  "deviceId": <DEVICE_ID>,                       // number here, string elsewhere
  "clientInstanceId": "<CLIENT_INSTANCE_ID>",
  "baseChangeId": null,
  "operations": [{
    "clientOpId": "note:create:<localNoteId>:<epochMicros>",  // "note:update:…" / "note:delete:…"
    "opType": "UPSERT_NOTE",                                   // or "DELETE_NOTE"
    "localNoteId": "note-<epochMicros>",
    "serverNoteId": null,                                      // null = create; real id = update/delete
    "note": {
      "title": "…", "content": "plain text", "contentRichHtml": "<p>…</p>",
      "profileIds": [], "attachments": [ /* see below */ ],
      "isPinned": false, "pinnedTime": null,
      "isArchived": false, "archivedTime": null,
      "wasPinnedBeforeArchive": 0,
      "userCreatedAt": "2026-09-09T20:00:00.000Z",             // ISO-8601 UTC
      "userEditedAt":  "2026-09-09T20:00:00.000Z",
      "sort": 5000
    }
  }]
}
```
→ `data.results[0] = {serverNoteId, changeId, version, success:true, status:"SUCCESS"}`.
**Update:** same op with `serverNoteId` set + same `localNoteId`; server bumps `version`.

### Image attachment  (4 steps, before the note push)
1. `POST /app/note/attachment/presign` `{"deviceId":<DEVICE_ID>,"fileName":"x.jpg","contentType":"image/jpeg"}`
   → `{data:{remoteKey:"admin/notes/<DEVICE_ID>/<attachmentId>.jpg", uploadUrl:<presigned S3 PUT, 30 min>,
      remoteUrl:"https://<cdn-host>/<remoteKey>", method:"PUT"}}`
   `attachmentId` (int) = the number in the remoteKey filename.
2. `PUT <uploadUrl>` body = raw bytes, header `Content-Type:` **exactly** the presign contentType
   (presigned SignedHeaders = content-type;host).
3. `POST /app/note/attachment/confirm` `{"deviceId":<DEVICE_ID>,"remoteKey":…,"remoteUrl":…,
   "thumbnailUrl":<remoteUrl>,"fileName":…,"fileSize":<bytes>}`
4. note push `attachments` entry:
   `{"attachmentId":<int>,"remoteKey":…,"remoteUrl":…,"thumbnailUrl":…,"fileName":…,"fileSize":…,"sortOrder":0}`

Max 8 / note. App uploads webp (~170 KB); jpg/png also fine.

---

## Tasks  (`/app/task`) — the app's chore / star-reward model

```json
{
  "deviceId": "<DEVICE_ID>", "title": "…", "description": "",
  "eventType": "2", "taskType": 0,
  "isAllDay": 1, "startDatetime": "2026-09-11 23:59:59",
  "userCalendarCategoryIds": ["<CATEGORY_ID>"], "zone": -4,
  "emoji": "WASTEBASKET",              // a non-empty emoji name is required
  "starCount": "2",                    // reward stars; "0" = no reward (accepted on add and edit)
  "priority": "0",
  "timerDurationSeconds": 123,        // optional focus-timer
  "taskTimeoutPenalty": 0, "taskTimeoutPenaltyStarPercent": "0.00", "taskTimeoutPenaltyStarCount": 0,
  "isRecurring": 0, "eventRecurrenceRule": null,
  "timeReminders": [], "reminderPipelineVersion": 1
}
```
→ `{data:{createdCount:1, eventIds:[…]}}`.

**Read** — `POST /app/task/list`:
```json
{"deviceId":"<DEVICE_ID>","dateTime":"<day 23:59:59 in UTC>","zone":-4,
 "appCurrentTime":"<local now>","userCalendarCategoryIds":["<CATEGORY_ID>",…] | null,
 "isFrontHandleHidden":1,"language":"en","pageSize":1000,
 "filterOverdueMiscellaneous":0,"use12HourFormat":true}
```
→ `{data:{miscEventList:[{eventId,title,eventType:2,starCount,emoji,isAllDay,startDatetime,
isRecurring,eventRecurrenceRulesId,…}], anyTimeMiscEventList, miscIconList}}`.
`userCalendarCategoryIds: null` returns nothing — pass the category ids you care about.

**Edit** — `POST /app/task/edit`: the full add body (all fields, not just the changed
ones) plus `"eventId"`, `"updateMethod":0`, `"originEventId":null`,
`"originStartDatetime"` (from the list row), `"originEndDatetime":null` → `{code:200,
data:null}`. The eventId is kept. Verified for one-off tasks; recurring not tested.

**Delete** — `POST /app/task/delete` `{"deviceId":…,"eventId":…,"deleteMethod":0}`
(`deleteMethod:2` for a whole recurring chore). Also `/app/task/complete`,
`/app/task/statistics`.

**Task vs event:** a task/chore is a to-do assigned to a person, with an optional star
reward. For a plain dated **deadline** (no assignee) use an all-day event instead — it
shows on the calendar; a task shows only in the Tasks tab.

---

## Other endpoints (captured, lower priority)

| area | endpoints |
|---|---|
| Rewards | `/app/user/reward/add` `{title,isRepeatable,description,emoji,userCalendarCategoryIds,exchangeQuantity,rewardType}`, `/exchange` `{rewardId}`, `/initMember`, `/setSortMode`, `/setCanRetract`, `/detail`, `/category/list` |
| Category edit | `POST /app/user/event/category/edit` `{userCalendarCategoryId,deviceId,categoryName,categoryColor,categoryType,categoryAvatar,categoryBirthday,familyRole,allergens[]}` |
| Display settings | `POST /app/user/settings/userEventDisplaySettings` `{deviceId,startWeekOn,shadeWeekends,dimPastEvent,showAnniversary}` |
| Lists | `POST /app/user/list/list` (read), `/app/lists/items/` `/all` `/batch` `/reorder` `/today` `/scheduled` `/completed` `/statistics` (item bodies not captured) |
| Grocery | `/app/grocery/planItems` `/customItems` `/customItems/edit|delete|check` `/timeSetting` |
| Meals | `GET /app/mealCategory`, `POST /app/mealRecipe/list`, `POST /app/mealRecipe`, `POST /app/mealPlan`, `POST /app/mealPlan/list`, `POST /app/v2/recipe/groupedList` |
| Wall-device pairing | `POST /app/device/code/create` `{virtualDeviceId,deviceType,requestId}` → `deviceCode`; `/app/device/code/query` `/list` |
| Feature flags | `GET /app/module/switch` → `{greetingCardSwitch,rewardSwitch,anniversaryModuleSwitch,magicImportSwitch,fileUploadModuleSwitch,…}` (0/1) |
| Membership | `GET /app/user/membershipPackage/current` (`{packageId:null}` = free), `/listV2`. Plus unlocks AI Dialogue, Magic Import, more paired devices |
| Realtime | `wss://im.myecalendar.com/ws?id=…` |
| AI | `ai.myecalendar.com/ai/chat/*` (paid, points-based) |

~150 further `/app/…` endpoints exist in the app binary
(`.../eCalendar*.app/…/Frameworks/App.framework/App`) — names only, no shapes.

---

## Quirks / gotchas

- `event/list` 7-day-minimum range.
- `event/add` returns no id — always dedupe before create.
- All-day reminders: single, `minute` unit only.
- Tasks reject an empty `emoji`. (`starCount:"0"` is fine — verified 2026-09-10.)
- `deviceId` is a JSON **number** in `note/sync/push`, a **string** almost everywhere else.
- Externally-synced event rows carry UTC times; app-created ones carry local.
- `zone` must match the target date's actual UTC offset (DST), not today's.
- mitmproxy intercepting breaks a few SDK flows (the `im.myecalendar.com` websocket), and
  the app can show "bad response" / "no network" until fully quit + relaunched after
  proxy teardown.
