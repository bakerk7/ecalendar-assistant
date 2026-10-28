#!/usr/bin/env python3
"""Doorbell ring -> 10 s camera popup on the Dragon Touch eCalendar.

Polls HA (read-only GET /api/states/<entity>) every 1 s. When the event entity's
state (ISO timestamp of last ring) changes, sends an explicit broadcast to com.bakerk7.doorbelloverlay/.RingReceiver (full-screen
overlay window over the calendar, no Activity);
falls back to launching the kiosk Activity if that fails. Disable: touch ~/.doorbell-popup/DISABLED
"""
import json, os, subprocess, time, urllib.request, logging

HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(HOME, ".doorbell-popup")
os.makedirs(STATE_DIR, exist_ok=True)
logging.basicConfig(filename=os.path.join(STATE_DIR, "listener.log"), level=logging.INFO,
                    format="%(asctime)s %(message)s")
ADB = os.path.join(HOME, "platform-tools/adb")
DEV = os.environ.get("ECAL_DEVICE", "192.0.2.20:5555")
ENTITY = os.environ.get("DOORBELL_ENTITY", "event.doorbell_doorbell")
HA = os.environ.get("HA_URL", "http://192.0.2.10:8123")
COMPONENT = "com.bakerk7.familydashboard/.MainActivity"          # fallback (launches an Activity)
OVERLAY = "com.bakerk7.doorbelloverlay/.RingReceiver"              # primary: overlay window, no Activity
COOLDOWN = 12

def token():
    for line in open(os.path.join(HOME, "docker-stacks/ecalendar-assistant/.env")):
        if line.startswith("HA_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"\'')
    raise SystemExit("HA_TOKEN missing")

def ha_state(tok):
    req = urllib.request.Request(f"{HA}/api/states/{ENTITY}", headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r).get("state")

def adb(*args, timeout=15):
    return subprocess.run([ADB, "-s", DEV, *args], capture_output=True, text=True, timeout=timeout)

def popup():
    for attempt in range(3):
        try:
            if attempt:  # fast path: device is normally already connected
                subprocess.run([ADB, "connect", DEV], capture_output=True, timeout=10)
            r = adb("shell", "am", "broadcast", "-f", "0x20", "-n", OVERLAY)
            if r.returncode == 0 and "Broadcast completed" in r.stdout:
                logging.info("overlay broadcast sent (attempt %d)", attempt + 1)
                for _ in range(6):  # confirm the overlay window exists, else fall back
                    time.sleep(0.5)
                    w = adb("shell", "dumpsys window windows | grep -c DoorbellOverlay", timeout=8)
                    if w.stdout.strip() not in ("", "0"):
                        logging.info("overlay window confirmed"); return
                logging.warning("overlay window not seen")
            else:
                logging.warning("overlay broadcast failed: %s %s", r.stdout.strip(), r.stderr.strip())
            r = adb("shell", "am", "start", "-n", COMPONENT)
            if r.returncode == 0 and "Error" not in r.stdout + r.stderr:
                logging.info("FALLBACK activity popup launched (attempt %d)", attempt + 1); return
            logging.warning("am start failed: %s %s", r.stdout.strip(), r.stderr.strip())
        except Exception as e:
            logging.warning("adb error: %s", e)
        subprocess.run([ADB, "disconnect", DEV], capture_output=True, timeout=10)
        time.sleep(1)
    logging.error("popup failed after retries")

def main():
    tok, last, last_fire = token(), None, 0.0
    logging.info("listener start entity=%s device=%s", ENTITY, DEV)
    while True:
        try:
            s = ha_state(tok)
            if last is None:
                last = s; logging.info("baseline state %s", s)
            elif s != last and s not in ("unavailable", "unknown"):
                last = s
                if os.path.exists(os.path.join(STATE_DIR, "DISABLED")):
                    logging.info("ring %s ignored (DISABLED)", s)
                elif time.time() - last_fire < COOLDOWN:
                    logging.info("ring %s ignored (cooldown)", s)
                else:
                    logging.info("RING %s", s); last_fire = time.time(); popup()
            else:
                last = s if s not in ("unavailable", "unknown") else last
        except Exception as e:
            logging.warning("HA poll error: %s", e); time.sleep(3)
        time.sleep(1.0)

if __name__ == "__main__":
    main()
