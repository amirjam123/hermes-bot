# Hermes Telegram Bot (Python)

Autonomous Telegram AI agent powered by Google Gemini, with in-process cron/interval scheduling, running 24/7 on Render as a Background Worker.

## Files
- `bot.py` — main bot + APScheduler
- `requirements.txt` — Python dependencies
- `render.yaml` — Render Blueprint (Background Worker)
- `.env.example` — template for local secrets

## Local run
```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env  # then fill values
python bot.py
```

## Scheduling usage (in Telegram)
- `/schedule interval 30 Summarize the latest AI news`
- `/schedule daily 09:00 Give me a morning briefing`
- `/schedule cron 0 9 * * 1-5 Weekday morning report`
- `/jobs` — list jobs
- `/cancel <job_id>` — cancel

Jobs live in memory and reset when the worker restarts.
