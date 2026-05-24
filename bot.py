"""
roi'sbot — מעקב אוטומטי אחרי מניות + התראות Telegram
"""

import os
import asyncio
import logging
import json
from datetime import datetime, time as dtime
import pytz
import pandas as pd
import pandas_ta as ta
from anthropic import Anthropic
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockSnapshotRequest
from alpaca.data.timeframe import TimeFrame
from telegram import Bot
from telegram.constants import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ── הגדרות ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("roisbot")

TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ALPACA_API_KEY   = os.environ["ALPACA_API_KEY"]
ALPACA_SECRET    = os.environ["ALPACA_SECRET"]
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]

SCAN_INTERVAL_MIN = 10          # כל כמה דקות לסרוק
MAX_WATCHLIST     = 5           # מקסימום מניות במעקב
SCREENER_HOUR     = 9           # שעת סריקה בוקרית (ET)
SCREENER_MIN      = 35
ET = pytz.timezone("America/New_York")

# מניות ברירת מחדל אם Screener לא מצא מספיק
FALLBACK_STOCKS = ["TSLA", "NVDA", "AAPL", "MSFT", "AMZN"]

# רשימת מניות נפוצות לסריקה
SCAN_UNIVERSE = [
    "TSLA","NVDA","AAPL","MSFT","AMZN","META","GOOGL","AMD","NFLX",
    "PLTR","MSTR","COIN","SMCI","ARM","UBER","SNAP","HOOD","UAL","BA"
]

# ── State ────────────────────────────────────────────────────────────────────
watchlist: list[str] = []
last_signals: dict[str, str] = {}   # symbol -> last signal sent

# ── Clients ──────────────────────────────────────────────────────────────────
alpaca   = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET)
claude   = Anthropic(api_key=ANTHROPIC_KEY)
tg_bot   = Bot(token=TELEGRAM_TOKEN)


# ── Telegram helpers ─────────────────────────────────────────────────────────
async def send(text: str):
    try:
        await tg_bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        log.error(f"Telegram error: {e}")


# ── Market hours ─────────────────────────────────────────────────────────────
def market_is_open() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    return dtime(9, 30) <= now.time() <= dtime(16, 0)


# ── Data fetching ─────────────────────────────────────────────────────────────
def get_bars(symbol: str, limit: int = 60) -> pd.DataFrame | None:
    try:
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            limit=limit
        )
        bars = alpaca.get_stock_bars(req).df
        if bars.empty:
            return None
        if isinstance(bars.index, pd.MultiIndex):
            bars = bars.loc[symbol]
        return bars.reset_index()
    except Exception as e:
        log.error(f"get_bars {symbol}: {e}")
        return None


# ── Screener ──────────────────────────────────────────────────────────────────
async def run_screener():
    """בוחר 5 מניות חמות כל בוקר לפי נפח + תנועה + RSI"""
    global watchlist
    log.info("Running morning screener...")
    candidates = []

    try:
        req = StockSnapshotRequest(symbol_or_symbols=SCAN_UNIVERSE)
        snapshots = alpaca.get_stock_snapshot(req)

        for sym, snap in snapshots.items():
            try:
                day = snap.daily_bar
                prev = snap.previous_daily_bar
                if not day or not prev or prev.close == 0:
                    continue
                change_pct = abs((day.close - prev.close) / prev.close * 100)
                volume = day.volume
                candidates.append({
                    "symbol": sym,
                    "change_pct": change_pct,
                    "volume": volume,
                    "close": day.close,
                })
            except Exception:
                continue

        # מיון: תנועה גדולה + נפח גבוה
        candidates.sort(key=lambda x: x["change_pct"] * 0.6 + (x["volume"] / 1_000_000) * 0.4, reverse=True)
        new_list = [c["symbol"] for c in candidates[:MAX_WATCHLIST]]

        if len(new_list) < MAX_WATCHLIST:
            for s in FALLBACK_STOCKS:
                if s not in new_list:
                    new_list.append(s)
                if len(new_list) == MAX_WATCHLIST:
                    break

        watchlist = new_list
        last_signals.clear()

        lines = "\n".join([
            f"  {i+1}. <b>{s}</b> — {next((c for c in candidates if c['symbol']==s), {}).get('change_pct', 0):.1f}% תנועה"
            for i, s in enumerate(watchlist)
        ])
        await send(
            f"🌅 <b>roi'sbot — רשימת מעקב בוקרית</b>\n\n"
            f"{lines}\n\n"
            f"⏱ סריקה כל {SCAN_INTERVAL_MIN} דקות | שעות מסחר בלבד"
        )
        log.info(f"Watchlist: {watchlist}")

    except Exception as e:
        log.error(f"Screener error: {e}")
        if not watchlist:
            watchlist = FALLBACK_STOCKS.copy()
            await send(f"⚠️ Screener נכשל — עובד עם ברירת מחדל:\n{', '.join(watchlist)}")


