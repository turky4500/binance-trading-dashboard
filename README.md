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
  EMA 20/50/200 · RSI · MACD · ATR · VWAP · volume · structure
  swing levels · breakout detection · retest confirmation
        │
        ▼
Deterministic trade plans  (entry zone, SL, TP1/2/3, R:R, invalidation)
        │
        ▼
100-point scoring  (configurable weights, threshold 70 by default)
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
- 📈 Multi-timeframe analysis: 15m / 1h / 4h / 1d with EMA 20/50/200, RSI, MACD, ATR, VWAP, volume, structure
- 🎯 Complete trade plans: entry zone, SL (ATR + structure based), TP1/TP2/TP3, R:R, invalidation level
- 🏆 Configurable 100-point scoring with per-component breakdown
- 🔁 Automatic refresh every 15 minutes via GitHub Actions (no manual re-runs)
- ⏱ Live countdown to the next data cycle + fully automatic in-page refresh — new data renders without any manual reload, with a visible toast and a LIVE/STALE indicator
- 🌗 Dark / Light theme toggle (persisted across visits)
- 🟢 Setup lifecycle: `READY`, `WAITING_CONFIRMATION`, `TRIGGERED`, `TP1_HIT`, `TP2_HIT`, `TP3_HIT`, `STOPPED`, `EXPIRED`, `INVALIDATED`
- 📊 Performance history page (win rate, TP hit rates, avg score/R:R) — with the explicit caveat that it is not a promise
- 🔍 Search, filters (direction/status/high-score) and sorting
- 📱 Responsive dark UI, English/Arabic (RTL), 4H candlestick charts with levels drawn
- 🔔 Optional Telegram alerts for setups scoring ≥ 85
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
| `min_score_to_show` | `70` | Minimum score for an opportunity to be displayed |
| `max_opportunities` | `8` | Maximum number of displayed opportunities |
| `expiry_hours` | `48` | Setup expires if not triggered within this time |
| `stale_after_minutes` | `45` | Dashboard flags data as stale (DATA SOURCE ERROR) after this |
| `universe.min_quote_volume_24h` | `8e6` | Minimum 24h quote volume (USDT) for a pair to be analyzed |
| `universe.max_spread_pct` | `0.15` | Maximum bid/ask spread |
| `universe.exclude_24h_change_gt` | `25` | Skip coins that pumped more than this % in 24h (no chasing) |
| `universe.exclude_assets` | list | Extra symbols to ignore |
| `scoring.*` | 20/15/15/15/10/10/10/5 | Per-component weights (must sum to 100) |
| `risk.atr_sl_min` / `atr_sl_max` | `0.8` / `2.0` | Stop-loss distance bounds in ATR multiples |
| `risk.pullback_zone_atr` | `0.6` | Entry-zone width (pullback setups) |
| `telegram.enabled` | `false` | Enable Telegram alerts |
| `telegram.min_score_alert` | `85` | Alert threshold |

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

Trend Alignment 20 · Market Structure 15 · Support/Resistance 15 · Volume 15 · Momentum 10 · Entry Quality 10 · Risk/Reward 10 · Liquidity 5. Breakdown per component is shown in every analysis modal. Grading: ≥90 Excellent · ≥80 Strong · ≥70 Good · <70 not shown (unless you lower the threshold).

## 📊 Performance page

Statistics over the scanner's own closed history (win rate, TP1/TP2 hit rates, average score & R:R, hold times). **These describe the tool, not the future** — historical results never guarantee future performance.

## 🔔 Telegram alerts (optional)

Set `telegram.enabled: true` and add the two repository secrets (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). When a **new** setup scores ≥ 85, the workflow sends:

```
🚨 New High-Quality Setup
ZEC/USDT
LONG
Entry: ...  SL: ...  TP1: ...  TP2: ...  R:R: ...  Score: 89/100
```

Tokens are read from environment variables only — never stored, never logged.

## 🧪 Quality checks in CI

The workflow runs `pytest` before every analysis. The test suite verifies indicator math (EMA/RSI known values), plan invariants (entry > SL, TP ordering, R:R minimums, ATR-bounded stops), scoring bounds (weights sum to 100), tracker state transitions (TRIGGERED → TP / STOPPED / EXPIRED) and storage round-trips.

## 🗄️ Storage choice

Plain JSON files committed to the repository (`data/`). Rationale: zero cost, zero infrastructure, fully transparent version history via git, atomic writes prevent corruption, and GitHub Pages serves them directly. History is capped at 500 records and the kline cache only keeps symbols with active setups.

## Alternatives & notes

- **Vercel / Cloudflare Pages**: you can deploy the `site/` folder instead of GitHub Pages if you prefer — but a separate scheduler would be needed to refresh `data/` (a cron GitHub Action can keep committing data regardless of where the site is hosted).
- Keep the repository **public** for free unlimited Actions minutes (a private repo with 96 runs/day will exceed the free allowance).

## ⚠️ Disclaimer

This software is provided for informational and educational purposes only. It is not financial advice, and nothing it outputs is a guarantee. Trading digital assets involves substantial risk of loss. You are solely responsible for your own trading decisions.
