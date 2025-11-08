import csv, os, smtplib, base64, urllib.parse
from datetime import datetime
from email.mime.text import MIMEText
from flask import Flask, request, make_response, send_file
from io import BytesIO
from dotenv import load_dotenv

load_dotenv()

NOTIFY_TO = os.getenv("NOTIFY_TO")
SMTP_HOST  = os.getenv("SMTP_HOST")
SMTP_PORT  = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER  = os.getenv("SMTP_USER")
SMTP_PASS  = os.getenv("SMTP_PASS")
CSV_PATH   = os.getenv("CSV_PATH", "opens.csv")
# Ensure CSV file exists safely (for free Render plan)
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

def send_alert_email(track_id, subj_decoded, rcpt, ua, ip):
    try:
        if not (SMTP_HOST and SMTP_USER and SMTP_PASS and NOTIFY_TO):
            print("⚠️ Missing email settings — skipping alert.")
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
        msg = MIMEText(body)
        msg["Subject"] = f"Read Alert: {subj_decoded or 'No Subject'}"
        msg["From"] = SMTP_USER
        msg["To"] = NOTIFY_TO

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, [NOTIFY_TO], msg.as_string())

        print(f"✅ Email alert sent for Track ID: {track_id}")

    except Exception as e:
        print(f"⚠️ Error sending alert email: {e}")

def log_open(row):
    header = ["time_utc","track_id","subject_b64","subject","recipient","ip","user_agent"]
    file_exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(header)
        w.writerow(row)

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

        # Log to CSV
        log_open([
            datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
            track_id,
            subj_b64,
            subj_decoded,
            urllib.parse.unquote(rcpt),
            ip,
            ua
        ])

        # Try to send alert email
        send_alert_email(track_id, subj_decoded, urllib.parse.unquote(rcpt), ua, ip)

        # Return pixel image (no caching)
        resp = make_response(send_file(BytesIO(PIXEL), mimetype="image/png"))
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        return resp

    except Exception as e:
        print(f"❌ Error in /px.png route: {e}")
        return f"Internal Server Error: {e}", 500

@app.route("/")
def ok():
    return "Tracker up and running!"

if __name__ == "__main__":
    print("🚀 Tracker starting on port 5000 ...")
    app.run(host="0.0.0.0", port=5000, debug=False)
