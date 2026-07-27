"""Ergebnis-Register für Kalibrierungs-Läufe (VCP-Sweeps, Forward-Tests).

Hintergrund: Die Analyse-Läufe sind teuer (voller Sweep ≈ 1 h, Forward-Test
≈ 2,5 h) und ihre Ergebnisse existierten bisher nur als Konsolenausgabe. Nach
dem nächsten Lauf war die vorige Zahl weg — Vergleiche über Sessions hinweg
waren nur aus dem Gedächtnis möglich, was am 2026-07-27 mehrfach zu falschen
Schlüssen geführt hat (Stichprobengrößen wurden verwechselt).

Dieses Modul schreibt jeden Lauf als eine Zeile nach `experiments/results.jsonl`:
Parameter, Metriken, Git-Stand und Datenumfang. Append-only und eine Zeile pro
Lauf, damit Git-Diffs lesbar bleiben und parallele Läufe nicht kollidieren.

Verwendung im Analyse-Tool
--------------------------
    from experiment_log import log_experiment
    log_experiment(
        tool="vcp_forward_test",
        params={"max_final_range": 0.12, ...},
        metrics={"alpha_med_8w": 1.4, "n_8w": 41, ...},
        context={"universe": 2644, "weeks_back": 78},
    )

Auswertung
----------
  python experiment_log.py --list                 # letzte Läufe
  python experiment_log.py --list --tool vcp_sweep
  python experiment_log.py --compare max_final_range alpha_med_8w
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_DIR = Path(__file__).resolve().parent / "experiments"
LOG_PATH = LOG_DIR / "results.jsonl"


def _git_state() -> dict[str, Any]:
    """Commit und Dirty-Flag — ohne die kann ein Ergebnis später keinem
    Codestand zugeordnet werden, und genau das macht alte Zahlen wertlos.
    """
    def _run(args: list[str]) -> str | None:
        try:
            out = subprocess.run(args, capture_output=True, text=True,
                                 timeout=10, cwd=Path(__file__).resolve().parent)
            return out.stdout.strip() if out.returncode == 0 else None
        except Exception:
            return None

    commit = _run(["git", "rev-parse", "--short", "HEAD"])
    status = _run(["git", "status", "--porcelain"])
    return {
        "git_commit": commit,
        # Nur verfolgte Änderungen zählen; unversionierte Caches/.env sind egal
        "git_dirty": bool(status and any(
            not line.startswith("??") for line in status.splitlines())),
    }


def log_experiment(tool: str, params: dict[str, Any], metrics: dict[str, Any],
                   context: dict[str, Any] | None = None,
                   note: str | None = None, path: Path | None = None) -> str:
    """Hängt einen Lauf ans Register an. Rückgabe: run_id.

    Wirft nicht — ein kaputtes Register darf einen 2-Stunden-Lauf nicht um sein
    Ergebnis bringen. Fehler werden gemeldet und der Lauf läuft weiter.
    """
    path = path or LOG_PATH
    record = {
        "run_id": uuid.uuid4().hex[:12],
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": tool,
        "host": socket.gethostname(),
        **_git_state(),
        "params": params,
        "context": context or {},
        "metrics": metrics,
    }
    if note:
        record["note"] = note

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as e:                       # pragma: no cover
        print(f"[EXPERIMENT-LOG] Konnte nicht schreiben: {e}", file=sys.stderr)
    return record["run_id"]


def load_experiments(path: Path | None = None, tool: str | None = None):
    """Register als DataFrame; `params`/`metrics`/`context` werden mit Präfix
    flachgezogen, damit man direkt filtern und sortieren kann.
    """
    import pandas as pd

    path = path or LOG_PATH
    if not path.exists():
        return pd.DataFrame()

    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue           # eine kaputte Zeile kippt nicht das Register
            flat = {k: v for k, v in rec.items()
                    if k not in ("params", "metrics", "context")}
            for prefix, key in (("p_", "params"), ("m_", "metrics"), ("c_", "context")):
                for k, v in (rec.get(key) or {}).items():
                    flat[f"{prefix}{k}"] = v
            rows.append(flat)

    df = pd.DataFrame(rows)
    if tool and not df.empty:
        df = df[df["tool"] == tool]
    return df


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="Läufe auflisten")
    ap.add_argument("--tool", type=str, default=None, help="nach Tool filtern")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--compare", nargs="+", metavar="SPALTE",
                    help="ausgewählte Spalten gegenüberstellen, z. B. "
                         "p_max_final_range m_alpha_med_8w")
    args = ap.parse_args()

    import pandas as pd
    pd.set_option("display.width", 200)

    df = load_experiments(tool=args.tool)
    if df.empty:
        print(f"Register leer oder nicht vorhanden: {LOG_PATH}")
        return 0

    if args.compare:
        missing = [c for c in args.compare if c not in df.columns]
        if missing:
            print(f"Unbekannte Spalten: {', '.join(missing)}")
            print(f"Verfügbar: {', '.join(sorted(df.columns))}")
            return 1
        print(df[["ts", "tool"] + args.compare].to_string(index=False))
        return 0

    cols = [c for c in ("ts", "tool", "git_commit", "git_dirty", "run_id")
            if c in df.columns]
    print(df.sort_values("ts", ascending=False).head(args.limit)[cols]
          .to_string(index=False))
    print(f"\n{len(df)} Läufe in {LOG_PATH}")
    print("Spalten (p_=Parameter, m_=Metriken, c_=Kontext):")
    print("  " + ", ".join(sorted(c for c in df.columns
                                  if c.startswith(("p_", "m_", "c_")))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
