"""
Trading Vision — Freqtrade Hybrid Dashboard Backend
====================================================
A zero-load FastAPI proxy that translates Freqtrade REST-API data
into a premium dashboard experience. No background tasks, no DB,
no disk I/O. Pure request → response.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Configuration (env vars)
# ---------------------------------------------------------------------------
FREQTRADE_API_URL = os.getenv("FREQTRADE_API_URL", "http://localhost:8080")
FREQTRADE_USERNAME = os.getenv("FREQTRADE_API_USERNAME", "freqtrader")
FREQTRADE_PASSWORD = os.getenv("FREQTRADE_API_PASSWORD", "SuperSecurePassword")

# ---------------------------------------------------------------------------
# Freqtrade Authentication (JWT with auto-refresh)
# ---------------------------------------------------------------------------
import logging

logger = logging.getLogger("trading_vision")


class FreqtradeAuth:
    """Handles JWT authentication against the Freqtrade API server.

    - Lazy login: authenticates on first API call, not at startup
    - Auto-refresh: re-authenticates transparently on HTTP 401
    - Uses /api/v1/token/login with username + password (form-data)
    """

    def __init__(self) -> None:
        self._token: str | None = None
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=FREQTRADE_API_URL,
            timeout=httpx.Timeout(10.0, connect=5.0),
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        assert self._client is not None, "Auth not started"
        return self._client

    async def login(self) -> bool:
        """Authenticate against Freqtrade and store JWT token."""
        try:
            resp = await self.client.post(
                "/api/v1/token/login",
                data={
                    "username": FREQTRADE_USERNAME,
                    "password": FREQTRADE_PASSWORD,
                },
            )
            if resp.status_code == 200:
                self._token = resp.json().get("access_token", "")
                self.client.headers["Authorization"] = f"Bearer {self._token}"
                logger.info("Freqtrade JWT login successful")
                return True
            else:
                logger.warning("Freqtrade login failed: %s", resp.status_code)
                return False
        except httpx.ConnectError:
            logger.debug("Freqtrade not reachable for login")
            return False

    async def ensure_auth(self) -> None:
        """Login if we don't have a token yet."""
        if self._token is None:
            await self.login()

    async def get(
        self, path: str, params: dict | None = None
    ) -> httpx.Response:
        """GET with automatic auth — retries once on 401."""
        await self.ensure_auth()
        resp = await self.client.get(path, params=params)

        # Token expired → re-login and retry once
        if resp.status_code == 401:
            logger.info("Got 401, refreshing JWT token …")
            if await self.login():
                resp = await self.client.get(path, params=params)

        return resp


