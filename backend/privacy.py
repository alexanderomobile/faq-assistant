"""Обезличивание данных: код разговора и вычистка контактов из текста.

Урок требует, чтобы логи были анонимными: никаких ФИО, телефонов и паспортных
данных. Поэтому в журнал идёт не идентификатор разговора из браузера, а его HMAC
с секретной солью, а из текста вопроса (и из ответа перед записью) вырезаются почта,
ники и ссылки на Telegram, телефоны и длинные номера, номера документов и имя после
«меня зовут». Вопрос вычищается ещё до отправки в OpenAI: модели номер телефона не нужен.

Ограничение: имя, написанное без подсказки («Иван Петров, перезвоните»), правилами
не распознать — такие случаи покрывает срок хранения журнала.
"""

import hashlib
import hmac
import re
from collections.abc import Iterable

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_TG_LINK = re.compile(r"(?i)(?:https?://)?(?:t\.me|telegram\.me)/[A-Za-z0-9_+]{4,}")
# Ник после кириллицы («тг@ivan_petrov») тоже ник; после латиницы и точки это часть адреса почты.
_MENTION = re.compile(r"(?<![A-Za-z0-9_.])@[A-Za-z][A-Za-z0-9_]{3,}")
# Паспорт серия/номер: «4510 123456», «45 10 № 123456», «серия 4510 номер 123456».
_DOCUMENT = re.compile(r"(?i)(?:паспорт\w*|серия)[^\d\n]{0,15}\d{2}\s?\d{2}[^\d\n]{0,12}\d{6}|\d{4}\s*№\s*\d{6}")
# Имя только после явной подсказки и только с заглавной буквы (флаг (?i) здесь сломал бы проверку заглавной),
# кириллицей или латиницей.
_NAME = re.compile(r"([Мм]еня зовут|[Мм]о[её] имя)\s+[А-ЯЁA-Z][а-яёa-z]+(?:\s+[А-ЯЁA-Z][а-яёa-z]+){0,2}")
# 7+ цифр с пробелами, дефисами, точками, скобками и плюсом: телефоны («8.900.123.45.67»), карты, счета.
_LONG_NUMBER = re.compile(r"\+?\d[\d\s().\-]{5,}\d")
# Дата в начале такого числа («08.11.2026», «08.11.2026 12») — не номер, если после неё мало цифр.
_DATE = re.compile(r"\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})")
# Суммы с разрядами («1 500 000 рублей») и годы («2019-2026») номерами не считаются.
_AMOUNT_AFTER = re.compile(r"\s*(?:руб|₽|\$|usd|долл|евро|eur|€|млн|тыс|у\.?\s?е)", re.IGNORECASE)
_GROUPED = re.compile(r"\d{1,3}(?:[  .]\d{3})+")
_YEARS = re.compile(r"(?:(?:19|20)\d{2})(?:\s*[-–]\s*|\s+)(?:(?:19|20)\d{2})(?:(?:\s*[-–]\s*|\s+)(?:19|20)\d{2})*")


def user_hash(salt: str, raw_user_id: object) -> str:
    """Обезличенный код разговора: по нему видно реплики одного разговора, но не человека."""
    if raw_user_id is None or raw_user_id == "":
        return ""
    digest = hmac.new(salt.encode("utf-8"), str(raw_user_id).encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:16]


def _canon(contact: str) -> str:
    """Одна форма для одного контакта: ссылка t.me/имя и @имя — это один и тот же ник."""
    contact = contact.lower()
    link = re.search(r"(?:t\.me|telegram\.me)/([a-z0-9_+]+)", contact)
    return "@" + link.group(1) if link else contact


def public_contacts_in(texts: Iterable[str]) -> tuple[str, ...]:
    """Почта, ники и ссылки Telegram, которые уже опубликованы в базе знаний.

    Всё, что есть в материалах сайта, — публичные контакты Александра и его ботов. Без этого вопрос
    «что за бот @ZCoderTest_bot?» терял бы ник при обезличивании, и помощник отвечал, что такого не знает.
    """
    found: dict[str, None] = {}
    for text in texts:
        for pattern in (_EMAIL, _TG_LINK, _MENTION):
            for match in pattern.finditer(text):
                found[match.group(0)] = None
    return tuple(found)


def redact(text: str, keep: tuple[str, ...] = ()) -> str:
    """Убирает контакты и номера. keep — публичные контакты владельца, их оставляем."""
    kept = {_canon(k) for k in keep}

    def _unless_kept(placeholder: str):
        return lambda m: m.group(0) if _canon(m.group(0)) in kept else placeholder

    text = _EMAIL.sub(_unless_kept("[почта]"), text)
    text = _TG_LINK.sub(_unless_kept("[ник]"), text)
    text = _MENTION.sub(_unless_kept("[ник]"), text)
    text = _DOCUMENT.sub("[документ]", text)
    text = _NAME.sub(lambda m: f"{m.group(1)} [имя]", text)

    def _number(match: re.Match[str]) -> str:
        value = match.group(0)
        if sum(ch.isdigit() for ch in value) < 7:
            return value
        if _YEARS.fullmatch(value.strip()):
            return value
        date = _DATE.match(value.strip())
        if date and sum(ch.isdigit() for ch in value.strip()[date.end():]) < 7:
            return value
        if _GROUPED.fullmatch(value.strip()) and _AMOUNT_AFTER.match(text, match.end()):
            return value
        return "[номер]"

    return _LONG_NUMBER.sub(_number, text)


def normalize(text: str) -> str:
    """Нормальная форма вопроса для кеша и подсчёта популярных вопросов."""
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())
