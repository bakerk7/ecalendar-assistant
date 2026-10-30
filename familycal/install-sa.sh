#!/bin/bash
# Install the Google service-account key for the Family Calendar helper on your docker host (FAMCAL_HOST).
#   GOOGLE_SA_JSON = the key JSON itself (or a path to the .json file)
#   optional: SHARED_CAL_ID (default: discovered from the eCalendar import)
#             SOCCER_ICS    (default: ICS_URL from soccer_sync.py)
#             MIGRATE=1     also copy the 109 eCalendar-only events into the shared Google calendar
# Never prints the key. Prereq: in Google Calendar, share "Shared Calendar" with the service
# account's email -> "Make changes to events".
set -euo pipefail
HOST=${FAMCAL_HOST:?set FAMCAL_HOST=user@your-docker-host (reached with tailscale ssh)}
STACK='~/docker-stacks/familycal'
ssh_() { tailscale ssh "$HOST" "$@"; }

if [ -z "${GOOGLE_SA_JSON:-}" ] && [ -f /workspace/.ecal/google_sa.json ]; then GOOGLE_SA_JSON=/workspace/.ecal/google_sa.json; fi
[ -n "${GOOGLE_SA_JSON:-}" ] || { echo "GOOGLE_SA_JSON is not set"; exit 1; }
TMP=$(mktemp); chmod 600 "$TMP"; trap 'rm -f "$TMP"' EXIT
if [ -f "$GOOGLE_SA_JSON" ]; then cat "$GOOGLE_SA_JSON" > "$TMP"; else printf '%s' "$GOOGLE_SA_JSON" > "$TMP"; fi
SA_EMAIL=$(python3 - "$TMP" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit("GOOGLE_SA_JSON is not valid JSON")
if d.get("type") != "service_account" or not d.get("private_key") or not d.get("client_email"):
    sys.exit("GOOGLE_SA_JSON is not a service-account key (need type/private_key/client_email)")
print(d["client_email"])
PY
)
echo "Service account: $SA_EMAIL"

if [ -z "${SOCCER_ICS:-}" ] && [ -f /workspace/ecalendar-assistant/soccer_sync.py ]; then
  SOCCER_ICS=$(grep -oP '^ICS_URL = "\K[^"]+' /workspace/ecalendar-assistant/soccer_sync.py || true)
fi

echo "1/5 Updating helper code + settings"
tar cz -C "$(dirname "$0")/../ecalendar-assistant/familycal/server" famcal requirements.txt Dockerfile 2>/dev/null \
  | ssh_ "cd $STACK && tar xz" || echo "  (code copy skipped; using deployed code)"
ssh_ "cd $STACK && touch .env && chmod 600 .env && \
  sed -i '/^FAMCAL_SHARED_CAL_ID=/d;/^FAMCAL_SOCCER_ICS=/d' .env && \
  { [ -z '${SHARED_CAL_ID:-}' ] || echo 'FAMCAL_SHARED_CAL_ID=${SHARED_CAL_ID:-}' >> .env; } && \
  { [ -z '${SOCCER_ICS:-}' ] || echo 'FAMCAL_SOCCER_ICS=${SOCCER_ICS:-}' >> .env; }"

echo "2/5 Rebuilding/restarting the helper"
ssh_ "cd $STACK && docker compose up -d --build </dev/null >/dev/null 2>&1 && for i in \$(seq 30); do curl -sf localhost:8091/health >/dev/null && break; sleep 1; done"

echo "3/5 Copying the key (stdin -> /data/google_sa.json, mode 600, owned by the container user)"
ssh_ "docker exec -i familycal sh -c 'umask 077; cat > /data/google_sa.json.tmp && mv /data/google_sa.json.tmp /data/google_sa.json'" < "$TMP"

echo "4/5 Restarting in Google service-account mode + checks"
ssh_ "cd $STACK && docker compose restart </dev/null >/dev/null 2>&1; for i in \$(seq 30); do curl -sf localhost:8091/health >/dev/null && break; sleep 1; done"
set +e
ssh_ "docker exec familycal python -m famcal.google_setup ${MIGRATE:+--migrate}" </dev/null
RC=$?
set -e

echo "5/5 First sync"
ssh_ "cd $STACK && K=\$(grep -oP '(?<=FAMCAL_API_KEY=).*' .env) && curl -s -X POST -H \"X-API-Key: \$K\" localhost:8091/api/sync; echo; curl -s localhost:8091/health; echo" </dev/null
[ $RC -eq 0 ] && echo "Done." || echo "Checks reported a problem (code $RC) – see above. To undo: docker exec familycal rm /data/google_sa.json && docker compose restart"
exit $RC
