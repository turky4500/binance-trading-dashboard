# -*- coding: utf-8 -*-
"""
Read-only Binance public market-data client.

IMPORTANT:
  * Public market data ONLY. No API keys, no signing, no trading endpoints.
  * Tries the official REST hosts in order and falls back automatically
    (useful in regions where api.binance.com is geo-restricted):
        api.binance.com -> api1..api3.binance.com -> data-api.binance.vision
    data-api.binance.vision is Binance's official public market-data host.
  * Global rate limiter (min interval between requests), retries with
    exponential backoff on 429/5xx/timeouts, and clear error propagation.
"""
import json
import time
import threading
import urllib.error
import urllib.request

HOSTS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://data-api.binance.vision",
]

DEFAULT_MIN_INTERVAL = 0.10  # seconds between requests (well under 6000 weight/min)
DEFAULT_TIMEOUT = 25

_lock = threading.Lock()
_last_call = 0.0
_source = HOSTS[0]


def _wait():
    global _last_call
    with _lock:
        wait = DEFAULT_MIN_INTERVAL - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()


def _get(path, timeout=DEFAULT_TIMEOUT, attempts=4):
    global _source
    _wait()
    last_err = None
    for a in range(attempts):
        for host in HOSTS:
            url = f"{host}{path}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "binance-trading-dashboard/1.0"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    data = json.loads(r.read().decode("utf-8"))
                _source = host
                return data
            except urllib.error.HTTPError as e:
                # 451/403 => geo-restricted host; 418/429 => rate limited. Try next host.
                last_err = f"HTTP {e.code} from {host}"
                if e.code == 429:
                    time.sleep(2.0 * (a + 1))
                elif e.code in (418, 429, 451, 403):
                    continue
                raise RuntimeError(last_err)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = f"{type(e).__name__} from {host}: {e}"
                continue
        time.sleep(1.2 * (a + 1))
    raise RuntimeError(f"Binance data source unreachable after {attempts} attempts ({last_err})")


def ping():
    try:
        return _get("/api/v3/ping", attempts=2)
    except Exception:
        return {}


def server_time():
    t = _get("/api/v3/time", attempts=2)
    return int(t["serverTime"])


def exchange_info():
    return _get("/api/v3/exchangeInfo")


def ticker_24h():
    return _get("/api/v3/ticker/24hr")


def book_ticker():
    return _get("/api/v3/ticker/bookTicker")


def price(symbol):
    try:
        t = _get(f"/api/v3/ticker/price?symbol={symbol}", attempts=2)
        return float(t["price"])
    except Exception:
        return None


def klines(symbol, interval, limit=500, end_time=None):
    url = f"/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    if end_time:
        url += f"&endTime={int(end_time)}"
    return _get(url)


def source_host():
    return _source
