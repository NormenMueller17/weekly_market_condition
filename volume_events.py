"""volume_events.py — Handelstage mit mechanischem Sondervolumen.

Anlass (2026-09-19): Der Wochenreport meldete so viele Kaufkandidaten wie nie, obwohl der
Markt seitwaerts bis schwaecher lief. Das Kriterium `Vol-Breakout` vergleicht das
WOCHENMITTEL des Tagesvolumens mit dem 20-Tage-Schnitt. Am Freitag, 2026-09-18
(Quartalsverfall mit Indexanpassungen) lag das Freitagsvolumen bei 1,89x des Mo-Do-Schnitts
(normale Freitage 0,8-0,9x); das hebt das Wochenmittel um rund 18 % und schob die
Ausbruchsquote von 15 % auf 39 % (ueblich 6-9 %). Ohne diesen Tag blieben von den
Titeln mit Score >= 6 und Volumenausbruch 1,8 % statt 11,8 % uebrig. Dasselbe Muster
zeigte sich am 2026-06-26 (Russell-Anpassung, 1,81x): 12 Signale statt ueblicher 0-4.

Optionsverfall und Indexfonds-Umschichtung sind keine Nachfrage. Diese Tage werden
deshalb aus dem Wochenmittel UND aus dem 20-Tage-Schnitt herausgerechnet.

Erfasst sind
  * Quartalsverfall: 3. Freitag im Maerz, Juni, September, Dezember (faellt der
    Freitag auf einen Boersenfeiertag, gilt der letzte Handelstag davor -- 2026-06-19 war
    Juneteenth, der Verfall lag am Donnerstag 06-18). Er faellt mit der quartalsweisen
    S&P-Anpassung und der Nasdaq-100-Neugewichtung im Dezember zusammen.
  * Russell-Anpassung im Juni: letzter Freitag des Monats.
Monatsverfall zeigt keinen Effekt (2026-08-21: 0,92x) und bleibt drin.

Weitere Tage lassen sich in rules.json unter `filters.volume_event_days_extra` als
"YYYY-MM-DD" nachtragen (z. B. eine spaetere Russell-Anpassung im Herbst, deren Termin
hier nicht hartkodiert ist, weil er nicht belegt ist).
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable

import numpy as np
import pandas as pd

QUARTAL_MONATE = (3, 6, 9, 12)
RUSSELL_MONAT = 6


def _fridays(year: int, month: int) -> list[dt.date]:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(4 - d.weekday()) % 7)      # erster Freitag
    out = []
    while d.month == month:
        out.append(d)
        d += dt.timedelta(days=7)
    return out


def nominal_days(year: int) -> list[dt.date]:
    """Kalendertermine des Jahres, unabhaengig davon, ob an dem Tag gehandelt wurde."""
    tage = []
    for m in QUARTAL_MONATE:
        tage.append(_fridays(year, m)[2])               # 3. Freitag
    tage.append(_fridays(year, RUSSELL_MONAT)[-1])      # letzter Freitag im Juni
    return sorted(set(tage))


def event_mask(index: pd.Index, extra: Iterable[str] = ()) -> np.ndarray:
    """Boolesches Array passend zu `index`: True an Tagen mit Sondervolumen.

    Faellt ein Termin nicht auf einen Handelstag der Serie (Feiertag), zaehlt der
    letzte Handelstag davor, hoechstens drei Kalendertage zurueck. Termine ausserhalb
    der Serie bleiben unberuecksichtigt.
    """
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    tage = idx.normalize()
    mask = np.zeros(len(idx), dtype=bool)
    if len(idx) == 0:
        return mask

    kandidaten: set[dt.date] = set()
    for y in range(idx.min().year, idx.max().year + 1):
        kandidaten.update(nominal_days(y))
    for s in extra or ():
        try:
            kandidaten.add(dt.date.fromisoformat(str(s)))
        except ValueError:
            continue

    vorhanden = {d.date(): i for i, d in enumerate(tage)}
    sortiert = sorted(vorhanden)
    for nominal in kandidaten:
        if nominal in vorhanden:
            mask[vorhanden[nominal]] = True
            continue
        davor = [d for d in sortiert if d < nominal and (nominal - d).days <= 3]
        if davor and nominal < sortiert[-1]:            # nur, wenn die Serie den Termin ueberspannt
            mask[vorhanden[davor[-1]]] = True
    return mask


def ohne_ereignistage(serie: pd.Series, extra: Iterable[str] = ()) -> tuple[pd.Series, np.ndarray]:
    """(Serie ohne Ereignistage, Maske ueber die Originalserie)."""
    m = event_mask(serie.index, extra)
    return serie[~m], m
