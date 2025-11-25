import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate

from config import SETTINGS


def send_email(html_body: str, subject_suffix: str = ""):
    msg = MIMEText(html_body, 'html')
    subj = SETTINGS.mail_subject_prefix + (f" – {subject_suffix}" if subject_suffix else "")
    msg['Subject'] = subj
    msg['From'] = SETTINGS.mail_from
    msg['To'] = SETTINGS.mail_to
    msg['Date'] = formatdate(localtime=True)

    with smtplib.SMTP(SETTINGS.smtp_host, SETTINGS.smtp_port) as s:
        s.starttls()
        if SETTINGS.smtp_user and SETTINGS.smtp_pass:
            s.login(SETTINGS.smtp_user, SETTINGS.smtp_pass)
        s.sendmail(SETTINGS.mail_from, SETTINGS.mail_to.split(","), msg.as_string())



def send_email(html_body: str,
               subject: str = "Weekly US Market Report",
               attachments: list[str] | None = None):
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = YOUR_FROM_ADDRESS
    msg["To"] = YOUR_TO_ADDRESS

    # HTML
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Anhänge
    for path in attachments or []:
        with open(path, "rb") as f:
            # Excel (XLSX) – korrekter MIME Subtype
            part = MIMEBase("application",
                            "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition",
                        f'attachment; filename="{os.path.basename(path)}"')
        msg.attach(part)

    # SMTP-Versand wie gehabt …
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as s:
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
