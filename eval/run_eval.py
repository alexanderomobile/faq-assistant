"""Прогон тестового набора: код урока против этого помощника, на одной базе знаний.

    python eval/run_eval.py mine      --set eval/test_set_b.json --run eval/runs/b   # этот помощник
    python eval/run_eval.py nomem     --set eval/test_set_b.json --run eval/runs/b   # без памяти диалога
    python eval/run_eval.py norewrite --set eval/test_set_b.json --run eval/runs/b   # без переписывания уточнений
    python eval/run_eval.py lesson    --set eval/test_set_b.json --run eval/runs/b --lesson-dir ../lesson-code

Наборы вопросов писали отдельные агенты, которые видели только тексты data/docs: ни faqs.json, ни промпта,
ни кода они не видели. test_set_a.json (32 вопроса и 10 диалогов) — первый набор, по его ошибкам помощник
дорабатывался; test_set_b.json — второй, написан после доработки и до неё ни разу не запускался.

Код урока (https://github.com/MrGAN12009/pcef9) запускается как есть: его backend копируется в рабочую
папку, рядом кладётся наша база знаний в его формате (faqs.json и документы .txt целиком), индекс
строит его же build_index, а вопросы идут в его FastAPI-приложение. Памяти у него нет: реплики диалога
уходят по одной, как их отправляет его страница. Нужен OPENAI_API_KEY в окружении.

Результат — <run>/results/<система>.json: ответ, время, токены, найденные фрагменты на каждую реплику.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]


def conversations(test: dict[str, Any]) -> list[dict[str, Any]]:
    """Одиночный вопрос — разговор из одной реплики, диалог — из нескольких."""
    convs = [{"id": s["id"], "turns": [s]} for s in test["singles"]]
    return convs + [{"id": d["id"], "turns": d["turns"]} for d in test["dialogs"]]


def run(convs: list[dict[str, Any]], ask: Callable[[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for conv in convs:
        session = f"eval-{conv['id']}-{uuid.uuid4().hex[:12]}"
        for n, turn in enumerate(conv["turns"], 1):
            t0 = time.perf_counter()
            got = ask(session, turn["question"])
            ms = int((time.perf_counter() - t0) * 1000)
            rows.append({"conv": conv["id"], "turn": n, "question": turn["question"], "ms": ms, **got})
            print(f"{conv['id']}#{n} {ms:>5} мс  {got['answer'][:70]!r}", flush=True)
    return rows


def ask_mine(history_turns: int, rewrite: bool) -> tuple[Callable[[str, str], dict[str, Any]], dict[str, Any]]:
    os.environ["HISTORY_TURNS"] = str(history_turns)
    os.environ["REWRITE_FOLLOWUPS"] = "1" if rewrite else "0"
    # Журнал прогона — во временной папке: рабочий журнал на сервере он не трогает.
    os.environ["STATE_DIR"] = tempfile.mkdtemp(prefix="faq-eval-")
    os.environ.setdefault("USER_HASH_SALT", "eval-only-salt")
    sys.path.insert(0, str(ROOT))
    from backend.config import load_settings
    from backend.factory import build_assistant

    settings = load_settings()
    assistant = build_assistant(settings)

    def ask(session: str, question: str) -> dict[str, Any]:
        reply = assistant.answer(session, question)
        return {"answer": reply.answer, "status": reply.status, "sources": reply.sources,
                "top_score": reply.top_score, "history_turns": reply.history_turns,
                "search_query": reply.search_query, "prompt_tokens": reply.prompt_tokens,
                "completion_tokens": reply.completion_tokens}

    info = {"model": settings.chat_model, "top_k": settings.top_k, "min_similarity": settings.min_similarity,
            "history_turns": history_turns, "rewrite_followups": settings.rewrite_followups,
            "kb_version": assistant.kb_version, "items": len(assistant.items),
            "vectors": int(assistant.index.ntotal)}
    return ask, info


def ask_lesson(lesson_dir: Path, work: Path) -> tuple[Callable[[str, str], dict[str, Any]], dict[str, Any]]:
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(lesson_dir / "backend", work / "backend", ignore=shutil.ignore_patterns("__pycache__"))
    data = work / "data"
    data.mkdir()
    shutil.copy(ROOT / "data" / "faqs.json", data / "faqs.json")
    for doc in sorted((ROOT / "data" / "docs").glob("*.md")):
        (data / f"{doc.stem}.txt").write_text(doc.read_text(encoding="utf-8"), encoding="utf-8")
    sys.path.insert(0, str(work))
    build = importlib.import_module("backend.build_index")
    build.main()
    lesson_app = importlib.import_module("backend.app")
    from fastapi.testclient import TestClient

    client = TestClient(lesson_app.app)
    usage: dict[str, int] = {}
    completions = lesson_app.client.chat.completions
    original = completions.create

    def recording_create(*args: Any, **kwargs: Any) -> Any:
        response = original(*args, **kwargs)
        usage.update(prompt_tokens=response.usage.prompt_tokens, completion_tokens=response.usage.completion_tokens)
        return response

    completions.create = recording_create  # считаем токены, ничего не меняя в самом вызове

    def ask(_session: str, question: str) -> dict[str, Any]:
        resp = client.post("/chat", json={"message": question, "top_k": 3})  # так шлёт его frontend/index.html
        resp.raise_for_status()
        body = resp.json()
        return {"answer": body["answer"], "sources": [c.get("source", "") for c in body["context"]], **usage}

    info = {"model": "gpt-4.1-mini", "top_k": 3, "history_turns": 0, "items": int(lesson_app.faiss_index.ntotal)}
    return ask, info


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("system", choices=["mine", "nomem", "norewrite", "lesson"])
    parser.add_argument("--set", type=Path, required=True, help="файл с вопросами, например eval/test_set_b.json")
    parser.add_argument("--run", type=Path, required=True, help="папка прогона, например eval/runs/b")
    parser.add_argument("--lesson-dir", type=Path, default=ROOT.parent / "lesson-code")
    parser.add_argument("--work", type=Path, default=Path(tempfile.gettempdir()) / "faq-eval-lesson")
    args = parser.parse_args()
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("нужен OPENAI_API_KEY в окружении")

    test = json.loads(args.set.read_text(encoding="utf-8"))
    if args.system == "lesson":
        ask, info = ask_lesson(args.lesson_dir.resolve(), args.work.resolve())
    else:
        ask, info = ask_mine(0 if args.system == "nomem" else 3, rewrite=args.system == "mine")
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    rows = run(conversations(test), ask)
    results = args.run / "results"
    results.mkdir(parents=True, exist_ok=True)
    out = results / f"{args.system}.json"
    out.write_text(json.dumps({"system": args.system, "set": args.set.name, "started": started, **info, "rows": rows},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(rows)} ответов -> {out}")


if __name__ == "__main__":
    main()
