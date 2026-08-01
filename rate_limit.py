import threading
import time
import random

class RateLimiter:
    """
    Simple token-bucket style limiter:
    - max_calls: allowed calls per period_seconds
    - shared across threads
    """
    def __init__(self, max_calls: int, period_seconds: float):
        self.max_calls = max_calls
        self.period = period_seconds
        self.lock = threading.Lock()
        self.calls = []  # timestamps of recent calls

    def acquire(self):
        while True:
            with self.lock:
                now = time.time()
                # drop old timestamps
                cutoff = now - self.period
                while self.calls and self.calls[0] < cutoff:
                    self.calls.pop(0)

                if len(self.calls) < self.max_calls:
                    self.calls.append(now)
                    return

                # need to wait: time until oldest call leaves the window
                wait_s = (self.calls[0] + self.period) - now

            # add jitter so threads don't synchronize
            time.sleep(max(0.05, wait_s) + random.uniform(0.05, 0.25))


def install_yfinance_limiter(limiter: "RateLimiter") -> bool:
    """Haengt `limiter` in yfinance ein, sodass er JEDEN HTTP-Request bremst.

    Bis hierher sass die Bremse an vier `acquire()`-Aufrufen in
    fetch_quote_data_single. Gemessen loest ein Ticker aber 9 HTTP-Requests
    aus — 2,25 je acquire. Der Limiter zaehlte damit das Falsche: bei
    6 acquire/s gingen real ~13 Requests/s raus. Ausserdem war
    load_weekly_history (3708 Titel) gar nicht gebremst, obwohl die
    Drosselung schon vor dem Fundamentaldaten-Batch bestand.

    yfinance leitet jeden Zugriff durch YfData._make_request. Ein Wrapper
    dort zaehlt Requests statt Methodenaufrufe und erfasst alle Aufrufer.

    Gibt False zurueck, wenn yfinance seine Datenschicht umgebaut hat — der
    Aufrufer MUSS das melden, sonst laeuft der Lauf still ungebremst.
    """
    try:
        import yfinance.data as yfdata

        ziel = yfdata.YfData
        if getattr(ziel, "_limiter_installiert", False):
            return True

        original = ziel._make_request

        def gebremst(self, *args, **kwargs):
            limiter.acquire()
            return original(self, *args, **kwargs)

        ziel._make_request = gebremst
        ziel._limiter_installiert = True
        return True
    except Exception:
        return False