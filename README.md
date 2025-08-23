# Weekly US Market Report

Automatisierter Wochenreport (S&P 500 Breadth + Index-Momentum + Risiko/Sentiment) mit Versand per E-Mail.

## Setup

1. Repo forken/klonen und GitHub Secrets setzen (Repository → Settings → Secrets and variables → Actions):
   - `MAIL_FROM` – Absenderadresse
   - `MAIL_TO` – Empfängerliste, kommasepariert
   - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` – z. B. SendGrid SMTP (User=apikey, Pass=API Key)
   - Optional: `UNIVERSE` (sp500|custom), `CUSTOM_TICKERS`, `LOOKBACK_WEEKS`

2. Requirements installieren (lokal): `pip install -r requirements.txt`

3. Lokal testen: `python -m src.main`

## Output

- E-Mail mit HTML-Bericht inkl. WoW-Vergleichen und Ampel-Fazit.

## Hinweise

- Put/Call Ratio (CPC), VIX, 10Y (TNX), USD-Proxy (UUP) werden via yfinance geladen.
- Prozent > 50/200 Wochen-MA und 52W High/Low werden auf Basis der S&P500-Unternehmen berechnet.
