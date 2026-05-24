# roi'sbot 🤖

בוט מסחר יומי אוטומטי — מעקב אחרי 5 מניות + התראות Telegram

## איך מפעילים על Railway

### 1. צור Repository חדש ב-GitHub
- פתח github.com
- לחץ New Repository
- שם: `roisbot`
- העלה את שני הקבצים: `bot.py`, `requirements.txt`, `railway.toml`

### 2. חבר ל-Railway
- פתח railway.app
- לחץ New Project → Deploy from GitHub
- בחר את `roisbot`

### 3. הוסף Environment Variables
ב-Railway לחץ על הפרויקט → Variables → Add:

| שם | ערך |
|---|---|
| TELEGRAM_TOKEN | הטוקן מ-BotFather |
| TELEGRAM_CHAT_ID | 7349213874 |
| ALPACA_API_KEY | המפתח מ-Alpaca |
| ALPACA_SECRET | הסוד מ-Alpaca |
| ANTHROPIC_API_KEY | המפתח מ-Anthropic |

### 4. Deploy
Railway יפעיל את הבוט אוטומטית.
תקבל הודעה בטלגרם "roi'sbot מתחיל לעבוד!"

## מה הבוט עושה

- **9:35 ET** — בוחר 5 מניות חמות לפי תנועה + נפח
- **כל 10 דקות** — סורק כל מניה
- **ניתוח כפול** — אינדיקטורים טכניים (EMA/RSI/MACD) + Claude AI
- **התראה** נשלחת רק כש:
  - יש סיגנל LONG או SHORT (לא WAIT)
  - ביטחון ≥ 65%
  - הסיגנל השתנה מהפעם האחרונה

## פורמט ההתראה

```
🟢 roi'sbot | TSLA | 10:42 ET
━━━━━━━━━━━━━━━━━━━━
📊 פעולה: לונג ▲
⚡ ביטחון: 78% ████████░░
⏱ עיתוי: מיידי
━━━━━━━━━━━━━━━━━━━━
💰 כניסה:    427.50
🛑 סטופ:     424.00
🎯 יעד 1:   432.00
🎯 יעד 2:   436.50
📐 R/R:      1:1.8
```
