import csv, os, smtplib, base64, urllib.parse, threading
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

# ---------- EMAIL SENDING (background thread to avoid Render timeout) ----------
def send_alert_email(track_id, subj_decoded, rcpt, ua, ip):
    try:
        if not (SMTP_HOST and SMTP_USER and SMTP_PASS and NOTIFY_TO):
            print("⚠️ Missing email settings — skipping alert.")
            return

        # 👇 Debug line added here
        print(f"Connecting to {SMTP_HOST}:{SMTP_PORT} as {SMTP_USER} ...")

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
        msg["To"] = NOTIF