# ── Technical Analysis ────────────────────────────────────────────────────────
def analyze_technical(df: pd.DataFrame) -> dict:
    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    vol   = df["volume"]

    ema8  = ta.ema(close, length=8).iloc[-1]
    ema21 = ta.ema(close, length=21).iloc[-1]
    ema50 = ta.ema(close, length=50).iloc[-1] if len(df) >= 50 else None
    rsi   = ta.rsi(close, length=14).iloc[-1]
    macd  = ta.macd(close)
    macd_val  = macd["MACD_12_26_9"].iloc[-1]
    macd_sig  = macd["MACDs_12_26_9"].iloc[-1]
    macd_hist = macd["MACDh_12_26_9"].iloc[-1]

    # ATR לסטופ
    atr = ta.atr(high, low, close, length=14).iloc[-1]

    current = close.iloc[-1]
    prev    = close.iloc[-2]
    change  = (current - prev) / prev * 100

    # קביעת כיוון
    bullish_signals = 0
    bearish_signals = 0

    if ema8 > ema21: bullish_signals += 1
    else: bearish_signals += 1

    if ema50 and current > ema50: bullish_signals += 1
    elif ema50: bearish_signals += 1

    if rsi > 55: bullish_signals += 1
    elif rsi < 45: bearish_signals += 1

    if macd_val > macd_sig: bullish_signals += 1
    else: bearish_signals += 1

    if macd_hist > 0: bullish_signals += 1
    else: bearish_signals += 1

    if bullish_signals >= 4:
        signal = "LONG"
    elif bearish_signals >= 4:
        signal = "SHORT"
    else:
        signal = "WAIT"

    return {
        "current_price": round(current, 2),
        "change_pct": round(change, 2),
        "ema8": round(ema8, 2),
        "ema21": round(ema21, 2),
        "ema50": round(ema50, 2) if ema50 else None,
        "rsi": round(rsi, 1),
        "macd_hist": round(macd_hist, 4),
        "atr": round(atr, 2),
        "signal": signal,
        "bullish": bullish_signals,
        "bearish": bearish_signals,
    }


# ── AI Analysis ───────────────────────────────────────────────────────────────
def analyze_ai(symbol: str, tech: dict) -> dict:
    prompt = f"""
נתח את המניה {symbol} על סמך הנתונים הטכניים הבאים ותן המלצה.
החזר JSON בלבד ללא backticks.

נתונים:
- מחיר נוכחי: {tech['current_price']}
- שינוי: {tech['change_pct']}%
- EMA8: {tech['ema8']}, EMA21: {tech['ema21']}, EMA50: {tech['ema50']}
- RSI(14): {tech['rsi']}
- MACD Histogram: {tech['macd_hist']}
- ATR: {tech['atr']}
- סיגנלים שוריים: {tech['bullish']}/5, דוביים: {tech['bearish']}/5

החזר:
{{
  "action": "LONG/SHORT/WAIT",
  "confidence": 0-100,
  "entry": "מחיר כניסה מומלץ",
  "stop_loss": "מחיר סטופ לוס",
  "target1": "יעד ראשון",
  "target2": "יעד שני",
  "risk_reward": "יחס R/R",
  "reason": "סיבה קצרה בעברית (משפט אחד)",
  "urgency": "מיידי/בעוד דקות/המתן לאישור"
}}
"""
    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.content[0].text.strip().replace("```json", "").replace("```", "")
        return json.loads(text)
    except Exception as e:
        log.error(f"AI analysis error: {e}")
        return {
            "action": tech["signal"],
            "confidence": 50,
            "entry": str(tech["current_price"]),
            "stop_loss": str(round(tech["current_price"] - tech["atr"] * 1.5, 2)),
            "target1": str(round(tech["current_price"] + tech["atr"] * 2, 2)),
            "target2": str(round(tech["current_price"] + tech["atr"] * 3.5, 2)),
            "risk_reward": "1:2",
            "reason": "ניתוח טכני בלבד (AI לא זמין)",
            "urgency": "המתן לאישור"
        }


