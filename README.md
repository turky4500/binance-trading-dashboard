# 📊 Binance Trading Dashboard

**لوحة تحكم تلقائية لرصد أفضل فرص التداول على Binance Spot — تحليل فقط، بدون أي تداول.**

A fully automated, **read-only** trading-opportunity dashboard for Binance Spot. It pulls live public market data, runs deterministic multi-timeframe technical analysis, publishes only high-quality setups (Entry / SL / TP / R:R / Score), tracks their lifecycle, keeps a performance history, and ships everything as a free static site on GitHub Pages — refreshed every 15 minutes by GitHub Actions.

> ⚠️ **This project never places orders.** No trading permissions, no withdrawals, no API keys. It is analysis software, not a trading bot.

---

## كيف يعمل النظام — How it works

```
Binance public market data (read-only, no API key needed)
        │
        ▼
Universe filter (USDT spot pairs, liquidity, spread, activity)
        │
        ▼
Multi-timeframe technical analysis  (15m / 1h / 4h / 1d)
  EMA 20/50/200 · RSI · MACD · ATR · VWAP · SuperTrend · volume · structure
  swing levels · breakout detection · retest confirmation
        │
        ▼
Deterministic trade plans  (entry zone, SL, TP1/2/3, R:R, invalidation)
        │
        ▼
100-point scoring  (configurable weights, threshold 82 by default)
        │
        ▼
Lifecycle tracking  (READY → TRIGGERED → TP1/TP2/TP3 HIT / STOPPED / EXPIRED)
        │
        ▼
JSON storage committed to the repo  →  static dashboard on GitHub Pages
```

The engine is **100% deterministic**: every indicator, entry, stop and target is computed from real market data. No LLM invents any price or level (the only generated text is template explanations in English/Arabic).

## Features

- 🌐 Live Binance spot data (public endpoints only — zero secrets required)
- ⚡ Real-time prices in the dashboard via Binance public WebSocket streams — prices flash green/red on every change (degrades gracefully to pipeline prices when the stream is unavailable)
- 📈 Multi-timeframe analysis: 15m / 1h / 4h / 1d with EMA 20/50/200, RSI, MACD, ATR, VWAP, SuperTrend (10,3), volume, structure
- 🎯 Complete trade plans: entry zone, SL (ATR + structure based), TP1/TP2/TP3, R:R, invalidation level
- 🏆 Configurable 100-point scoring with per-component breakdown
- 🔁 Automatic refresh every 15 minutes via GitHub Actions (no manual re-runs)
- 🛡️ **Publish-time freshness guard**: a plan is never published if the live price already reached its TP1 (stale-on-arrival recommendations are impossible), and READY setups that ran away from their entry zone are downgraded to WAITING. Every card and analysis shows the live **distance to TP1** (updated each second from the WebSocket price) — you always know how fresh a setup is
- ⏱ **Recommendation timestamp on every card**: publication time in your local 12-hour clock with the date, a color-coded age badge (Fresh <6h / Aging 6-18h / Old >18h) and a validity bar showing how much of the 48-hour window remains — so you can judge instantly whether a setup is still worth entering
- ⏱ Live countdown to the next data cycle + fully automatic in-page refresh — new data renders without any manual reload, with a visible toast and a LIVE/STALE indicator
- 🧪 Historical backtest (last 6 months, same deterministic rules) with score calibration and per-setup-type statistics — refreshed daily and shown transparently on the Performance page
- 🌗 Dark / Light theme toggle (persisted across visits)
- 🟢 Setup lifecycle: `READY`, `WAITING_CONFIRMATION`, `TRIGGERED`, `TP1_HIT`, `TP2_HIT`, `TP3_HIT`, `STOPPED`, `EXPIRED`, `INVALIDATED`
- 📊 Performance history page (win rate, TP hit rates, avg score/R:R) — with the explicit caveat that it is not a promise
- 📉 Upgraded charts: 1H/4H timeframe switcher, volume bars, RSI(14) panel, EMA/VWAP/SuperTrend overlays and trade levels drawn on the same canvas
- 🧮 Position size calculator in every analysis (risk-based sizing: qty = capital × risk% ÷ stop distance — spot only, no leverage)
- ⭐ Persistent watchlist with live prices, star-to-follow cards and a dedicated filter
- 📈 Market tab: historical breadth chart (% of top-30 above EMA50), BTC line, pipeline health (cycles/24h, longest gap, avg duration) and a live pipeline log
- ⚙️ In-page settings panel (theme, language, live prices on/off, display min-score and card limits, alert toggles) — persisted locally
- 📱 Installable PWA (manifest + service worker with honest network-first caching — stale data is never served as live), works offline
- 🩺 Health monitoring: [UptimeRobot guide](docs/HEALTH_MONITORING.md) + optional Telegram notification when the pipeline fails
- 🔬 **Coin Analyzer tab**: type any Binance spot symbol and get an instant, on-demand analysis computed **in your browser** from live public Binance data — verdict (setup or not), full trade plan, 4-timeframe indicator table (EMA/RSI/MACD/ATR/VWAP/SuperTrend/volume), a checklist of why no setup exists, a 4H chart, and one-tap add-to-watchlist. The JavaScript engine is a faithful mirror of the Python analyzer, locked in parity by a **golden test** (`tests/golden_js.test.js`) that runs in CI on every push — the two engines cannot drift apart.
- ⚡ **Quant Agent tab**: a separate deterministic opportunity scanner fed by the server-side pipeline (never by the browser). It walks **every qualified Binance Spot/USDT pair** (no top-N cap) and evaluates it independently on **15m, 1h and 4h**, with confirmation from the next higher timeframe(s). It requires price above EMA200, bullish SuperTrend (10,3), no three-candle chop, no upper-wick rejection or descending swing highs, a held breakout/SuperTrend bounce, score ≥82 and TP1 R:R ≥1.5. Cards show the execution timeframe, while strict JSON in `data/agent_scan.json` includes per-timeframe signals and rejection reasons.
- 🔍 Search, filters (direction/status/high-score) and sorting
- 📱 Responsive dark UI, English/Arabic (RTL), 4H candlestick charts with levels drawn
- 🔔 Full lifecycle alerts: Telegram notifications for every transition (READY / TRIGGERED / TP hits / STOPPED / EXPIRED / INVALIDATED — per-event toggles), plus optional in-browser notifications and distinctive sounds for each event while the dashboard is open
- 🛡️ Error handling: stale data is clearly flagged as `DATA SOURCE ERROR` with the last successful update shown

