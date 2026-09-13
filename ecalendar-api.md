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
- **Token:** JWT, no time-based expiry (`/app/user/login` returns `expireTime: -1`), but
  **any new sign-in to the account, on any device, issues a new token and kills the old
  one** — old-token calls then return `{"code":401,"msg":"Account logged in on another
  device"}`. Full-account bearer credential (read+write every note / event / task /
  reward on the account). `ECALENDAR_TOKEN`, or read from the macOS app
  plist `~/Library/Containers/com.fujia.ecalendar/Data/Library/Preferences/com.fujia.ecalendar.plist`
  → `plistlib.load` → key `flutter.user` (a JSON string) → `.token`.
- **Per-account IDs:** `<DEVICE_ID>` = `flutter.deviceMemory` in the plist;
  `<CLIENT_INSTANCE_ID>` = `flutter.notesSyncClientInstanceId` (needed only for Notes).
- **Headers on every call:**
  ```
  user-agent: Dart/3.9 (dart:io)
  key: <token>
  authorization: Bearer <token>
  x-time-zone: America/Chicago       # the device's IANA zone -- use your account's
  x-zone-id: America/Chicago
  x-client-capabilities: routine-v1
  resource: app
  x-language: en
  versionname: 571
  content-type: application/json
  ```
- **Responses:** writes and most lists are POST; lookups like `/app/user/mine/info` and
  `/app/family/list` are GET. Body is `{"code":200|500|401, "msg":…, "data":…}` (some
  list endpoints also put `total`/`rows` at top level), with HTTP status 200 even on
  errors. May be gzip-encoded — handle `Content-Encoding: gzip`.
  `code:500` + a `msg` = validation error (auth was fine). `code:401` = token no longer
  valid (usually replaced by a newer sign-in); the app also sends `authorization:
  Bearer null` before login and gets the same 401.

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
`startDatetime`/`endDatetime` are stored as **UTC**; the app converts to the account
zone for display. Convert local → UTC before create/edit, compare read-back times
in UTC, and use `dateDescription` on the row as the "what the app shows" check
(sending local wall time makes events show hours early — verified 2026-09-13).

### `POST /app/event/add` — create
```json
{
  "deviceId": "<DEVICE_ID>",
  "title": "…", "description": "",
  "eventType": 0,
  "isAllDay": 0,                          // 1 = all-day
  "startDatetime": "2026-09-10 14:00:00", // UTC — convert from local first
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
  "emoji": "WASTEBASKET",              // emoji name, or null for no icon (the app sends null); "" is rejected
  "starCount": "2",                    // reward stars; "0" = no reward (accepted on add and edit)
  "priority": "0",
  "timerDurationSeconds": 123,        // optional focus-timer
  "taskTimeoutPenalty": 0, "taskTimeoutPenaltyStarPercent": "0.00", "taskTimeoutPenaltyStarCount": 0,
  "isRecurring": 0, "eventRecurrenceRule": null,
  "timeReminders": [], "reminderPipelineVersion": 1
}
```
→ `{data:{createdCount:1, eventIds:[…]}}`.

A task created this way is a **chore** (`taskMode: 0` on read). The app's own "add chore"
request (captured 2026-09-13) is the body above with `"emoji": null`, `"starCount": 0` as
a number, and no `timeReminders` changes. The server stores `23:59:59` as `23:59:00`.

### Routines  (`taskMode: 1`)

A routine is a repeating task pinned to one or more **times of day**. It uses the same
`POST /app/task`, but with no `isAllDay` / `startDatetime` / `timeReminders`:

```json
{
  "deviceId": "<DEVICE_ID>", "title": "…", "description": "",
  "eventType": "2", "taskType": 0, "taskMode": 1,
  "routinePeriods": [1, 3],            // one entry per time-of-day slot, see table
  "routineStartDate": "2026-09-13",    // local date the routine starts
  "isRecurring": 1,
  "eventRecurrenceRule": {"recurrenceUnit": 1, "recurrenceValue": "1", "weekDays": null,
                          "recurrenceMonthOption": null, "eventRecurrenceRulesId": 0},
  "userCalendarCategoryIds": ["<CATEGORY_ID>"], "zone": -5,
  "emoji": null, "starCount": 0, "priority": "0", "timerDurationSeconds": null,
  "taskTimeoutPenalty": 0, "taskTimeoutPenaltyStarPercent": "0.00", "taskTimeoutPenaltyStarCount": 0
}
```
→ `{data:{createdCount:2, eventIds:["<id for period 1>","<id for period 3>"]}}`: **one
independent recurring series per period**, each its own series root.

| `routinePeriod` | window (local) | seen |
|---|---|---|
| 1 | 00:00 – 12:00 (morning) | captured |
| 2 | 12:00 – 18:00 (afternoon), presumably | not captured |
| 3 | 18:00 – 24:00 (evening) | captured |

