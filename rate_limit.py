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