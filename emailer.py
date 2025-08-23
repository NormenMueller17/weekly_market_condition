import smtplib
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