# ── Format Alert ──────────────────────────────────────────────────────────────
def format_alert(symbol: str, tech: dict, ai: dict) -> str:
    action = ai["action"]
    emoji = {"LONG": "🟢", "SHORT": "🔴", "WAIT": "🟡"}.get(action, "⚪")
    action_he = {"LONG": "לונג ▲", "SHORT": "שורט ▼", "WAIT": "המתנה ◆"}.get(action, action)
    conf = ai.get("confidence", 0)
    conf_bar = "█" * (conf // 10) + "░" * (10 - conf // 10)

    now_et = datetime.now(ET).strftime("%H:%M ET")

    return (
        f"{emoji} <b>roi'sbot | {symbol}</b> | {now_et}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 פעולה: <b>{action_he}</b>\n"
        f"⚡ ביטחון: {conf}% {conf_bar}\n"
        f"⏱ עיתוי: {ai.get('urgency', '—')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 כניסה:    <b>{ai.get('entry', '—')}</b>\n"
        f"🛑 סטופ:     <b>{ai.get('stop_loss', '—')}</b>\n"
        f"🎯 יעד 1:   <b>{ai.get('target1', '—')}</b>\n"
        f"🎯 יעד 2:   <b>{ai.get('target2', '—')}</b>\n"
        f"📐 R/R:      <b>{ai.get('risk_reward', '—')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 RSI: {tech['rsi']} | EMA8/21: {tech['ema8']}/{tech['ema21']}\n"
        f"💬 {ai.get('reason', '')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <i>לצורך מידע בלבד — אינו המלצת השקעה</i>"
    )


# ── Main Scan ─────────────────────────────────────────────────────────────────
async def scan_all():
    if not market_is_open():
        log.info("Market closed — skipping scan")
        return

    if not watchlist:
        await run_screener()
        return

    log.info(f"Scanning: {watchlist}")

    for symbol in watchlist:
        try:
            df = get_bars(symbol, limit=60)
            if df is None or len(df) < 30:
                log.warning(f"{symbol}: not enough data")
                continue

            tech = analyze_technical(df)
            ai   = analyze_ai(symbol, tech)

            action = ai["action"]
            conf   = ai.get("confidence", 0)

            # שלח התראה רק אם:
            # 1. הסיגנל הוא LONG או SHORT (לא WAIT)
            # 2. ביטחון >= 65%
            # 3. הסיגנל השתנה מהפעם האחרונה
            prev_signal = last_signals.get(symbol)
            if action != "WAIT" and conf >= 65 and action != prev_signal:
                msg = format_alert(symbol, tech, ai)
                await send(msg)
                last_signals[symbol] = action
                log.info(f"Alert sent: {symbol} {action} {conf}%")
            else:
                log.info(f"{symbol}: {action} {conf}% — no alert (prev: {prev_signal})")

        except Exception as e:
            log.error(f"Scan error {symbol}: {e}")

        await asyncio.sleep(1)  # למנוע rate limiting


# ── Startup ───────────────────────────────────────────────────────────────────
async def on_startup():
    await send(
        "🤖 <b>roi'sbot מתחיל לעבוד!</b>\n\n"
        f"⏱ סריקה כל {SCAN_INTERVAL_MIN} דקות\n"
        "📊 ניתוח: אינדיקטורים טכניים + AI\n"
        "📱 התראות רק על סיגנלים חזקים (ביטחון ≥65%)\n\n"
        "ממתין לשעות מסחר (9:30–16:00 ET)..."
    )
    # אם שוק פתוח עכשיו — הרץ screener מיד
    if market_is_open():
        await run_screener()
        await scan_all()


# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    scheduler = AsyncIOScheduler(timezone=ET)

    # Screener בוקרי — 9:35 ET
    scheduler.add_job(run_screener, "cron",
                      hour=SCREENER_HOUR, minute=SCREENER_MIN,
                      day_of_week="mon-fri")

    # סריקה כל X דקות
    scheduler.add_job(scan_all, "interval",
                      minutes=SCAN_INTERVAL_MIN)

    scheduler.start()
    await on_startup()

    # הרץ לנצח
    while True:
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
