"""Подмены без сети: эмбеддинги по буквенным триграммам и модель, которая запоминает свой промпт."""
from __future__ import annotations

import hashlib

import numpy as np

DIM = 256


class FakeEmbedder:
    """Похожие тексты дают похожие векторы: счётчик триграмм, разложенный по 256 корзинам."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        out = np.zeros((len(texts), DIM), dtype="float32")
        for row, text in enumerate(texts):
            t = " " + text.lower().replace("ё", "е") + " "
            for i in range(len(t) - 2):
                h = int(hashlib.md5(t[i:i + 3].encode()).hexdigest()[:8], 16)
                out[row, h % DIM] += 1.0
        return out


class FakeChat:
    """Запоминает промпты. На просьбу переписать уточнение отвечает rewrite_reply (или самим вопросом)."""

    model = "fake-model"

    def __init__(self, reply: str = "Ответ по базе. Напишите Александру, если нужно."):
        self.reply = reply
        self.rewrite_reply = ""
        self.calls: list[list[dict[str, str]]] = []  # только вызовы для ответа посетителю
        self.rewrite_calls: list[list[dict[str, str]]] = []
        self.rewrite_quick: list[bool] = []
        self.delay = 0.0  # секунд «думает» модель над ответом
        self.fail = False
        self.fail_rewrite = False

    def complete(self, messages, max_tokens=None, quick=False):
        from backend.assistant import REWRITE_PROMPT

        if messages[0]["content"] == REWRITE_PROMPT:
            self.rewrite_calls.append(messages)
            self.rewrite_quick.append(quick)
            if self.fail_rewrite:
                raise TimeoutError("модель не ответила")
            last = messages[-1]["content"].rsplit("Последний вопрос посетителя: ", 1)[-1]
            return self.rewrite_reply or last, 40, 10
        self.calls.append(messages)
        if self.fail:
            raise TimeoutError("модель не ответила")
        if self.delay:
            import time

            time.sleep(self.delay)
        return self.reply, 120, 30


ITEMS = [
    {"question": "Сколько стоит настроить помощника?",
     "answer": "Стоимость и срок запуска Александр называет после бесплатного разбора задачи.", "source": "faqs.json#1"},
    {"question": "Как начать работу?",
     "answer": "Опишите в двух-трёх предложениях, какая работа в агентстве повторяется, и запишитесь на бесплатный разбор.",
     "source": "faqs.json#2"},
    {"question": "Кейсы: бот подбирает квартиры по голосовому сообщению",
     "answer": "Telegram-бот понимает запрос текстом или голосом и отвечает карточками квартир с сайта.",
     "source": "03-keisy.md#Кейс 3"},
]
