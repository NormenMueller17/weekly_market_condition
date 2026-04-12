import os
from dataclasses import dataclass

def _get_env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if (v is not None and v != "") else default

def _get_env_int(name: str, default: int) -> int:
    try:
        v = os.getenv(name)
        return int(v) if (v is not None and v != "") else default
    except (TypeError, ValueError):
        return default

def _get_env_float(name: str, default: float) -> float:
    try:
        v = os.getenv(name)
        return float(v) if (v is not None and v != "") else default
    except (TypeError, ValueError):
        return default

def _get_env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes")

@dataclass
class Settings:
    universe: str = _get_env("UNIVERSE", "sp500")
    custom_tickers: str = _get_env("CUSTOM_TICKERS", "")

    mail_from: str = _get_env("MAIL_FROM", "report@example.com")
    mail_to: str = _get_env("MAIL_TO", "")
    mail_subject_prefix: str = _get_env("MAIL_SUBJECT_PREFIX", "Weekly US Market Report")

    smtp_host: str = _get_env("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = _get_env_int("SMTP_PORT", 587)
    smtp_user: str = _get_env("SMTP_USER", "")
    smtp_pass: str = _get_env("SMTP_PASS", "")

    lookback_weeks: int = _get_env_int("LOOKBACK_WEEKS", 60)
    cache_dir: str = _get_env("CACHE_DIR", ".cache")

    # ── Output options ────────────────────────────────────────────────────────
    # Set EXPORT_EXCEL=true to generate and attach the full Excel workbook.
    # Default is false: only the JSON signal file and HTML email are produced.
    export_excel: bool = _get_env_bool("EXPORT_EXCEL", False)

    # ── Trade signal / position-sizing parameters (Blueprint defaults) ────────
    account_equity:  float = _get_env_float("ACCOUNT_EQUITY",  100_000.0)
    win_rate:        float = _get_env_float("WIN_RATE",         0.59)
    win_loss_ratio:  float = _get_env_float("WIN_LOSS_RATIO",   4.04)
    kelly_fraction:  float = _get_env_float("KELLY_FRACTION",   0.33)

SETTINGS = Settings()
