# ◈ Domain Tracker — Telegram Dashboard

Extract and visualize domain tracking data from your Telegram groups.

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Get Telegram API credentials
1. Go to **https://my.telegram.org**
2. Log in with your Telegram phone number
3. Click **"API development tools"**
4. Create an app — you'll get an `api_id` and `api_hash`

### 3. Run the app
```bash
python app.py
```

### 4. Open the dashboard
Go to **http://localhost:5000** in your browser.

---

## First-Time Setup Flow

1. **Setup page** — Enter your `api_id` and `api_hash`
2. **Login page** — Enter your phone number (with country code, e.g. `+212...`)
3. **Verify page** — Enter the 5-digit code sent to your Telegram app
4. **(Optional) 2FA** — Enter your cloud password if you have 2FA enabled
5. **Dashboard** — Select your tracking group, click **Fetch**, done!

---

## Message Format Supported

The parser reads messages in this format:

```
   7003 (IN)   6529 (OUT)
s_cmh2_5428   7003   6529
plaudioverhuur.nl
```

It extracts:
- **Domain** → `plaudioverhuur.nl`
- **IN** → `7003`
- **OUT** → `6529`
- **Date** → message timestamp

---

## Files Created at Runtime

| File | Purpose |
|------|---------|
| `config.json` | Stores your api_id, api_hash, phone |
| `telegram_session.session` | Telethon auth session (keep safe!) |

---

## Notes

- Your session is saved locally — you won't need to log in again after the first time.
- The `telegram_session.session` file contains your auth token. Don't share it.
- You can fetch up to 3000 messages at once (adjust the limit in the dashboard).
- Use **Export CSV** to download data for further analysis.
