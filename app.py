import csv, os, base64, urllib.parse, threading, requests
from datetime import datetime
from flask import Flask, request, make_response, send_file
from io import BytesIO
from dotenv import load_dotenv

load_dotenv()

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
NOTIFY_TO = os.getenv("NOTIFY_TO")
NOTIFY_FROM = os.getenv("NOTIFY_FROM", "tracker@example.com")
CSV_PATH = os.getenv("CSV_PATH", "opens.csv")

# Ensure CSV file exists
try:
    if not os.path.exists(CSV_PATH):
        with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
            f.write("time_utc,track_id,subject_b64,subject,recipient,ip,user_agent,cachebuster\n")
except Exception as e:
    print(f"⚠️ Could not create log file {CSV_PATH}: {e}")

app = Flask(__name__)

recent_opens = {}  # cache for 10-minute dedupe

# 1×1 transparent PNG
PIXEL = bytes.fromhex(
    "89504E470D0A1A0A0000000D49484452000000010000000108060000"
    "1F15C4890000000A49444154789C636000000200010005FE02FEA7B108B9"
    "0000000049454E44AE426082"
)

def pixel_response():
    resp = make_response(send_file(BytesIO(PIXEL), mimetype="image/png"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


# ---------- EMAIL ALERT USING RESEND ----------
def send_alert_email(track_id, subj_decoded, rcpt, ua, ip, cb):
    try:
        if not RESEND_API_KEY:
            print("⚠️ RESEND_API_KEY not set — skipping alert.")
            return

        body = (
            f"📬 Tracked email opened!\n\n"
            f"Track ID: {track_id or '(none)'}\n"
            f"Subject: {subj_decoded or '(no subject)'}\n"
            f"Recipient: {rcpt or '(unknown)'}\n"
            f"Cache-Buster: {cb}\n"
            f"IP: {ip}\n"
            f"User-Agent: {ua}\n"
            f"Opened at: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
        )

        headers = {
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "from": NOTIFY_FROM,
            "to": [NOTIFY_TO],
            "subject": f"Read Alert: {subj_decoded or 'No Subject'}",
            "text": body
        }

        print(f"📡 Sending alert via Resend to {NOTIFY_TO} …")
        r = requests.post("https://api.resend.com/emails", headers=headers, json=data)
        if r.status_code == 200:
            print(f"✅ Email alert sent for Track ID: {track_id}")
        else:
            print(f"⚠️ Resend API error {r.status_code}: {r.text}")

    except Exception as e:
        print(f"⚠️ Error sending alert via Resend: {e}")

def send_alert_in_background(track_id, subj_decoded, rcpt, ua, ip, cb):
    threading.Thread(
        target=send_alert_email,
        args=(track_id, subj_decoded, rcpt, ua, ip, cb),
        daemon=True
    ).start()


# ---------- LOGGING ----------
def log_open(row):
    header = ["time_utc","track_id","subject_b64","subject","recipient","ip","user_agent","cachebuster"]
    file_exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(header)
        w.writerow(row)


# ---------- PIXEL ROUTE ----------
@app.route("/px.png")
def pixel():
    try:
        track_id = request.args.get("id", "").strip()
        subj_b64 = request.args.get("s", "").strip()
        rcpt     = request.args.get("to", "").strip()
        cb       = request.args.get("cb", "").strip() or "none"

        subj_decoded = ""
        if subj_b64:
            try:
                subj_decoded = base64.urlsafe_b64decode(subj_b64 + "==").decode("utf-8", errors="ignore")
            except Exception:
                subj_decoded = ""

        ua = request.headers.get("User-Agent", "")
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        now = datetime.utcnow()

        # --- 1️⃣ Skip known proxy/bot user-agents ---
        bot_signatures = [
            "googleimageproxy", "outlook.office.com", "microsoft office",
            "appleimageproxy", "thunderbird", "protection.outlook.com",
            "mail.ru", "yahoo", "safe links"
        ]
        if any(sig in ua.lower() for sig in bot_signatures):
            print(f"🤖 Prefetch detected from {ua[:80]} → skipping alert.")
            return pixel_response()

        # --- 2️⃣ Delay filter: ignore hits within 90 seconds of Render wake-up or send time ---
        if (now - datetime.utcnow()).total_seconds() < 90:
            print("⏳ Skipping early proxy fetch (too soon after send).")
            return pixel_response()

        # --- 3️⃣ Duplicate control by TrackID + Cache-buster ---
        key = f"{track_id}:{cb}"
        if key in recent_opens:
            last_time = recent_opens[key]
            if (now - last_time).total_seconds() < 600:
                print(f"⏳ Skipping duplicate alert for Track ID: {track_id} (cb={cb})")
                return pixel_response()
        recent_opens[key] = now

        # --- Log the open ---
        try:
            log_open([
                now.strftime('%Y-%m-%d %H:%M:%S'),
                track_id, subj_b64, subj_decoded,
                urllib.parse.unquote(rcpt), ip, ua, cb
            ])
        except Exception as e:
            print(f"⚠️ Logging failed: {e}")

        # --- Trigger background alert ---
        try:
            send_alert_in_background(track_id, subj_decoded, urllib.parse.unquote(rcpt), ua, ip, cb)
        except Exception as e:
            print(f"⚠️ Background alert failed: {e}")

        return pixel_response()

    except Exception as e:
        print(f"❌ Error in /px.png route: {e}")
        return pixel_response()


@app.route("/")
def ok():
    return "Tracker up and running! (Ping this URL every 5 min via UptimeRobot to keep awake)"


if __name__ == "__main__":
    PORT = int(os.getenv("PORT", "5000"))
    print(f"🚀 Tracker starting on port {PORT} …")
    app.run(host="0.0.0.0", port=PORT, debug=False)
