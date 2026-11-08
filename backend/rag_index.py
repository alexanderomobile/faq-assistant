"""База знаний: загрузка FAQ и документов, FAISS-индекс и поиск похожих фрагментов.

Отличия от rag_index.py урока:
- метаданные лежат в JSON, а не в .npy с pickle (np.load(allow_pickle=True) выполняет код из подменённого файла);
- векторы нормализуются и ищутся через IndexFlatIP, поэтому оценка — косинусная близость от -1 до 1,
  и по ней можно отсечь нерелевантное порогом (L2-расстояние урока порога не имело);
- документ с разделами «## …» режется по разделам: один файл на всё давал бы один размытый вектор;
- длинный раздел (кейс: задача, решение, результат) ищется ещё и по абзацам, а модели отдаётся целиком.
  Вектор раздела в 1000 знаков размыт: на вопрос «сколько групп Telegram читает помощник?» он проигрывал
  коротким FAQ, хотя ответ («8 групп») был только в нём. Каждый абзац ищется вместе с заголовком раздела,
  поэтому кусок из одних цифр не теряет тему, а найденный абзац приводит к полному разделу.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import faiss
import numpy as np


@dataclass(frozen=True)
class Hit:
    item: dict[str, str]
    score: float


def load_faq_data(path: Path) -> list[dict[str, str]]:
    """FAQ из JSON: список объектов строго с полями question и answer (как требует урок)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path.name}: ожидается список объектов")
    items = []
    for i, row in enumerate(data):
        if not isinstance(row, dict) or not row.get("question") or not row.get("answer"):
            raise ValueError(f"{path.name}[{i}]: нужны непустые поля question и answer")
        items.append({"question": str(row["question"]).strip(), "answer": str(row["answer"]).strip(),
                      "source": f"{path.name}#{i + 1}"})
    return items


def load_txt_documents(directory: Path) -> list[dict[str, str]]:
    """Документы .txt и .md.

    Как в уроке, первая непустая строка — заголовок («вопрос»), остальное — текст («ответ»).
    Если в документе есть разделы «## Название», каждый раздел становится отдельным элементом
    с вопросом «Заголовок документа: Название раздела».
    """
    docs: list[dict[str, str]] = []
    if not directory.is_dir():
        return docs
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in {".txt", ".md"}:
            continue
        lines = [line.rstrip() for line in path.read_text(encoding="utf-8").strip().splitlines()]
        if not lines:
            continue
        title = lines[0].lstrip("# ").strip() or path.stem
        sections: list[tuple[str, list[str]]] = [("", [])]
        for line in lines[1:]:
            if line.startswith("## "):
                sections.append((line[3:].strip(), []))
            else:
                sections[-1][1].append(line)
        for name, body in sections:
            text = "\n".join(body).strip()
            if not text:
                continue
            question = f"{title}: {name}" if name else title
            source = f"{path.name}#{name}" if name else path.name
            docs.append({"question": question, "answer": text, "source": source})
    return docs


# Раздел длиннее этого ищется ещё и по абзацам. Абзацы FAQ и коротких разделов не режутся.
SPLIT_OVER = 400


def item_text(item: dict[str, str]) -> str:
    return f"{item['question']}\n{item['answer']}"


def search_entries(items: list[dict[str, str]]) -> tuple[list[str], list[int]]:
    """Тексты для векторов и номер фрагмента, к которому ведёт каждый вектор."""
    texts: list[str] = []
    owners: list[int] = []
    for n, item in enumerate(items):
        texts.append(item_text(item))
        owners.append(n)
        paragraphs = [p.strip() for p in item["answer"].split("\n\n") if p.strip()]
        if len(item["answer"]) > SPLIT_OVER and len(paragraphs) > 1:
            for paragraph in paragraphs:
                texts.append(f"{item['question']}\n{paragraph}")
                owners.append(n)
    return texts, owners


def kb_version(items: list[dict[str, str]], embed_model: str) -> str:
    raw = embed_model + "\n" + "\n\n".join(item_text(i) for i in items)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def normalized(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype="float32")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.maximum(norms, 1e-12)


def build_index(vectors: np.ndarray) -> faiss.IndexFlatIP:
    vectors = normalized(vectors)
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)  # pyright: ignore[reportCallIssue]  # обёртка faiss принимает один аргумент
    return index


def save_index(index: faiss.Index, items: list[dict[str, str]], owners: list[int], index_path: Path,
               meta_path: Path, info: dict[str, Any]) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    # Через байты, а не faiss.write_index(path): faiss на Windows не открывает пути с кириллицей.
    index_path.write_bytes(faiss.serialize_index(index).tobytes())
    meta = {**info, "items": items, "owners": owners}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def load_index(index_path: Path,
               meta_path: Path) -> tuple[faiss.Index, list[dict[str, str]], list[int], dict[str, Any]]:
    """Индекс, фрагменты, номер фрагмента для каждого вектора и сведения о сборке."""
    if not index_path.exists() or not meta_path.exists():
        raise RuntimeError("Индекс не найден: сначала соберите его командой `python -m backend.build_index`")
    index = faiss.deserialize_index(np.frombuffer(index_path.read_bytes(), dtype=np.uint8))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    items = meta.pop("items")
    owners = meta.pop("owners", list(range(len(items))))
    if index.ntotal != len(owners) or not all(0 <= o < len(items) for o in owners):
        raise RuntimeError(f"Индекс и метаданные разошлись: {index.ntotal} векторов, {len(owners)} ссылок")
    return index, items, owners, meta


def search(index: faiss.Index, items: list[dict[str, str]], queries: np.ndarray, k: int,
           min_score: float, owners: list[int] | None = None) -> list[Hit]:
    """Ближайшие фрагменты для одного или нескольких векторов запроса.

    Несколько запросов (вопрос, вопрос вместе с предыдущим, переписанное уточнение) объединяются,
    и несколько векторов одного фрагмента (раздел и его абзацы) тоже: у фрагмента берётся лучшая
    оценка. Возвращаются не больше k разных фрагментов с оценкой не ниже min_score.
    """
    owners = owners if owners is not None else list(range(len(items)))
    queries = normalized(queries)
    # Векторов больше, чем фрагментов: берём с запасом, чтобы после склейки осталось k разных.
    scores, idxs = index.search(queries, min(k * 4, index.ntotal))  # pyright: ignore[reportCallIssue]
    best: dict[int, float] = {}
    for row_scores, row_idxs in zip(scores, idxs):
        for score, i in zip(row_scores, row_idxs):
            if 0 <= i < len(owners) and score >= min_score:
                n = owners[int(i)]
                best[n] = max(best.get(n, -1.0), float(score))
    ranked = sorted(best.items(), key=lambda x: -x[1])[:k]
    return [Hit(items[i], score) for i, score in ranked]
