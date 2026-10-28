#!/usr/bin/env python3
"""Doorbell ring -> 10 s camera popup on the Dragon Touch eCalendar.

Polls HA (read-only GET /api/states/<entity>) every 1 s. When the event entity's
state (ISO timestamp of last ring) changes, sends an explicit broadcast to com.bakerk7.doorbelloverlay/.RingReceiver (full-screen
overlay window over the calendar, no Activity);
falls back to launching the kiosk Activity if that fails. Also pops up on binary_sensor.doorbell_motion off->on (no cooldown; skipped only while an overlay is showing).
Disable all: touch ~/.doorbell-popup/DISABLED; motion only: touch ~/.doorbell-popup/MOTION_DISABLED
"""
import json, os, threading, subprocess, time, urllib.request, logging

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
COOLDOWN = 12                      # ring de-dupe
MOTION_ENTITY = os.environ.get("MOTION_ENTITY", "binary_sensor.doorbell_motion")  # follows Kyle's Protect motion zones/settings
MOTION_COOLDOWN = 0                # Kyle: no cooldown; only skip if an overlay is already showing
MOTION_DISABLED = os.path.join(STATE_DIR, "MOTION_DISABLED")

def token():
    for line in open(os.path.join(HOME, "docker-stacks/ecalendar-assistant/.env")):
        if line.startswith("HA_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"\'')
    raise SystemExit("HA_TOKEN missing")

def ha_state(tok, entity=ENTITY):
    req = urllib.request.Request(f"{HA}/api/states/{entity}", headers={"Authorization": "Bearer " + tok})
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

_busy = threading.Event()   # set while a popup launch is in progress

def popup_async():
    """Launch in a thread so HA polling (and logging of skipped triggers) continues."""
    def run():
        try: popup()
        finally: _busy.clear()
    _busy.set(); threading.Thread(target=run, daemon=True).start()

def overlay_showing():
    if _busy.is_set():
        return True
    try:
        w = adb("shell", "dumpsys window windows | grep -c DoorbellOverlay", timeout=8)
        return w.stdout.strip() not in ("", "0")
    except Exception:
        return False

def main():
    tok, last, last_fire = token(), None, 0.0
    m_last, m_fire = None, 0.0
    logging.info("listener start ring=%s motion=%s device=%s", ENTITY, MOTION_ENTITY, DEV)
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
                elif overlay_showing():
                    logging.info("ring %s: overlay already showing, not re-triggered", s); last_fire = time.time()
                else:
                    logging.info("RING %s", s); last_fire = time.time(); popup_async()
            else:
                last = s if s not in ("unavailable", "unknown") else last
        except Exception as e:
            logging.warning("HA poll error: %s", e); time.sleep(3)
        try:  # motion: off -> on only
            m = ha_state(tok, MOTION_ENTITY)
            if m_last == "off" and m == "on":
                if os.path.exists(os.path.join(STATE_DIR, "DISABLED")) or os.path.exists(MOTION_DISABLED):
                    logging.info("MOTION ignored (disabled)")
                elif time.time() - m_fire < MOTION_COOLDOWN:
                    logging.info("MOTION ignored (cooldown, %.0fs since last motion pop-up)", time.time() - m_fire)
                elif overlay_showing():
                    logging.info("MOTION ignored (overlay already showing)")
                else:
                    logging.info("MOTION popup"); m_fire = time.time(); popup_async()
            if m in ("on", "off"):
                m_last = m
        except Exception as e:
            logging.warning("HA motion poll error: %s", e)
        time.sleep(1.0)

if __name__ == "__main__":
    main()
