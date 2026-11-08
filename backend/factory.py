"""Сборка ассистента из настроек: клиент OpenAI, индекс, память, журнал."""
from __future__ import annotations

from openai import OpenAI, Timeout

from .assistant import Assistant, OpenAIChat
from .config import Settings
from .embeddings import OpenAIEmbedder
from .journal import Journal
from .memory import DialogMemory
from .privacy import public_contacts_in
from .rag_index import item_text, load_index


def build_assistant(settings: Settings) -> Assistant:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY не задан: положите его в .env или /etc/faq-assistant.env")
    if not settings.user_hash_salt:
        raise RuntimeError("USER_HASH_SALT не задан: без соли обезличенный код диалога легко подобрать")
    index, items, owners, meta = load_index(settings.index_path, settings.meta_path)
    if meta.get("embed_model") != settings.embed_model:
        # Вопрос, превращённый в вектор другой моделью, сравнивался бы с базой как попало.
        raise RuntimeError(f"Индекс собран моделью {meta.get('embed_model')}, а в настройках {settings.embed_model}: "
                           "пересоберите индекс (python -m backend.build_index) или поправьте EMBED_MODEL")
    # Ники, ссылки и почта из самих материалов сайта — публичные: при обезличивании вопроса их не вырезаем.
    public = tuple(dict.fromkeys(settings.public_contacts + public_contacts_in(item_text(i) for i in items)))
    # Подключение ждёт не больше 5 с, ответ — 20 с, один повтор. Было 30 с и два повтора: в худшем случае около
    # 90 с на вызов, а сайт ждёт помощника 60 с. Переписывание уточнения — до 8 с без повторов: не успело,
    # и поиск идёт без него. Общий срок ответа — ANSWER_DEADLINE_S в assistant.py.
    client = OpenAI(api_key=settings.openai_api_key, timeout=Timeout(20.0, connect=5.0), max_retries=1)
    quick = client.with_options(timeout=Timeout(8.0, connect=3.0), max_retries=0)
    return Assistant(
        embedder=OpenAIEmbedder(client, settings.embed_model),
        chat=OpenAIChat(client, settings.chat_model, settings.temperature, settings.max_tokens, quick_client=quick),
        index=index,
        items=items,
        owners=owners,
        memory=DialogMemory(settings.history_turns, settings.memory_ttl_minutes * 60, settings.max_sessions),
        journal=Journal(settings.journal_path),
        top_k=settings.top_k,
        min_similarity=settings.min_similarity,
        salt=settings.user_hash_salt,
        public_contacts=public,
        kb_version=str(meta.get("kb_version", "")),
        rewrite_followups=settings.rewrite_followups,
    )