_auth = FreqtradeAuth()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the shared HTTP client lifecycle."""
    await _auth.start()
    yield
    await _auth.stop()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Trading Vision",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _ft_get(path: str, params: dict | None = None) -> dict[str, Any]:
    """Forward a GET request to the Freqtrade API (with auth)."""
    resp = await _auth.get(path, params=params)
    resp.raise_for_status()
    return resp.json()


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# AI Signal Translator
# ---------------------------------------------------------------------------

def _rsi_text(rsi: float | None, pair: str) -> str:
    if rsi is None:
        return f"Kein RSI-Signal für {pair} verfügbar."
    if rsi >= 80:
        return f"{pair} ist stark überkauft (RSI {rsi:.0f}). Ein Rücksetzer ist sehr wahrscheinlich – ich halte die Füße still."
    if rsi >= 70:
        return f"{pair} nähert sich der überkauften Zone (RSI {rsi:.0f}). Vorsicht – Gewinnmitnahmen könnten einsetzen."
    if rsi <= 20:
        return f"{pair} ist extrem überverkauft (RSI {rsi:.0f}). Das könnte eine starke Bounce-Chance sein!"
    if rsi <= 30:
        return f"{pair} bewegt sich in der überverkauften Zone (RSI {rsi:.0f}). Ich beobachte genau auf Umkehrsignale."
    if 45 <= rsi <= 55:
        return f"{pair} ist neutral (RSI {rsi:.0f}). Kein klares Signal – ich warte geduldig."
    if rsi > 55:
        return f"{pair} zeigt bullische Tendenz (RSI {rsi:.0f}). Der Trend sieht positiv aus."
    return f"{pair} zeigt leichte Schwäche (RSI {rsi:.0f}). Könnte eine Kaufgelegenheit werden."


def _bb_text(close: float | None, bb_upper: float | None, bb_lower: float | None, pair: str) -> str:
    if close is None or bb_upper is None or bb_lower is None:
        return ""
    if close >= bb_upper:
        return f"Der Kurs von {pair} berührt das obere Bollinger Band – potenziell überdehnt."
    if close <= bb_lower:
        return f"{pair} testet das untere Bollinger Band – ein Rebound ist möglich."
    mid = (bb_upper + bb_lower) / 2
    if abs(close - mid) / (bb_upper - bb_lower + 1e-9) < 0.1:
        return f"{pair} pendelt nahe der Bollinger-Mittellinie – Markt ist unentschlossen."
    return ""


def _macd_text(macd: float | None, signal: float | None, pair: str) -> str:
    if macd is None or signal is None:
        return ""
    if macd > signal and macd > 0:
        return f"MACD von {pair} ist bullisch – Momentum steigt."
    if macd < signal and macd < 0:
        return f"MACD von {pair} ist bärisch – Abwärtsdruck nimmt zu."
    if macd > signal and macd <= 0:
        return f"MACD-Kreuzung bei {pair}: erstes bullisches Signal, noch unter Null."
    if macd < signal and macd >= 0:
        return f"MACD-Kreuzung bei {pair}: Momentum lässt nach, Vorsicht."
    return ""


def _volume_text(vol: float | None, vol_mean: float | None, pair: str) -> str:
    if vol is None or vol_mean is None or vol_mean == 0:
        return ""
    ratio = vol / vol_mean
    if ratio > 2.0:
        return f"Ungewöhnlich hohes Volumen bei {pair} ({ratio:.1f}x Durchschnitt) – da passiert etwas Großes!"
    if ratio > 1.5:
        return f"Erhöhtes Volumen bei {pair} – steigendes Interesse im Markt."
    if ratio < 0.5:
        return f"Sehr niedriges Volumen bei {pair} – der Markt schläft. Vorsicht vor Fakeouts."
    return ""


def translate_signals(pair: str, indicators: dict[str, Any]) -> list[str]:
    """
    Turn raw indicator values into human-readable German sentences.
    Expects keys like 'rsi', 'bb_upperband', 'bb_lowerband', 'macd', 'macdsignal',
    'close', 'volume', 'volume_mean' (names may vary by Freqtrade strategy).
    """
    sentences: list[str] = []

    rsi = indicators.get("rsi") or indicators.get("RSI")
    sentences.append(_rsi_text(rsi, pair))

    close = indicators.get("close")
    bb_upper = indicators.get("bb_upperband") or indicators.get("bb_upper")
    bb_lower = indicators.get("bb_lowerband") or indicators.get("bb_lower")
    bb = _bb_text(close, bb_upper, bb_lower, pair)
    if bb:
        sentences.append(bb)

    macd = indicators.get("macd") or indicators.get("MACD")
    macd_signal = indicators.get("macdsignal") or indicators.get("MACDsignal")
    mt = _macd_text(macd, macd_signal, pair)
    if mt:
        sentences.append(mt)

    vol = indicators.get("volume")
    vol_mean = indicators.get("volume_mean") or indicators.get("volume_sma")
    vt = _volume_text(vol, vol_mean, pair)
    if vt:
        sentences.append(vt)

    return sentences


def _compute_signal_proximity(indicators: dict[str, Any]) -> float:
    """
    Heuristic 0-100 % proximity to a buy signal.
    Higher = closer to buy trigger.
    """
    score = 50.0  # neutral starting point

    rsi = indicators.get("rsi") or indicators.get("RSI")
    if rsi is not None:
        # RSI < 30 adds up to +25 pts; RSI > 70 subtracts up to -25 pts
        if rsi <= 30:
            score += 25 * (1 - rsi / 30)
        elif rsi >= 70:
            score -= 25 * ((rsi - 70) / 30)

    close = indicators.get("close")
    bb_lower = indicators.get("bb_lowerband") or indicators.get("bb_lower")
    bb_upper = indicators.get("bb_upperband") or indicators.get("bb_upper")
    if close is not None and bb_lower is not None and bb_upper is not None:
        bb_range = bb_upper - bb_lower
        if bb_range > 0:
            position = (close - bb_lower) / bb_range  # 0 = at lower, 1 = at upper
            score += 25 * (1 - position) - 12.5  # lower → higher score

    macd = indicators.get("macd") or indicators.get("MACD")
    macd_signal = indicators.get("macdsignal") or indicators.get("MACDsignal")
    if macd is not None and macd_signal is not None:
        if macd > macd_signal:
            score += 10
        else:
            score -= 5

    return _clamp(score)


# ---------------------------------------------------------------------------
# In-Memory Cache — keeps bot load minimal (30s TTL)
# ---------------------------------------------------------------------------
_radar_cache: dict[str, Any] = {"data": None, "ts": 0.0}
RADAR_TTL = 30  # seconds


async def _fetch_pair_indicators(pair: str, timeframe: str = "5m") -> dict[str, Any]:
    """Fetch latest indicators for a single pair. Returns empty dict on error."""
    try:
        ph = await _ft_get(
            "/api/v1/pair_history",
            params={"pair": pair, "timeframe": timeframe, "limit": 1},
        )
        candles = ph.get("data", [])
        return candles[-1] if candles else {}
    except Exception:
        return {}


async def _build_radar() -> dict[str, Any]:
    """Scan the full whitelist in parallel and compute heat scores.

    Results are cached in-memory for RADAR_TTL seconds so that
    multiple frontend polls don't hammer the bot.
    """
    now = time.monotonic()
    if _radar_cache["data"] is not None and (now - _radar_cache["ts"]) < RADAR_TTL:
        return _radar_cache["data"]

    # 1) Fetch whitelist
    wl_resp = await _ft_get("/api/v1/whitelist")
    pairs: list[str] = wl_resp.get("whitelist", [])

    if not pairs:
        result = {"pairs_scanned": 0, "targets": [], "top_target": None}
        _radar_cache["data"] = result
        _radar_cache["ts"] = now
        return result

    # 2) Fetch config for timeframe
    timeframe = "5m"
    try:
        cfg = await _ft_get("/api/v1/show_config")
        timeframe = cfg.get("timeframe", "5m")
    except Exception:
        pass

    # 3) Parallel fetch indicators for ALL pairs  🚀
    indicator_results = await asyncio.gather(
        *[_fetch_pair_indicators(p, timeframe) for p in pairs]
    )

    # 4) Compute heat score + AI sentences for each pair
    targets: list[dict[str, Any]] = []
    for pair, indicators in zip(pairs, indicator_results):
        heat = round(_compute_signal_proximity(indicators), 1)
        # Only generate sentences for top candidates (save CPU)
        sentences = translate_signals(pair, indicators) if heat >= 50 else []
        targets.append({
            "pair": pair,
            "heat": heat,
            "sentences": sentences,
            "rsi": indicators.get("rsi") or indicators.get("RSI"),
            "close": indicators.get("close"),
        })

    # 5) Sort by heat descending
    targets.sort(key=lambda t: t["heat"], reverse=True)

    top = targets[0] if targets else None
    result = {
        "pairs_scanned": len(pairs),
        "targets": targets,
        "top_target": top["pair"] if top else None,
        "top_heat": top["heat"] if top else 0,
        "cached_at": time.time(),
    }
    _radar_cache["data"] = result
    _radar_cache["ts"] = now
    logger.info("Radar scan complete: %d pairs, top=%s (%.0f%%)",
                len(pairs), result["top_target"], result.get("top_heat", 0))
    return result


# ---------------------------------------------------------------------------
# API Routes — Proxy
# ---------------------------------------------------------------------------

@app.get("/api/proxy/status")
async def proxy_status():
    """Return open trades enriched with signal proximity."""
    try:
        trades: list[dict] = await _ft_get("/api/v1/status")
        enriched = []
        for t in trades:
            pair = t.get("pair", "???")
            proximity = 50.0
            indicators: dict = {}
            try:
                ph = await _ft_get(
                    "/api/v1/pair_history",
                    params={
                        "pair": pair,
                        "timeframe": t.get("timeframe", "5m"),
                        "limit": 1,
                    },
                )
                candles = ph.get("data", [])
                if candles:
                    indicators = candles[-1]
                    proximity = _compute_signal_proximity(indicators)
            except Exception:
                pass

            enriched.append({
                "pair": pair,
                "stake_amount": t.get("stake_amount"),
                "open_rate": t.get("open_rate"),
                "current_rate": t.get("current_rate"),
                "profit_pct": t.get("profit_pct", 0),
                "profit_abs": t.get("profit_abs", 0),
                "open_date": t.get("open_date_hum", t.get("open_date", "")),
                "timeframe": t.get("timeframe", "5m"),
                "signal_proximity": round(proximity, 1),
                "stoploss": t.get("stoploss_current_dist_pct"),
                "trade_id": t.get("trade_id"),
            })
        return JSONResponse({"trades": enriched})
    except httpx.ConnectError:
        return JSONResponse({"trades": [], "error": "Bot nicht erreichbar"}, status_code=200)
    except Exception as exc:
        return JSONResponse({"trades": [], "error": str(exc)}, status_code=200)


@app.get("/api/proxy/pair_history")
async def proxy_pair_history(
    pair: str = Query(..., description="Trading pair, e.g. BTC/USDT"),
    timeframe: str = Query("5m"),
    limit: int = Query(50),
):
    """Forward pair_history request."""
    try:
        data = await _ft_get(
            "/api/v1/pair_history",
            params={"pair": pair, "timeframe": timeframe, "limit": limit},
        )
        return JSONResponse(data)
    except httpx.ConnectError:
        return JSONResponse({"error": "Bot nicht erreichbar"}, status_code=200)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=200)


@app.get("/api/proxy/health")
async def proxy_health():
    """Quick health check — bot online? latency? dry-run?"""
    t0 = time.monotonic()
    try:
        ping = await _ft_get("/api/v1/ping")
        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        config: dict = {}
        try:
            config = await _ft_get("/api/v1/show_config")
        except Exception:
            pass

        return JSONResponse({
            "bot_online": True,
            "latency_ms": latency_ms,
            "dry_run": config.get("dry_run", None),
            "state": config.get("state", "unknown"),
            "strategy": config.get("strategy", "unknown"),
            "exchange": config.get("exchange", "unknown"),
            "version": ping.get("version", "unknown"),
        })
    except httpx.ConnectError:
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        return JSONResponse({
            "bot_online": False,
            "latency_ms": latency_ms,
            "dry_run": None,
            "state": "offline",
            "strategy": "—",
            "exchange": "—",
            "version": "—",
        })
    except Exception as exc:
        return JSONResponse({
            "bot_online": False,
            "latency_ms": -1,
            "dry_run": None,
            "state": "error",
            "error": str(exc),
        })


@app.get("/api/proxy/radar")
async def proxy_radar():
    """Whitelist radar: scan all pairs, compute heat scores, return sorted."""
    try:
        data = await _build_radar()
        return JSONResponse(data)
    except httpx.ConnectError:
        return JSONResponse({
            "pairs_scanned": 0,
            "targets": [],
            "top_target": None,
            "error": "Bot nicht erreichbar",
        })
    except Exception as exc:
        return JSONResponse({
            "pairs_scanned": 0,
            "targets": [],
            "top_target": None,
            "error": str(exc),
        })


@app.get("/api/proxy/mind")
async def proxy_mind():
    """AI-translated insights: combines active trades + whitelist radar."""
    thoughts: list[dict[str, Any]] = []
    try:
        # 1) Active trades with profit context
        try:
            trades: list[dict] = await _ft_get("/api/v1/status")
            for t in trades:
                pair = t.get("pair", "???")
                indicators: dict = {}
                try:
                    ph = await _ft_get(
                        "/api/v1/pair_history",
                        params={"pair": pair, "timeframe": t.get("timeframe", "5m"), "limit": 1},
                    )
                    candles = ph.get("data", [])
                    if candles:
                        indicators = candles[-1]
                except Exception:
                    pass

                sentences = translate_signals(pair, indicators)
                pct = t.get("profit_pct", 0)
                if pct is not None:
                    pct_val = pct * 100 if abs(pct) < 1 else pct
                    if pct_val > 5:
                        sentences.append(f"💰 {pair} läuft hervorragend mit {pct_val:+.2f}% Gewinn.")
                    elif pct_val > 0:
                        sentences.append(f"📈 {pair} ist leicht im Plus ({pct_val:+.2f}%).")
                    elif pct_val > -2:
                        sentences.append(f"📉 {pair} ist leicht im Minus ({pct_val:+.2f}%).")
                    else:
                        sentences.append(f"⚠️ {pair} steht bei {pct_val:+.2f}%.")

                thoughts.append({"pair": pair, "sentences": sentences, "type": "trade"})
        except Exception:
            pass

        # 2) Top radar targets (from cached whitelist scan)
        try:
            radar = await _build_radar()
            top_5 = radar.get("targets", [])[:5]
            # Avoid duplicates with active trades
            active_pairs = {t["pair"] for t in thoughts}
            for target in top_5:
                if target["pair"] not in active_pairs and target.get("sentences"):
                    thoughts.append({
                        "pair": target["pair"],
                        "sentences": target["sentences"],
                        "type": "radar",
                        "heat": target["heat"],
                    })
        except Exception:
            pass

        if not thoughts:
            thoughts = [{
                "pair": "System",
                "sentences": [
                    "Der Bot scannt den Markt und wartet auf Signale.",
                    "Sobald er online ist, erscheinen hier KI-Analysen.",
                ],
                "type": "system",
            }]

        return JSONResponse({"thoughts": thoughts})
    except httpx.ConnectError:
        return JSONResponse({
            "thoughts": [{
                "pair": "System",
                "sentences": ["❌ Bot ist nicht erreichbar."],
                "type": "system",
            }]
        })
    except Exception as exc:
        return JSONResponse({
            "thoughts": [{
                "pair": "System",
                "sentences": [f"Fehler: {exc}"],
                "type": "system",
            }]
        })


# ---------------------------------------------------------------------------
# Static files & SPA fallback
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")