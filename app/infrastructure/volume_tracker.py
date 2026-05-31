"""
app/infrastructure/volume_tracker.py

Realtime volume tracker maintaining a thread-safe sliding 10-second window
of taker buy/sell volumes parsed from market trade feeds.
Syncs volume imbalance ratios to Redis for low-latency decision making.
"""

import time
import os
import threading
from collections import deque
import redis

class RealtimeVolumeTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._windows = {}  # {pair: deque((timestamp, volume, is_taker_buy))}
        self._totals = {}   # {pair: {"buy": 0.0, "sell": 0.0}}
        
        try:
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            if "redis://" not in redis_url:
                # Docker internal hostname resolution fallback
                redis_url = "redis://redis:6379/0"
            self.redis = redis.from_url(redis_url)
        except Exception:
            self.redis = None

    def add_trade(self, pair: str, vol: float, is_buyer_maker: bool):
        """Adds a taker trade event to the sliding window and updates Redis."""
        pair = pair.lower()
        now = time.time()
        # If buyer maker is True, it means taker hit the bid (Taker Sell).
        # If buyer maker is False, it means taker hit the ask (Taker Buy).
        is_taker_buy = not is_buyer_maker

        with self._lock:
            if pair not in self._windows:
                self._windows[pair] = deque()
                self._totals[pair] = {"buy": 0.0, "sell": 0.0}
            
            self._windows[pair].append((now, vol, is_taker_buy))
            if is_taker_buy:
                self._totals[pair]["buy"] += vol
            else:
                self._totals[pair]["sell"] += vol

            self._prune(pair, now)
            ratio = self._calculate_ratio(pair)
            
        # Sync to Redis cache outside the lock to prevent blocking I/O bottlenecks
        if self.redis:
            try:
                self.redis.set(f"imbalance:{pair}", str(ratio), ex=60)
            except Exception:
                pass

    def _prune(self, pair: str, now: float):
        """Removes trades older than 10 seconds from the deque."""
        window = self._windows[pair]
        totals = self._totals[pair]
        cutoff = now - 10.0
        while window and window[0][0] < cutoff:
            _, pop_vol, pop_buy = window.popleft()
            if pop_buy:
                totals["buy"] -= pop_vol
            else:
                totals["sell"] -= pop_vol
        totals["buy"] = max(0.0, totals["buy"])
        totals["sell"] = max(0.0, totals["sell"])

    def _calculate_ratio(self, pair: str) -> float:
        """Calculates buy volume / sell volume in the current window."""
        totals = self._totals.get(pair)
        if not totals:
            return 1.0

        buy_vol = totals["buy"]
        sell_vol = totals["sell"]

        if sell_vol == 0:
            return 2.5 if buy_vol > 0 else 1.0

        return round(buy_vol / sell_vol, 4)

    def get_imbalance_ratio(self, pair: str) -> float:
        """API for direct O(1) memory lookup by the engine."""
        pair = pair.lower()
        now = time.time()
        with self._lock:
            self._prune(pair, now)
            return self._calculate_ratio(pair)
