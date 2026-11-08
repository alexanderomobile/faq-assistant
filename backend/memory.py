"""Память диалога: последние реплики каждого посетителя в памяти процесса.

Урок требует, чтобы ассистент помнил историю: иначе на «а сколько это стоит?» он не поймёт, о чём речь.
Код урока историю не хранил. Здесь у диалога есть идентификатор (его создаёт виджет в браузере и хранит
во вкладке), ассистент помнит последние history_turns пар «вопрос–ответ», молчащий дольше ttl диалог забывается,
а при переполнении вытесняется самый старый. После перезапуска службы память пустая — это осознанно:
долговременное хранение переписки не нужно, журнал хранит только обезличенные записи.

Истёкшие диалоги удаляются при каждом обращении и раз в несколько минут фоновой чисткой (sweep в app.py):
на тихом сайте последние реплики не должны лежать в памяти дольше обещанного.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field


@dataclass
class _Dialog:
    turns: deque[tuple[str, str]]
    touched: float = field(default_factory=time.monotonic)


class DialogMemory:
    def __init__(self, turns: int, ttl_seconds: float, max_sessions: int, clock=time.monotonic):
        self.turns = turns
        self.ttl = ttl_seconds
        self.max_sessions = max_sessions
        self.clock = clock
        self._dialogs: OrderedDict[str, _Dialog] = OrderedDict()
        # Когда диалог сбрасывали «Новым диалогом»: ответ, начатый раньше сброса, в память не попадает.
        self._resets: dict[str, float] = {}
        self._lock = threading.Lock()

    def now(self) -> float:
        return self.clock()

    def _drop_expired(self, now: float) -> None:
        for key in [k for k, d in self._dialogs.items() if now - d.touched > self.ttl]:
            del self._dialogs[key]
        for key in [k for k, t in self._resets.items() if now - t > self.ttl]:
            del self._resets[key]

    def history(self, session_id: str) -> list[tuple[str, str]]:
        """Пары (вопрос, ответ) от старых к новым."""
        with self._lock:
            now = self.clock()
            self._drop_expired(now)
            dialog = self._dialogs.get(session_id)
            return list(dialog.turns) if dialog else []

    def add(self, session_id: str, question: str, answer: str, started: float | None = None) -> None:
        """started — когда начали готовить ответ; если после этого диалог сбросили, ответ не запоминается."""
        with self._lock:
            now = self.clock()
            self._drop_expired(now)
            reset_at = self._resets.get(session_id)
            if started is not None and reset_at is not None and reset_at >= started:
                return
            dialog = self._dialogs.get(session_id)
            if dialog is None:
                dialog = _Dialog(deque(maxlen=self.turns))
                self._dialogs[session_id] = dialog
            dialog.turns.append((question, answer))
            dialog.touched = now
            self._dialogs.move_to_end(session_id)
            while len(self._dialogs) > self.max_sessions:
                self._dialogs.popitem(last=False)

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._dialogs.pop(session_id, None)
            self._resets[session_id] = self.clock()
            while len(self._resets) > self.max_sessions:  # меток не больше, чем диалогов
                self._resets.pop(next(iter(self._resets)))

    def sweep(self) -> None:
        """Удалить истёкшие диалоги и метки сброса, даже если никто не пишет."""
        with self._lock:
            self._drop_expired(self.clock())

    def __len__(self) -> int:
        with self._lock:
            return len(self._dialogs)
