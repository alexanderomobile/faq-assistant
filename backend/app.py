"""FastAPI-сервер FAQ-ассистента.

    uvicorn backend.app:app --host 127.0.0.1 --port 8301

POST /chat   {"message": "...", "session_id": "..."} → {"answer": "...", "status": "...", "sources": [...]}
POST /reset  {"session_id": "..."} — забыть диалог («Новый диалог» в виджете)
GET  /health — состояние: элементов в базе, версия базы, диалогов в памяти

На сайте сервер слушает только 127.0.0.1: виджет ходит на /api/chat сайта, а сайт пересылает запрос сюда
с отпечатком адреса посетителя в заголовке X-Client-Key. Отдельная страница frontend/index.html обращается
к серверу напрямую — для неё разрешённые адреса задаются в ALLOWED_ORIGINS.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .assistant import Assistant, clean_question
from .config import Settings, load_settings

log = logging.getLogger("faq.app")

SESSION_PATTERN = r"^[A-Za-z0-9_-]{16,64}$"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str = Field(pattern=SESSION_PATTERN)


class ResetRequest(BaseModel):
    session_id: str = Field(pattern=SESSION_PATTERN)


class ChatResponse(BaseModel):
    answer: str
    status: str
    sources: list[str]


class RateLimiter:
    """Скользящие окна: не больше per_minute запросов в минуту и per_day в сутки с одного ключа,
    плюс общий суточный потолок на всех (защита ключа OpenAI от расхода).

    Ключ — отпечаток адреса посетителя от сайта. Он хранится только в памяти и не дольше суток после
    последнего запроса: sweep удаляет старые ключи, его раз в несколько минут вызывает фоновая чистка.
    """

    def __init__(self, per_minute: int, per_day: int, daily_cap: int, clock=time.monotonic):
        self.per_minute = per_minute
        self.per_day = per_day
        self.daily_cap = daily_cap
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._rejection_logged: dict[str, float] = {}
        self._day = date.today()
        self._today = 0
        self._lock = threading.Lock()

    def check(self, key: str) -> str | None:
        """None — можно; иначе причина отказа."""
        with self._lock:
            now = self.clock()
            if date.today() != self._day:
                self._day, self._today = date.today(), 0
            if self._today >= self.daily_cap:
                return "daily_cap"
            hits = self._hits.get(key) or deque()
            while hits and now - hits[0] > 86400:
                hits.popleft()
            if len(hits) >= self.per_day:
                return "per_day"
            if sum(1 for t in hits if now - t <= 60) >= self.per_minute:
                return "per_minute"
            hits.append(now)
            self._hits[key] = hits
            self._today += 1
            return None

    def should_log_rejection(self, key: str) -> bool:
        """Отказ по лимиту пишется в журнал не чаще раза в минуту на ключ: поток запросов не раздует журнал."""
        with self._lock:
            now = self.clock()
            if now - self._rejection_logged.get(key, float("-inf")) < 60:
                return False
            self._rejection_logged[key] = now
            return True

    def sweep(self) -> None:
        """Забыть ключи, по которым не было запросов больше суток."""
        with self._lock:
            now = self.clock()
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] > 86400]:
                del self._hits[k]
            for k in [k for k, t in self._rejection_logged.items() if now - t > 60]:
                del self._rejection_logged[k]

    def __len__(self) -> int:
        with self._lock:
            return len(self._hits)


LIMIT_TEXT = {
    "per_minute": "Слишком много сообщений подряд. Подождите минуту.",
    "per_day": "На сегодня лимит вопросов исчерпан. Напишите Александру в Telegram @alexandero_optimum.",
    "daily_cap": "Помощник сейчас перегружен. Напишите Александру в Telegram @alexandero_optimum.",
}


SWEEP_SECONDS = 300  # как часто фоновая чистка забывает старые отпечатки адресов и истёкшие диалоги


def create_app(assistant: Assistant | None = None, settings: Settings | None = None,
               sweep_seconds: float = SWEEP_SECONDS) -> FastAPI:
    settings = settings or load_settings()
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        def sweeper() -> None:
            while not stop.wait(sweep_seconds):
                limiter.sweep()
                if holder:
                    holder[0].memory.sweep()

        thread = threading.Thread(target=sweeper, name="faq-sweeper", daemon=True)
        thread.start()
        yield
        stop.set()

    app = FastAPI(title="FAQ-ассистент", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    if settings.allowed_origins:
        # Только для отдельной демо-страницы; виджет на сайте ходит через сам сайт и CORS не нужен.
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_credentials=False,
                           allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
    limiter = RateLimiter(settings.rate_per_minute, settings.rate_per_day, settings.daily_cap)
    holder: list[Assistant] = [assistant] if assistant is not None else []
    last_cleanup = [0.0]

    def get_assistant() -> Assistant:
        if not holder:
            from .factory import build_assistant
            holder.append(build_assistant(settings))
        return holder[0]

    def maybe_cleanup(a: Assistant) -> None:
        now = time.monotonic()
        if a.journal is not None and now - last_cleanup[0] > 3600:
            last_cleanup[0] = now
            removed = a.journal.cleanup(settings.retention_days)
            if removed:
                log.info("журнал: удалено записей старше %s дней: %s", settings.retention_days, removed)

    @app.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest, request: Request):  # обычная функция: FastAPI выполнит её в пуле потоков
        a = get_assistant()
        key = request.headers.get("X-Client-Key") or (request.client.host if request.client else "unknown")
        # Сначала лимит: любой запрос, даже пустой или слишком длинный, расходует попытку посетителя,
        # поэтому поток мусорных запросов не пишет в журнал больше, чем позволяет лимит.
        reason = limiter.check(key)
        if reason:
            if limiter.should_log_rejection(key):
                a.reject(req.session_id, reason)
            return JSONResponse(status_code=429, content={"detail": LIMIT_TEXT[reason]})
        message = req.message.strip()
        if not clean_question(message):  # пусто или одни служебные теги <вопрос>
            a.reject(req.session_id, "empty")
            raise HTTPException(status_code=400, detail="Пустое сообщение")
        if len(message) > settings.max_message_chars:
            a.reject(req.session_id, "too_long")
            raise HTTPException(status_code=413,
                                detail=f"Сообщение длиннее {settings.max_message_chars} знаков, сократите его")
        maybe_cleanup(a)
        try:
            reply = a.answer(req.session_id, message)
        except Exception:
            log.exception("ошибка при ответе")
            return JSONResponse(status_code=503, content={
                "detail": "Помощник временно недоступен. Напишите Александру в Telegram @alexandero_optimum."})
        log.info("chat status=%s history=%s top=%s ms=%s sources=%s", reply.status, reply.history_turns,
                 reply.top_score, reply.timings.get("total_ms"), ",".join(reply.sources))
        return ChatResponse(answer=reply.answer, status=reply.status, sources=reply.sources)

    @app.post("/reset")
    def reset(req: ResetRequest):
        get_assistant().memory.reset(req.session_id)
        return {"status": "ok"}

    @app.get("/health")
    def health():
        a = get_assistant()
        return {"status": "ok", "items": len(a.items), "kb_version": a.kb_version, "dialogs": len(a.memory),
                "model": a.chat.model}

    return app


app = create_app()
