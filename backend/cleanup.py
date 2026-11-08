"""Удаление записей журнала старше RETENTION_DAYS (по умолчанию 90 дней).

    python -m backend.cleanup

На сервере запускается раз в сутки таймером faq-assistant-cleanup: срок хранения соблюдается,
даже если в чат долго никто не пишет (сама служба чистит журнал только при новых вопросах).
"""
from __future__ import annotations

from .config import load_settings
from .journal import Journal


def main() -> int:
    settings = load_settings()
    removed = Journal(settings.journal_path).cleanup(settings.retention_days)
    print(f"журнал: удалено записей старше {settings.retention_days} дней: {removed}")
    return removed


if __name__ == "__main__":
    main()
