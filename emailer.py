import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.utils import formatdate
from email import encoders
from config import SETTINGS


def send_email(html_body: str, subject_suffix: str = "", attachments: list[str] | None = None):
    """
    Versendet eine HTML-Mail mit optionalen Anhängen (.xlsx, .csv, ...)
    unter Verwendung der SMTP-Parameter aus SETTINGS.
    """
    # Multipart-Container (Text + evtl. Attachments)
    msg = MIMEMultipart()
    subj = SETTINGS.mail_subject_prefix + (f" – {subject_suffix}" if subject_suffix else "")
    msg["Subject"] = subj
    msg["From"] = SETTINGS.mail_from
    msg["To"] = SETTINGS.mail_to
    msg["Date"] = formatdate(localtime=True)

    # HTML-Body anhängen
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Anhänge hinzufügen
    for path in attachments or []:
        if not os.path.exists(path):
            print(f"[WARN] Attachment not found: {path}")
            continue
        with open(path, "rb") as f:
            # generischer MIME-Typ für Binärdaten
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{os.path.basename(path)}"'
        )
        msg.attach(part)

    # Versand
    with smtplib.SMTP(SETTINGS.smtp_host, SETTINGS.smtp_port) as s:
        s.starttls()
        if SETTINGS.smtp_user and SETTINGS.smtp_pass:
            s.login(SETTINGS.smtp_user, SETTINGS.smtp_pass)
        s.sendmail(SETTINGS.mail_from, SETTINGS.mail_to.split(","), msg.as_string())