## Repository layout

```
├── analyzer/            # market-data client, indicators, strategies, scoring, tracker
├── backend/             # FastAPI server for local development preview
├── frontend/            # static dashboard (no build step, no external CDN)
├── config/settings.json # ALL tunable parameters (scores, filters, risk, timeframes)
├── scripts/             # static-site builder (Pages artifact + offline snapshot)
├── tests/               # deterministic unit tests
├── data/                # JSON storage (opportunities, history, meta, kline cache)
└── .github/workflows/   # analyze & deploy pipeline (every 15 min)
```

## 🚀 Quick start (local)

```bash
git clone https://github.com/<your-user>/binance-trading-dashboard.git
cd binance-trading-dashboard

python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 1) run one analysis cycle (fetches live Binance data, writes data/*.json)
python -m analyzer.run

# 2) start the dashboard locally
uvicorn backend.main:app --host 0.0.0.0 --port 8000
# open http://localhost:8000
```

Run the tests:

```bash
python -m pytest tests/ -q
```

## 🌍 Deploy to GitHub Pages (free, automatic)

1. Create a **public** repository named `binance-trading-dashboard` on GitHub (public = free Pages + unlimited Actions minutes).
2. Push this project to it:

   ```bash
   git init && git add -A
   git commit -m "Initial commit: Binance Trading Dashboard"
   git branch -M main
   git remote add origin https://github.com/<your-user>/binance-trading-dashboard.git
   git push -u origin main
   ```

3. In the repo: **Settings → Pages → Source → "GitHub Actions"** (one-time setting).
4. The workflow `Analyze & Deploy Dashboard` runs immediately after the push, then automatically **every 15 minutes**. Your dashboard will be live at:

   ```
   https://<your-user>.github.io/binance-trading-dashboard/
   ```

The dashboard polls its data files every 60 seconds, so new opportunities appear in the browser automatically — you never re-run anything manually.

### GitHub Secrets required

**None.** The scanner uses Binance *public* market data, which needs no authentication.

