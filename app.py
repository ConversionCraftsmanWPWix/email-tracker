import csv, os, base64, urllib.parse, threading, requests, random
from datetime import datetime
from flask import Flask, request, make_response, send_file
from io import BytesIO
from dotenv import load_dotenv

load_dotenv()

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
NOTIFY_TO = os.getenv("NOTIFY_TO")
NOTIFY_FROM = os.getenv("NOTIFY_FROM", "tracker@example.com")
CSV_PATH = os.getenv("CSV_PATH", "opens.csv")

# Create log file if missing
try:
    if not os.path.exists(CSV_PATH):
        with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
            f.write("time_utc,track_id,subject_b64,subject,recipient,ip,user_agent\n")
except Exception as e:
    print(f"⚠️ Could not create log file {CSV_PATH}: {e}")

app = Flask(__name__)

# 1x1 transparent PNG
PIXEL = bytes.fromhex(
    "89504E470D0A1A0A0000000D49484452000000010000000108060000"
    "1F15C4890000000A49444154789C636000000200010005FE02FEA7B108B9"
    "0000000049454E44AE426082"
)

# Track sent times (to filter bots)
sent_log = {}
IGNORE_AGENTS = [
    "GoogleImageProxy",
    "GoogleImageProxyFetcher",
    "GoogleImageProxyService",
    "Thunderhead",
    "favicon",
]

# ---------- SEND ALERT VIA RESEND ----------
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
            "Content-Type": "application/json",
        }
        data = {
            "from": NOTIFY_FROM,
            "to": [NOTIFY_TO],
            "subject": f"Read Alert: {subj_decoded or 'No Subject'}",
            "text": body,
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
    threading.Thread(
        target=send_alert_email, args=(track_id, subj_decoded, rcpt, ua, ip), daemon=True
    ).start()


# ---------- LOGGING ----------
def log_open(row):
    header = ["time_utc", "track_id", "subject_b64", "subject", "recipient", "ip", "user_agent"]
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
        track_id = request.args.get("id", "")
        subj_b64 = request.args.get("s", "")
        rcpt = request.args.get("to", "")
        _ = request.args.get("r", "")  # random Gmail-bypass parameter

        subj_decoded = ""
        if subj_b64:
            try:
                subj_decoded = base64.urlsafe_b64decode(subj_b64 + "==").decode("utf-8", errors="ignore")
            except Exception:
                subj_decoded = ""

        ua = request.headers.get("User-Agent", "")
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)

        # Ignore Gmail image proxy bots
        if any(b in ua for b in IGNORE_AGENTS) or "ggpht.com" in ua.lower():
            print(f"⚠️ Ignored automated fetch from {ua}")
            resp = make_response(send_file(BytesIO(PIXEL), mimetype="image/png"))
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
            return resp

        # Ignore too-early requests
        last_sent = sent_log.get(track_id)
        if last_sent:
            seconds_since = (datetime.utcnow() - last_sent).total_seconds()
            if seconds_since < 10:
                print(f"⚠️ Ignored open too soon ({seconds_since:.1f}s) for {track_id}")
                resp = make_response(send_file(BytesIO(PIXEL), mimetype="image/png"))
                resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
                resp.headers["Pragma"] = "no-cache"
                return resp

        # Log the open
        log_open([
            datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            track_id,
            subj_b64,
            subj_decoded,
            urllib.parse.unquote(rcpt),
            ip,
            ua,
        ])

        # Send email alert
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


# ---------- MARK EMAIL AS SENT ----------
@app.route("/sent")
def mark_sent():
    try:
        track_id = request.args.get("id", "")
        sent_log[track_id] = datetime.utcnow()
        print(f"🕒 Marked {track_id} as sent at {sent_log[track_id]}")
        return "ok", 200
    except Exception as e:
        print(f"⚠️ Error in /sent route: {e}")
        return "error", 500


# ---------- ROOT ROUTE ----------
@app.route("/")
def ok():
    return "Tracker up and running!"


# ---------- MAIN ----------
if __name__ == "__main__":
    PORT = int(os.getenv("PORT", "5000"))
    print(f"🚀 Tracker starting on port {PORT} ...")
    app.run(host="0.0.0.0", port=PORT, debug=False)
