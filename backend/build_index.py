"""Сборка базы знаний: data/faqs.json + data/docs/*.txt|md → эмбеддинги → FAISS-индекс и метаданные.

    python -m backend.build_index

Индекс пишется в data/index/ (faiss.index и meta.json). Версия базы знаний — хеш текстов и модели эмбеддингов:
по ней видно, на какой версии базы был дан ответ (поле kb_version в журнале).
"""
from __future__ import annotations

import time

from openai import OpenAI

from .config import load_settings
from .embeddings import OpenAIEmbedder
from .rag_index import build_index, kb_version, load_faq_data, load_txt_documents, save_index, search_entries


def main() -> None:
    settings = load_settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY не задан: положите его в .env")

    items = []
    faq_path = settings.data_dir / "faqs.json"
    if faq_path.exists():
        faqs = load_faq_data(faq_path)
        items += faqs
        print(f"FAQ из {faq_path.name}: {len(faqs)}")
    docs = load_txt_documents(settings.data_dir / "docs")
    items += docs
    print(f"Фрагментов документов из {settings.data_dir / 'docs'}: {len(docs)}")
    if not items:
        raise SystemExit("Нет данных для индекса: ни faqs.json, ни документов")

    t0 = time.time()
    embedder = OpenAIEmbedder(OpenAI(api_key=settings.openai_api_key), settings.embed_model)
    texts, owners = search_entries(items)
    vectors = embedder.embed(texts)
    index = build_index(vectors)
    info = {"kb_version": kb_version(items, settings.embed_model), "embed_model": settings.embed_model,
            "dim": int(vectors.shape[1]), "built": time.strftime("%Y-%m-%d %H:%M:%S")}
    save_index(index, items, owners, settings.index_path, settings.meta_path, info)
    print(f"Индекс: {len(items)} фрагментов, {len(texts)} векторов (длинные разделы ещё и по абзацам), "
          f"размерность {vectors.shape[1]}, версия {info['kb_version']}, {time.time() - t0:.1f} с "
          f"-> {settings.index_path}")


if __name__ == "__main__":
    main()
