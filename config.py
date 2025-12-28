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


def _get_env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}

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

    # --- 2-Phase Screener / Leader Gate ---
    # Phase-1 builds technical features for (almost) the whole universe.
    # Phase-2 fetches expensive fundamentals only for the Leader subset.
    leader_min_total_score: int = _get_env_int("LEADER_MIN_TOTAL_SCORE", 5)
    leader_min_rs: int = _get_env_int("LEADER_MIN_RS", 70)
    leader_require_vol_breakout: bool = _get_env_bool("LEADER_REQUIRE_VOL_BREAKOUT", True)

SETTINGS = Settings()