Optional (only for Telegram alerts): `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (Settings → Secrets and variables → Actions).

## 🔑 Binance "Read-Only" API — do I need keys?

No. The dashboard uses only public market-data endpoints (`/api/v3/ticker/24hr`, `/api/v3/klines`, `/api/v3/exchangeInfo`, `/api/v3/ticker/bookTicker`) which require no API key at all. There is therefore **no key to leak** — the codebase contains no `BINANCE_API_KEY` / `BINANCE_SECRET` anywhere.

If you ever want a personal key anyway: Binance → API Management → Create API → restrict it to **"Enable Reading" only** (never enable Spot Trading/Withdrawals), then store it only in your local `.env` — but the dashboard does not use it.

> Geo-note: some regions block `api.binance.com`. The client automatically falls back to Binance's official public data host `data-api.binance.vision` (same market data, also maintained by Binance), so the pipeline keeps working.

> **Precise scheduling:** GitHub's cron is best-effort (it frequently runs 10–45 min late). For hard on-the-second timing, an external free scheduler (cron-job.org) triggers the workflow via the GitHub API — full setup guide in [docs/EXTERNAL_SCHEDULER.md](docs/EXTERNAL_SCHEDULER.md) (English/Arabic, ~3 minutes, tested end-to-end). The dashboard itself is always honest about timing: the countdown ring reaches zero, the "Next Update" field switches to **SYNC (مزامنة)** if the pipeline is late, and the page retries automatically until fresh data arrives — no manual reloads ever needed.

## ⚙️ Configuration — `config/settings.json`

Everything is tunable in one file:

| Key | Default | Meaning |
|---|---|---|
| `update_interval_minutes` | `15` | How often GitHub Actions re-analyzes (also change the workflow cron) |
| `min_score_to_show` | `82` | Minimum score for an opportunity to be displayed (calibrated from backtest: score ≥80 historically ~36% TP1 rate vs ~20% below) |
| `max_opportunities` | `8` | Maximum number of displayed opportunities |
| `expiry_hours` | `48` | Setup expires if not triggered within this time |
| `stale_after_minutes` | `45` | Dashboard flags data as stale (DATA SOURCE ERROR) after this |
| `universe.max_symbols_to_screen` | `0` | `0` means scan every qualified Binance Spot/USDT pair (no top-N cap) |
| `universe.min_quote_volume_24h` | `5e6` | Minimum 24h quote volume (USDT) for a pair to enter the full agent scan |
| `universe.min_trades_24h` | `1000` | Minimum 24h trade count for basic market quality |
| `universe.max_spread_pct` | `0.20` | Maximum bid/ask spread |
| `min_rr_tp1` | `1.5` | Minimum R:R to TP1 for a plan to exist at all (kills marginal setups) |
| `universe.exclude_24h_change_gt` | `12` | Skip coins that pumped more than this % in 24h (no chasing) |
| `universe.exclude_assets` | list | Extra symbols to ignore |
| `scoring.*` | 20/15/15/15/10/10/10/5 | Per-component weights (must sum to 100) |
| `risk.atr_sl_min` / `atr_sl_max` | `0.8` / `2.0` | Stop-loss distance bounds in ATR multiples |
| `risk.min_tp1_distance_atr` | `1.2` | Minimum TP1 distance from entry (4H ATRs) — kills trivially-close targets that would look "late" |
| `strategy.allow_shorts` | `false` | SHORT setups disabled (spot trading — sell signals only make sense if you hold the asset) |
| `strategy.disabled_setups` | `["PULLBACK"]` | Setup types to skip entirely (PULLBACK historically ~9% win rate in backtest vs ~44% for VWAP_HOLD) |
| `market_filter.enabled` | `true` | When true, no NEW setups are published while broad-market breadth is below `min_breadth_pct` (existing ones keep being tracked) |
| `market_filter.min_breadth_pct` | `40` | Breadth gate: % of top-30 coins above their daily EMA50 required to publish new setups |
| `backtest.months` / `backtest.top_symbols` | `6` / `12` | Historical simulation depth (runs daily via `backtest.yml`) |
| `supertrend.period` / `supertrend.multiplier` | `10` / `3.0` | SuperTrend parameters (per timeframe, on charts, in scoring) |
| `quant_agent.enabled` | `true` | Enable the deterministic multi-timeframe SuperTrend scan and `agent_scan.json` output |
| `quant_agent.timeframes` | `["15m","1h","4h"]` | Execution timeframes scanned independently for opportunities |
| `quant_agent.max_signals_per_timeframe` | `4` | Reserve a fair card cap for each execution timeframe |
| `quant_agent.min_score` / `min_rr_tp1` | `82` / `1.5` | Minimum agent quality score and TP1 reward/risk |
| `quant_agent.max_ema200_extension_pct` | `5.0` | Reject vertical moves too far above EMA200 on the selected execution timeframe |
| `quant_agent.max_live_chase_atr` | `0.75` | Reject a live price that ran too far beyond the last closed execution-timeframe candle |
| `quant_agent.max_upper_wick_body_ratio` | `1.5` | Reject long upper wicks testing recent resistance |
| `quant_agent.max_stop_distance_pct` | `3.0` | Absolute stop-distance ceiling (the R:R gate is normally stricter) |
| `quant_agent.tp1_pct` / `tp2_pct` / `tp3_pct` | `1.2` / `2.25` / `4.25` | Scalp target percentages; TP2/TP3 prefer nearby structure inside their bands |
| `risk.pullback_zone_atr` | `0.6` | Entry-zone width (pullback setups) |
| `telegram.enabled` | `false` | Enable Telegram alerts |
| `telegram.min_score_alert` | `85` | Alert threshold for new setups |
| `telegram.notify.*` | per-event toggles | Enable/disable Telegram alerts per lifecycle event (`new_setup`, `ready`, `triggered`, `tp_hit`, `stopped`, `expired`, `invalidated`) |

Timeframes are listed per opportunity and analyzed on `15m / 1h / 4h / 1d` (defined in `analyzer/scanner.py`, `TFS`).

## 🟢 Setup lifecycle

| Status | Meaning |
|---|---|
| `READY` | Entry zone is active now — the plan is actionable |
| `WAITING_CONFIRMATION` | Needs a condition first (e.g. retest of a breakout) — do not enter yet |
| `TRIGGERED` | Price touched the entry zone (levels are then frozen) |
| `TP1_HIT` / `TP2_HIT` / `TP3_HIT` | Progressive targets reached |
| `STOPPED` | Stop loss hit (checked conservatively first) |
| `EXPIRED` | Never triggered within the expiry window |
| `INVALIDATED` | Invalidation level broken — the idea was wrong |

Tracking is done with **closed candles only** and targets are evaluated **only from the candle that triggered the entry onward** — no look-ahead bias.

## 📈 Scoring (100 points)

Trend Alignment 20 · Market Structure 15 · Support/Resistance 15 · Volume 15 · Momentum 10 · Entry Quality 10 · Risk/Reward 10 · Liquidity 5. Breakdown per component is shown in every analysis modal. Grading: ≥90 Excellent · ≥80 Strong · ≥70 Good · <82 not shown by default (threshold is configurable via `min_score_to_show`).

## 📊 Performance page

Statistics over the scanner's own closed history (win rate, TP1/TP2 hit rates, average score & R:R, hold times). **These describe the tool, not the future** — historical results never guarantee future performance.

## 🔔 Alerts (Telegram + browser + sound)

**Telegram (optional):** set `telegram.enabled: true` in `config/settings.json` and add two repository secrets (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). The workflow then sends:

- 🚨 every **new** setup scoring ≥ `telegram.min_score_alert` (default 85)
- 🎯 TRIGGERED / ✅ TP1-3 HIT / 🛑 STOPPED / ⏳ EXPIRED / ❌ INVALIDATED / 👌 READY transitions detected that cycle

Each event type has its own toggle under `telegram.notify` in the config. Tokens are read from environment variables only — never stored, never logged.

**Browser notifications + sounds (no setup needed):** while the dashboard page is open, every fresh data cycle is diffed against what is displayed — lifecycle changes and brand-new setups fire a browser notification, a toast, and a distinctive beep (ascending tone for take-profits, descending for stop-outs, etc.). The 🔔 button in the top bar opens a control panel: master toggles for notifications/sound, per-event checkboxes, and a sound test. Preferences persist locally. Note: browser notifications require the page to be open (a background worker/Telegram covers the closed case).

## 🧪 Quality checks in CI

The workflow runs `pytest` plus the golden parity test (`node tests/golden_js.test.js` — fetches the same historical bars and asserts the JS Coin-Analyzer engine matches Python reference outputs within tight tolerances) before every analysis. The test suite verifies indicator math (EMA/RSI known values), plan invariants (entry > SL, TP ordering, R:R minimums, ATR-bounded stops), scoring bounds (weights sum to 100), tracker state transitions (TRIGGERED → TP / STOPPED / EXPIRED) and storage round-trips.

## 🗄️ Storage choice

Plain JSON files committed to the repository (`data/`). Rationale: zero cost, zero infrastructure, fully transparent version history via git, atomic writes prevent corruption, and GitHub Pages serves them directly. History is capped at 500 records and the kline cache only keeps symbols with active setups.

## Alternatives & notes

- **Vercel / Cloudflare Pages**: you can deploy the `site/` folder instead of GitHub Pages if you prefer — but a separate scheduler would be needed to refresh `data/` (a cron GitHub Action can keep committing data regardless of where the site is hosted).
- Keep the repository **public** for free unlimited Actions minutes (a private repo with 96 runs/day will exceed the free allowance).

## ⚠️ Disclaimer

This software is provided for informational and educational purposes only. It is not financial advice, and nothing it outputs is a guarantee. Trading digital assets involves substantial risk of loss. You are solely responsible for your own trading decisions.
