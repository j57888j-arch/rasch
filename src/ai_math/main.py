from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text

from .config import settings
from .db import SessionLocal, dispose_db, init_db
from .handlers import router, recover_pending_grading

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("ai_math")

bot: Bot | None = None
dp: Dispatcher | None = None
redis_client: Redis | None = None
polling_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot, dp, redis_client, polling_task
    # Render datastores may take a short time to become reachable during deploy/restart.
    last_error = None
    for attempt in range(6):
        try:
            await init_db()
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            logger.exception("Database initialization attempt %s failed", attempt + 1)
            await asyncio.sleep(min(2 + attempt, 8))
    if last_error is not None:
        raise RuntimeError("Database initialization failed after retries") from last_error
    storage = MemoryStorage()
    if settings.redis_url:
        try:
            redis_client = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=3)
            await redis_client.ping()
            storage = RedisStorage.from_url(settings.redis_url)
            logger.info("Redis connected; FSM uses Redis storage.")
        except Exception:
            logger.exception("Redis unavailable; falling back to in-memory FSM. Main data remains in Postgres.")
            if redis_client:
                await redis_client.aclose()
            redis_client = None
            storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)
    if settings.bot_token:
        bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        if settings.use_webhook and settings.public_base_url:
            webhook_url = settings.public_base_url.rstrip("/") + "/telegram/webhook"
            await bot.set_webhook(
                webhook_url, secret_token=settings.webhook_secret or None,
                allowed_updates=dp.resolve_used_update_types(), drop_pending_updates=False,
            )
            logger.info("Webhook configured: %s", webhook_url)
        elif not settings.use_webhook:
            await bot.delete_webhook(drop_pending_updates=False)
            polling_task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
            logger.info("Telegram polling started.")
        asyncio.create_task(recover_pending_grading(bot))
    else:
        logger.warning("BOT_TOKEN is empty. Health endpoints are available, but bot updates are disabled.")
    yield
    if dp:
        try:
            await dp.storage.close()
        except Exception:
            logger.exception("FSM storage close failed")
    if bot:
        try:
            if settings.use_webhook:
                await bot.delete_webhook(drop_pending_updates=False)
            await bot.session.close()
        except Exception:
            logger.exception("Bot shutdown failed")
    if polling_task:
        polling_task.cancel()
        with suppress(asyncio.CancelledError):
            await polling_task
    if redis_client:
        await redis_client.aclose()
    await dispose_db()


app = FastAPI(title="AI MATH Exam Bot", version="0.1.0", lifespan=lifespan)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "AI MATH", "status": "running", "health": "/health/ready"}


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    checks = {"database": "unknown", "redis": "optional"}
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        logger.exception("Database readiness check failed")
        checks["database"] = "error"
    if redis_client:
        try:
            await redis_client.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "degraded"
    status = 200 if checks["database"] == "ok" else 503
    return JSONResponse({"status": "ready" if status == 200 else "not_ready", "checks": checks}, status_code=status)


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    if not bot or not dp:
        raise HTTPException(status_code=503, detail="Bot is not configured")
    if settings.webhook_secret and x_telegram_bot_api_secret_token != settings.webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    try:
        update_data = await request.json()
        from aiogram.types import Update
        update = Update.model_validate(update_data, context={"bot": bot})
        await dp.feed_update(bot, update)
        return {"ok": True}
    except Exception as exc:
        logger.exception("Webhook update processing failed")
        raise HTTPException(status_code=400, detail="Update processing failed") from exc