On `/app/task/list` each routine instance comes back as a row with `taskMode: 1`,
`routinePeriod`, `routineSourceEventId` (= its own series `eventId`),
`routineInstanceDate`, `isAllDay: 0`, `startDatetime`/`endDatetime` set to the period
window, `eventRecurrenceRule.recurrenceRuleDescription: "Daily"`,
`deleteMethodSet: [1,2]`, and `updateMethodSet: [1]`. A one-off chore has
`deleteMethodSet: null`. Editing and deleting routines weren't captured.
`ecal.create_routine()` sends this body.

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
(`deleteMethod:2` for a whole recurring chore). Also `/app/task/complete`.

**Weekly counts** — `POST /app/task/statistics`:
```json
{"deviceId":"<DEVICE_ID>","userCalendarCategoryIds":["<CATEGORY_ID>",…],
 "startDatetime":"2026-09-13 05:00:00","endDatetime":"2026-09-20 04:59:59",   // week, UTC
 "currentDatetime":"<UTC now>","dateTime":"<today 23:59:59 in UTC>",
 "filterOverdueMiscellaneous":0,"language":"en","zone":-5}
```
→ `{data:{zoneId:"America/Chicago", taskStatisticsList:[{date:"2026-09-13",taskTotal:5},…],
miscIconList:[…]}}`: the number of tasks per day, which is what the week strip shows.

**Templates / title autocomplete** — `POST /app/event/selectTaskTemplateList`
`{"pageNum":1,"pageSize":10}` → `{total:52, rows:[{taskTemplateId,categoryName:"Housework",
title:"Wash dishes",starCount:"2",rrule:"FREQ=DAILY",emoji:"BOWL WITH SPOON"},…]}`. The
app sends this again with `"title":"<what's typed so far>"` as you type a task name,
to suggest matching templates. It's also a good source of valid `emoji` names.

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
| Account | `GET /app/user/mine/info` → `{userId,userName,email,plusType,isSubscribe,…}` (`ecal.whoami`); `GET /app/family/list` → families + paired devices (`ecal.family`); `GET /app/user/summary/virtual-device-detail?deviceId=` |
| Home summary | `POST /app/user/summary/data` `{activeTaskCategoryIds,activeEventCategoryIds,deviceId,language,appCurrentTime(UTC),zone,use12HourFormat,handleCrossDay}` → today's event/task counts (`ecal.summary`) |
| Category stats | `POST /app/user/event/category/list/v2` `{deviceId,pageSize,filterOverdueMiscellaneous,zone,appCurrentTime(UTC),dateTime(end of day, UTC)}` → `{total,completed,categories[{…,starCount,completedMiscellaneousCount,totalMiscellaneousCount}]}` (`ecal.category_stats`) |
| Login | `POST /app/user/login` `{email,password}` → `{token,expireTime:-1,…}`. **A proxy capture of this request contains the account password in plain text** — delete the capture |
| Consent | `GET /app/consent/types`, `POST /app/consent/record` `{consentType}` |
| Feature flags | `GET /app/module/switch` → `{greetingCardSwitch,rewardSwitch,anniversaryModuleSwitch,magicImportSwitch,fileUploadModuleSwitch,…}` (0/1) |
| Membership | `GET /app/user/membershipPackage/current` (`{packageId:null}` = free), `/listV2`. Plus unlocks AI Dialogue, Magic Import, more paired devices |
| Realtime | `wss://im.myecalendar.com/ws?id=…` |
| AI | `ai.myecalendar.com/ai/chat/*` (paid, points-based). `GET /ai/chat/quota` (extra `x-device-id` header) → `{tier,remaining,aiPoints{canUseAi,…}}` |

~150 further `/app/…` endpoints exist in the app binary
(`.../eCalendar*.app/…/Frameworks/App.framework/App`) — names only, no shapes.

---

## Quirks / gotchas

- A new sign-in anywhere invalidates the previous token (`code:401` "Account logged in
  on another device", HTTP 200).
- `event/list` 7-day-minimum range.
- `event/add` returns no id — always dedupe before create.
- All-day reminders: single, `minute` unit only.
- Tasks reject an empty `emoji` string, but `null` is accepted (the app sends it when no icon is picked). (`starCount:"0"` is fine — verified 2026-09-10.)
- A routine with N `routinePeriods` creates N separate recurring series, so N `eventIds`.
- Deleting a routine: `/app/task/delete` rejects the ids the routine-create call
  returns ("eventId is invalid") — resolve the series id from `list_tasks` (the row
  whose `eventId == routineSourceEventId`) and delete with `series=True`.
- The Board menu item is not API-writable (`/app/note/boards` rejects requests) —
  use regular Notes with image attachments for display content instead.
- `deviceId` is a JSON **number** in `note/sync/push`, a **string** almost everywhere else.
- Event datetimes are stored as UTC; the app displays them in the account zone
  (convert local → UTC before create/edit).
- `zone` must match the target date's actual UTC offset (DST), not today's.
- mitmproxy intercepting breaks a few SDK flows (the `im.myecalendar.com` websocket), and
  the app can show "bad response" / "no network" until fully quit + relaunched after
  proxy teardown.
