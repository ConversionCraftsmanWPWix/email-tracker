import csv, os, base64, urllib.parse, threading, requests, time
from datetime import datetime
from flask import Flask, request, make_response, send_file
from io import BytesIO
from dotenv import load_dotenv

load_dotenv()

# ===== CONFIG =====
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
NOTIFY_TO = os.getenv("NOTIFY_TO")
NOTIFY_FROM = os.getenv("NOTIFY_FROM", "tracker@example.com")
CSV_PATH = os.getenv("CSV_PATH", "opens.csv")

# Keep track of when each email was sent
# (In a real system you might persist this in a DB)
SEND_TIMES = {}

# Ensure CSV exists
try:
    if not os.path.exists(CSV_PATH):
        with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
            f.write("time_utc,track_id,subject_b64,subject,recipient,ip,user_agent\n")
except Exception as e:
    print(f"⚠️ Could not create log file {CSV_PATH}: {e}")

app = Flask(__name__)

# ===== 1×1 transparent PNG =====
PIXEL = bytes.fromhex(
    "89504E470D0A1A0A0000000D49484452000000010000000108060000"
    "1F15C4890000000A49444154789C636000000200010005FE02FEA7B108B9"
    "0000000049454E44AE426082"
)

# ===== ALERT VIA RESEND =====
def send_alert_email(track_id, subj_decoded, rcpt, ua, ip):
    try:
        if not RESEND_API_KEY:
            print("⚠️ RESEND_API_KEY not set — skipping alert.")
            return

        body = (
            f"📬 Tracked email opened!\n\n"
            f"Track ID: {track_id}\n"
            f"Subject: {subj_decoded}\n"
            f"Recipient: {rcpt}\n"
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

        print(f"📡 Sending alert via Resend to {NOTIFY_TO} ...")
        r = requests.post("https://api.resend.com/emails", headers=headers, json=data)
        if r.status_code == 200:
            print(f"✅ Email alert sent for Track ID: {track_id}")
        else:
            print(f"⚠️ Resend API error {r.status_code}: {r.text}")

    except Exception as e:
        print(f"⚠️ Error sending alert via Resend: {e}")

def send_alert_in_background(track_id, subj_decoded, rcpt, ua, ip):
    threading.Thread(target=send_alert_email,
                     args=(track_id, subj_decoded, rcpt, ua, ip),
                     daemon=True).start()

# ===== LOGGING =====
def log_open(row):
    header = ["time_utc","track_id","subject_b64","subject","recipient","ip","user_agent"]
    file_exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(header)
        w.writerow(row)

# ===== PIXEL ROUTE =====
@app.route("/px.png")
def pixel():
    try:
        track_id = request.args.get("id", "")
        subj_b64 = request.args.get("s", "")
        rcpt     = request.args.get("to", "")

        subj_decoded = ""
        if subj_b64:
            try:
                subj_decoded = base64.urlsafe_b64decode(subj_b64 + "==").decode("utf-8", errors="ignore")
            except Exception:
                subj_decoded = ""

        ua = request.headers.get("User-Agent", "")
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)

        # --- Ignore known bot or proxy requests ---
        ua_lower = (ua or "").lower()
        if any(bot in ua_lower for bot in [
            "googleimageproxy",
            "google",
            "outlook",
            "microsoft",
            "yahoo",
            "applemail",
            "proxy",
            "fastmail",
        ]):
            print(f"⚠️ Ignored automated fetch from {ua}")
            resp = make_response(send_file(BytesIO(PIXEL), mimetype="image/png"))
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
            return resp

        # --- Timing safeguard: ensure 10 seconds since "sent" ---
        now = time.time()
        last_sent = SEND_TIMES.get(track_id, now - 999)
        if now - last_sent < 10:
            print(f"⚠️ Ignored open within 10s for Track ID: {track_id}")
            resp = make_response(send_file(BytesIO(PIXEL), mimetype="image/png"))
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
            return resp

        # --- Log real opens ---
        log_open([
            datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
            track_id,
            subj_b64,
            subj_decoded,
            urllib.parse.unquote(rcpt),
            ip,
            ua
        ])

        send_alert_in_background(track_id, subj_decoded, urllib.parse.unquote(rcpt), ua, ip)

        resp = make_response(send_file(BytesIO(PIXEL), mimetype="image/png"))
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        return resp

    except Exception as e:
        print(f"❌ Error in /px.png route: {e}")
        resp = make_response(send_file(BytesIO(PIXEL), mimetype="image/png"))
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        return resp

# ===== RECORD SEND TIME (OPTIONAL) =====
@app.route("/sent")
def mark_sent():
    """Optional route to record when an email was sent (for timing filter)"""
    track_id = request.args.get("id", "")
    SEND_TIMES[track_id] = time.time()
    print(f"🕒 Marked email {track_id} as sent at {datetime.utcnow()}")
    return {"status": "ok", "track_id": track_id}

# ===== ROOT =====
@app.route("/")
def ok():
    return "Tracker up and running!"

# ===== MAIN =====
if __name__ == "__main__":
    PORT = int(os.getenv("PORT", "5000"))
    print(f"🚀 Tracker starting on port {PORT} ...")
    app.run(host="0.0.0.0", port=PORT, debug=False)
