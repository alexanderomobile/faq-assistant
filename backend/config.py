"""Настройки ассистента. Секреты и пути читаются из окружения: `.env` в корне проекта или /etc/faq-assistant.env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else default


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    chat_model: str
    embed_model: str
    data_dir: Path
    index_dir: Path
    state_dir: Path
    top_k: int
    min_similarity: float
    history_turns: int
    memory_ttl_minutes: int
    max_sessions: int
    max_message_chars: int
    rate_per_minute: int
    rate_per_day: int
    daily_cap: int
    retention_days: int
    user_hash_salt: str
    temperature: float
    max_tokens: int
    allowed_origins: tuple[str, ...]
    public_contacts: tuple[str, ...]
    rewrite_followups: bool = True  # уточнение в диалоге переписывается в самостоятельный вопрос для поиска

    @property
    def index_path(self) -> Path:
        return self.index_dir / "faiss.index"

    @property
    def meta_path(self) -> Path:
        return self.index_dir / "meta.json"

    @property
    def journal_path(self) -> Path:
        return self.state_dir / "journal.db"


def load_settings() -> Settings:
    return Settings(
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        chat_model=os.environ.get("CHAT_MODEL", "gpt-4.1-mini"),
        # Было text-embedding-3-small. На первом наборе вопросов нужный раздел попадал в первые 6 найденных
        # в 42 случаях из 51, с 3-large — в 46 (eval/retrieval.py). Смена модели требует пересборки индекса.
        embed_model=os.environ.get("EMBED_MODEL", "text-embedding-3-large"),
        data_dir=_path("DATA_DIR", BASE_DIR / "data"),
        index_dir=_path("INDEX_DIR", BASE_DIR / "data" / "index"),
        state_dir=_path("STATE_DIR", BASE_DIR / "var"),
        # Было 4. При 4 фрагментах помощник в 6 ответах из 60 говорил «в материалах этого нет», хотя ответ
        # в базе был: нужный раздел оказывался пятым-восьмым. 6 фрагментов — около 600 токенов промпта сверху.
        top_k=_int("TOP_K", 6),
        # Порог косинусной близости: ниже — фрагмент считается не относящимся к вопросу. Проверен и для 3-large:
        # у вопросов, ответ на которые есть в базе, лучшая оценка не ниже 0,316 (eval/retrieval.py).
        min_similarity=_float("MIN_SIMILARITY", 0.30),
        # Память: сколько последних пар «вопрос–ответ» помнит ассистент и сколько живёт молчащий диалог.
        history_turns=_int("HISTORY_TURNS", 3),
        memory_ttl_minutes=_int("MEMORY_TTL_MINUTES", 30),
        max_sessions=_int("MAX_SESSIONS", 2000),
        max_message_chars=_int("MAX_MESSAGE_CHARS", 1000),
        # Лимиты на одного посетителя и общий суточный потолок — защита ключа OpenAI от расхода.
        rate_per_minute=_int("RATE_PER_MINUTE", 8),
        rate_per_day=_int("RATE_PER_DAY", 80),
        daily_cap=_int("DAILY_CAP", 1500),
        retention_days=_int("RETENTION_DAYS", 90),
        user_hash_salt=os.environ.get("USER_HASH_SALT", ""),
        temperature=_float("TEMPERATURE", 0.2),
        max_tokens=_int("MAX_TOKENS", 400),
        allowed_origins=tuple(o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()),
        public_contacts=("@alexandero_optimum", "t.me/alexandero_optimum", "https://t.me/alexandero_optimum",
                         "alexandero.mobile@gmail.com"),
        rewrite_followups=_flag("REWRITE_FOLLOWUPS", True),
    )
