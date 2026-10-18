# Hermes Telegram Bot — Python
# ============================

import asyncio
import logging
import os
import uuid
from datetime import datetime

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from google import genai

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY") or os.getenv("HERMES_API_KEY") or ""
).strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
HERMES_ADMIN_CHAT_ID = os.getenv("HERMES_ADMIN_CHAT_ID", "").strip()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("hermes-bot")

# ---------------------------------------------------------------------------
# Clients / scheduler
# ---------------------------------------------------------------------------
genai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# In-memory async scheduler (jobs are lost on restart — by design for free tier)
scheduler = AsyncIOScheduler()

# Global application reference, set in main()
app: Application = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Model helper
# ---------------------------------------------------------------------------
async def ask_model(prompt: str) -> str:
    """Send a prompt to Gemini and return the text response."""
    if not genai_client:
        return "⚠️ GEMINI_API_KEY is not configured on the server."
    try:
        response = await genai_client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        text = getattr(response, "text", None)
        return (text or "").strip() or "(empty response)"
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Model call failed")
        return f"❌ Model error: {exc}"


# ---------------------------------------------------------------------------
# Scheduled job runner
# ---------------------------------------------------------------------------
async def run_scheduled_job(chat_id: int, prompt: str, job_id: str) -> None:
    """Execute a scheduled prompt and deliver the result to the Telegram chat."""
    logger.info("Running scheduled job %s for chat %s", job_id, chat_id)
    answer = await ask_model(prompt)
    message = (
        f"⏰ *Scheduled task* `{job_id}`\n"
        f"📝 Prompt: {prompt}\n\n"
        f"🤖 Result:\n{answer}"
    )
    try:
        await app.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Failed to send scheduled result: %s", exc)
        # Fallback without markdown in case of parse errors
        try:
            await app.bot.send_message(chat_id=chat_id, text=message)
        except Exception:  # pylint: disable=broad-except
            logger.exception("Fallback send failed")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------
HELP_TEXT = (
    "🤖 *Hermes Telegram Bot*\n\n"
    "Commands:\n"
    "/start — welcome message\n"
    "/help — show this help\n"
    "/run <prompt> — ask the model right now\n"
    "/schedule interval <minutes> <prompt> — run every N minutes\n"
    "/schedule daily <HH:MM> <prompt> — run every day at HH:MM (24h)\n"
    "/schedule cron <min> <hour> <day> <month> <weekday> <prompt> — cron schedule\n"
    "/jobs — list your scheduled jobs\n"
    "/cancel <job_id> — cancel a scheduled job\n\n"
    "Examples:\n"
    "`/schedule interval 30 Summarize the latest AI news`\n"
    "`/schedule daily 09:00 Give me a morning briefing`\n"
    "`/schedule cron 0 9 * * 1-5 Weekday morning report`\n"
)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Hello! I'm Hermes, your AI assistant.\n"
        "Use /help to see available commands.",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def run_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    prompt = " ".join(context.args).strip()
    if not prompt:
        await update.message.reply_text("Usage: /run <prompt>")
        return
    await update.message.reply_text("⏳ Thinking...")
    answer = await ask_model(prompt)
    await update.message.reply_text(answer)


