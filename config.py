import os
from dataclasses import dataclass


@dataclass
class Settings:
# Report & Universe
universe: str = os.getenv("UNIVERSE", "sp500") # sp500|nasdaq100|custom
custom_tickers: str = os.getenv("CUSTOM_TICKERS", "")


# Email
mail_from: str = os.getenv("MAIL_FROM", "report@example.com")
mail_to: str = os.getenv("MAIL_TO", "") # comma-separated
mail_subject_prefix: str = os.getenv("MAIL_SUBJECT_PREFIX", "Weekly US Market Report")


# SMTP
smtp_host: str = os.getenv("SMTP_HOST", "smtp.sendgrid.net")
smtp_port: int = int(os.getenv("SMTP_PORT", 587))
smtp_user: str = os.getenv("SMTP_USER", "apikey")
smtp_pass: str = os.getenv("SMTP_PASS", "")


# Misc
lookback_weeks: int = int(os.getenv("LOOKBACK_WEEKS", 60))
cache_dir: str = os.getenv("CACHE_DIR", ".cache")


SETTINGS = Settings()
