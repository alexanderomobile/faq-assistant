"""Диагностика поиска: на каком месте нужные разделы документов среди найденных фрагментов.

    python eval/retrieval.py [--set eval/test_set.json]

Для каждой реплики с ответом в базе знаний автор теста указал разделы документов (sources). Скрипт строит
запросы так же, как помощник (вопрос; в диалоге ещё и предыдущий вопрос вместе с текущим), и печатает место
лучшего из нужных разделов в общей выдаче по всем фрагментам базы (FAQ тоже участвуют, но в sources их нет:
автор теста FAQ не видел). Итог — доля реплик, у которых нужный раздел попал в первые k, и лучшие оценки для вопросов с ответом
и без ответа в базе (чтобы видеть, где стоит порог MIN_SIMILARITY).
Нужен OPENAI_API_KEY: вопросы превращаются в векторы одним запросом.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import load_settings  # noqa: E402
from backend.embeddings import OpenAIEmbedder  # noqa: E402
from backend.rag_index import load_index, normalized  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", type=Path, default=ROOT / "eval" / "test_set.json")
    args = parser.parse_args()
    settings = load_settings()
    index, items, owners, _ = load_index(settings.index_path, settings.meta_path)
    from openai import OpenAI

    embedder = OpenAIEmbedder(OpenAI(api_key=settings.openai_api_key), settings.embed_model)
    test = json.loads(args.set.read_text(encoding="utf-8"))
    turns = []
    for s in test["singles"]:
        turns.append((f"{s['id']}#1", s, None))
    for d in test["dialogs"]:
        for n, t in enumerate(d["turns"], 1):
            turns.append((f"{d['id']}#{n}", t, d["turns"][n - 2]["question"] if n > 1 else None))

    queries: list[str] = []
    for _, t, prev in turns:
        queries.append(t["question"])
        queries.append(f"{prev}\n{t['question']}" if prev else t["question"])
    vecs = normalized(embedder.embed(queries))
    all_vecs = normalized(index.reconstruct_n(0, index.ntotal))
    sims = vecs @ all_vecs.T
    sources = [i.get("source", "") for i in items]

    ranks = []
    best_answerable: list[float] = []
    best_not: list[float] = []
    for n, (key, t, _) in enumerate(turns):
        by_vector = np.maximum(sims[2 * n], sims[2 * n + 1])  # как в помощнике: лучшая из двух запросов
        best = np.full(len(items), -1.0)
        for v, owner in enumerate(owners):  # раздел и его абзацы: лучшая оценка
            best[owner] = max(best[owner], by_vector[v])
        order = [int(i) for i in np.argsort(-best)]
        if not (t["answerable"] and t["sources"]):
            best_not.append(float(best[order[0]]))
            print(f"{key:7} ответа в базе нет, лучший {best[order[0]]:.3f} {sources[order[0]][:40]:40}  {t['question'][:50]}")
            continue
        best_answerable.append(float(best[order[0]]))
        wanted = [order.index(sources.index(src)) + 1 for src in t["sources"] if src in sources]
        rank = min(wanted) if wanted else 99
        ranks.append(rank)
        top = sources[order[0]]
        print(f"{key:7} место {rank:>2}  лучший {best[order[0]]:.3f} {top[:40]:40}  {t['question'][:50]}")
    for k in (3, 4, 6, 8):
        print(f"нужный раздел в первых {k}: {sum(r <= k for r in ranks)} из {len(ranks)}")
    # Порог MIN_SIMILARITY отсекает фрагменты хуже него: здесь видно, где он стоит относительно реальных оценок.
    print("лучшая оценка, есть ответ в базе: мин {:.3f}, 10-й процентиль {:.3f}".format(
        min(best_answerable), float(np.percentile(best_answerable, 10))))
    if best_not:
        print("лучшая оценка, ответа в базе нет: мин {:.3f}, макс {:.3f}".format(min(best_not), max(best_not)))


if __name__ == "__main__":
    main()