async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    chat_id = update.effective_chat.id

    if not args:
        await update.message.reply_text(
            "Usage:\n"
            "/schedule interval <minutes> <prompt>\n"
            "/schedule daily <HH:MM> <prompt>\n"
            "/schedule cron <min> <hour> <day> <month> <weekday> <prompt>"
        )
        return

    mode = args[0].lower()

    try:
        if mode in ("interval", "every"):
            if len(args) < 3:
                raise ValueError("need minutes and prompt")
            minutes = int(args[1])
            if minutes < 1:
                raise ValueError("minutes must be >= 1")
            prompt = " ".join(args[2:]).strip()
            if not prompt:
                raise ValueError("prompt is empty")

            job_id = f"job_{uuid.uuid4().hex[:8]}"
            scheduler.add_job(
                run_scheduled_job,
                trigger=IntervalTrigger(minutes=minutes),
                args=[chat_id, prompt, job_id],
                id=job_id,
                replace_existing=True,
                misfire_grace_time=60,
            )
            await update.message.reply_text(
                f"✅ Scheduled every {minutes} minute(s).\nJob ID: `{job_id}`",
                parse_mode="Markdown",
            )

        elif mode == "daily":
            if len(args) < 3:
                raise ValueError("need time and prompt")
            time_str = args[1]
            hh, mm = time_str.split(":")
            hour, minute = int(hh), int(mm)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError("invalid time")
            prompt = " ".join(args[2:]).strip()
            if not prompt:
                raise ValueError("prompt is empty")

            job_id = f"job_{uuid.uuid4().hex[:8]}"
            scheduler.add_job(
                run_scheduled_job,
                trigger=CronTrigger(hour=hour, minute=minute),
                args=[chat_id, prompt, job_id],
                id=job_id,
                replace_existing=True,
                misfire_grace_time=120,
            )
            await update.message.reply_text(
                f"✅ Scheduled daily at {hour:02d}:{minute:02d}.\nJob ID: `{job_id}`",
                parse_mode="Markdown",
            )

        elif mode == "cron":
            # /schedule cron <min> <hour> <day> <month> <weekday> <prompt...>
            if len(args) < 8:
                raise ValueError("need 5 cron fields + prompt")
            cron_fields = args[1:6]
            prompt = " ".join(args[6:]).strip()
            if not prompt:
                raise ValueError("prompt is empty")

            trigger = CronTrigger.from_crontab(" ".join(cron_fields))
            job_id = f"job_{uuid.uuid4().hex[:8]}"
            scheduler.add_job(
                run_scheduled_job,
                trigger=trigger,
                args=[chat_id, prompt, job_id],
                id=job_id,
                replace_existing=True,
                misfire_grace_time=120,
            )
            await update.message.reply_text(
                f"✅ Scheduled with cron `{ ' '.join(cron_fields) }`.\nJob ID: `{job_id}`",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "Unknown mode. Use: interval, daily, or cron."
            )
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Schedule failed")
        await update.message.reply_text(f"❌ Failed to schedule: {exc}")


async def jobs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_jobs = [
        j for j in scheduler.get_jobs()
        if isinstance(j.args, (list, tuple)) and len(j.args) >= 1 and j.args[0] == chat_id
    ]
    if not user_jobs:
        await update.message.reply_text("You have no scheduled jobs.")
        return

    lines = ["📋 *Your scheduled jobs:*"]
    for j in user_jobs:
        prompt = j.args[1] if len(j.args) > 1 else "?"
        next_run = j.next_run_time.strftime("%Y-%m-%d %H:%M:%S") if j.next_run_time else "n/a"
        lines.append(f"• `{j.id}` — next: {next_run}\n  prompt: {prompt}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /cancel <job_id>")
        return
    job_id = context.args[0]
    try:
        scheduler.remove_job(job_id)
        await update.message.reply_text(f"🗑️ Cancelled job `{job_id}`.", parse_mode="Markdown")
    except Exception as exc:  # pylint: disable=broad-except
        await update.message.reply_text(f"❌ Could not cancel: {exc}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled exception: %s", context.error)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
async def post_init(application: Application) -> None:
    scheduler.start()
    logger.info("Scheduler started (in-memory).")

    # Optional daily status report to admin chat
    if HERMES_ADMIN_CHAT_ID:
        try:
            admin_chat = int(HERMES_ADMIN_CHAT_ID)
            async def daily_status():
                await application.bot.send_message(
                    chat_id=admin_chat,
                    text=f"✅ Hermes bot alive — {datetime.utcnow().isoformat()}Z",
                )
            scheduler.add_job(
                daily_status,
                trigger=CronTrigger(hour=0, minute=0),
                id="daily_status",
                replace_existing=True,
            )
        except ValueError:
            logger.warning("Invalid HERMES_ADMIN_CHAT_ID: %s", HERMES_ADMIN_CHAT_ID)


def main() -> None:
    global app
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("run", run_cmd))
    app.add_handler(CommandHandler("schedule", schedule_cmd))
    app.add_handler(CommandHandler("jobs", jobs_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_error_handler(error_handler)

    logger.info("Starting Hermes bot with polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
