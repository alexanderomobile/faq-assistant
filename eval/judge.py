"""Оценка ответов: автоматические проверки и слепые судьи.

    python eval/judge.py packets --run eval/runs/b --set eval/test_set_b.json --systems lesson nomem norewrite mine
    python eval/judge.py report  --run eval/runs/b --set eval/test_set_b.json --systems lesson nomem norewrite mine

packets — пакеты для судей: <run>/judge/packet_<судья>_<часть>.json и ключи к ним (*_key.json).
report  — сводка по оценкам из <run>/judge/verdicts/<судья>_<часть>.json: <run>/report.json и <run>/report.md.

Судьи — агенты-LLM, инструкция — eval/judge_prompt.md. Каждый видит вопрос, предыдущие вопросы диалога, заметку
автора теста о правильном ответе и ответы систем под буквами (W, X, Y, Z). Какая буква какой системе принадлежит,
судья не знает: у каждого судьи на каждом вопросе свой порядок. Судья ставит каждому ответу:
  correct   2 — верно и по делу, 1 — частично, 0 — неверно, не по вопросу или с выдумкой
  invented  есть ли в ответе факт, которого нет в базе знаний (data/docs и data/faqs.json)
  followup  для уточняющих реплик диалога: понял ли ответ, к чему относится короткий вопрос
Итог по ответу — медиана оценок трёх судей и большинство голосов по флажкам.

Автоматические проверки (без судей): числа, которых нет ни в базе знаний, ни в вопросе; разметка Markdown
(в окне чата она видна как звёздочки и решётки); повтор телефона или почты посетителя; время и токены.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
JUDGES = ["j1", "j2", "j3"]
PARTS: dict[str, Callable[[str], bool]] = {"singles": lambda conv: conv.startswith("s-"),
                                           "dialogs": lambda conv: conv.startswith("d-")}
NAMES = {"lesson": "код урока как есть", "nomem": "без памяти диалога", "norewrite": "без переписывания уточнений",
         "mine": "мой помощник", "mine-v1": "мой помощник, версия 1", "nomem-v1": "версия 1 без памяти"}
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
LIST_MARK = re.compile(r"^\s*\d+[.)]\s", re.M)
MARKDOWN = re.compile(r"\*\*|__|^#{1,6}\s|^\s*[-*•]\s|^\s*\d+[.)]\s", re.M)
# Контакты посетителей из наборов вопросов: помощник не должен повторять их в ответе.
VISITOR_CONTACTS = ("123-45-67", "900 123", "000-12-34", "900 000", "marina.re@example.com")


def letters(n: int) -> list[str]:
    return list("WXYZ"[-n:]) if n <= 4 else [chr(ord("A") + i) for i in range(n)]


def load_results(run: Path, systems: list[str]) -> dict[str, dict[str, Any]]:
    return {s: json.loads((run / "results" / f"{s}.json").read_text(encoding="utf-8")) for s in systems}


def load_tests(path: Path) -> dict[str, dict[str, Any]]:
    """Реплика теста по ключу 'conv#turn' плюс вопросы, заданные до неё в том же диалоге."""
    test = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict[str, Any]] = {}
    for s in test["singles"]:
        out[f"{s['id']}#1"] = {**s, "before": []}
    for d in test["dialogs"]:
        for n, turn in enumerate(d["turns"], 1):
            out[f"{d['id']}#{n}"] = {**turn, "before": [t["question"] for t in d["turns"][: n - 1]]}
    return out


def kb_text() -> str:
    parts = [p.read_text(encoding="utf-8") for p in sorted((ROOT / "data" / "docs").glob("*.md"))]
    parts.append((ROOT / "data" / "faqs.json").read_text(encoding="utf-8"))
    return "\n".join(parts)


def numbers(text: str) -> set[str]:
    text = LIST_MARK.sub(" ", text)  # «1. …» в начале строки — нумерация списка, а не факт
    return {n.replace(",", ".") for n in NUMBER.findall(text)}


def auto_checks(answer: str, question: str, known: set[str]) -> dict[str, Any]:
    unknown = sorted(numbers(answer) - known - numbers(question))
    in_question = [c for c in VISITOR_CONTACTS if c in question]
    return {
        "unknown_numbers": unknown,
        "markdown": bool(MARKDOWN.search(answer)),
        "contact_echo": any(c in answer for c in in_question),
        "chars": len(answer),
    }


def make_packets(run: Path, test_set: Path, systems: list[str]) -> None:
    results, tests = load_results(run, systems), load_tests(test_set)
    rows = {s: {f"{r['conv']}#{r['turn']}": r for r in results[s]["rows"]} for s in systems}
    keys = list(tests)
    assert all(set(rows[s]) == set(keys) for s in systems), "в результатах не те реплики"
    marks = letters(len(systems))
    judge_dir = run / "judge"
    judge_dir.mkdir(parents=True, exist_ok=True)
    for judge in JUDGES:
        rng = random.Random(f"pecf11-{run.name}-{judge}")  # у каждого судьи свой порядок, и он воспроизводим
        for part, belongs in PARTS.items():
            items, key = [], {}
            for k in keys:
                if not belongs(k.split("#")[0]):
                    continue
                order = systems[:]
                rng.shuffle(order)
                t = tests[k]
                items.append({
                    "item": k,
                    "earlier_questions_in_dialog": t["before"],
                    "question": t["question"],
                    "answerable_from_kb": t["answerable"],
                    "test_author_note": t["expected"],
                    "answers": {marks[i]: rows[s][k]["answer"] for i, s in enumerate(order)},
                })
                key[k] = {marks[i]: s for i, s in enumerate(order)}
            (judge_dir / f"packet_{judge}_{part}.json").write_text(
                json.dumps({"judge": judge, "part": part, "items": items}, ensure_ascii=False, indent=1),
                encoding="utf-8")
            (judge_dir / f"packet_{judge}_{part}_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
        print(f"{judge}: пакеты готовы ({', '.join(marks)})")


def make_report(run: Path, test_set: Path, systems: list[str]) -> None:
    results, tests = load_results(run, systems), load_tests(test_set)
    known = numbers(kb_text())
    verdicts: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for judge in JUDGES:
        for part in PARTS:
            vfile = run / "judge" / "verdicts" / f"{judge}_{part}.json"
            key = json.loads((run / "judge" / f"packet_{judge}_{part}_key.json").read_text(encoding="utf-8"))
            data = json.loads(vfile.read_text(encoding="utf-8"))
            seen = set()
            for v in data["ratings"]:
                system = key[v["item"]][v["answer"]]
                if (v["item"], v["answer"]) in seen:
                    raise SystemExit(f"{vfile.name}: повтор оценки {v['item']} {v['answer']}")
                verdicts.setdefault((v["item"], system), []).append(v)
                seen.add((v["item"], v["answer"]))
            missing = {(k, letter) for k in key for letter in key[k]} - seen
            if missing:
                raise SystemExit(f"{vfile.name}: нет оценок для {sorted(missing)[:5]}… ({len(missing)})")

    per_answer: dict[str, list[dict[str, Any]]] = {s: [] for s in systems}
    for s in systems:
        for r in results[s]["rows"]:
            k = f"{r['conv']}#{r['turn']}"
            t = tests[k]
            vs = verdicts[(k, s)]
            followups = [v["followup"] for v in vs if v.get("followup") is not None]
            per_answer[s].append({
                "item": k, "answerable": t["answerable"], "turn": r["turn"], "dialog": k.startswith("d-"),
                "correct": statistics.median(v["correct"] for v in vs),
                "invented": sum(bool(v["invented"]) for v in vs) >= 2,
                "followup": (sum(followups) >= 2) if len(followups) >= 2 else None,
                "ms": r["ms"], "prompt_tokens": r.get("prompt_tokens", 0),
                "completion_tokens": r.get("completion_tokens", 0),
                **auto_checks(r["answer"], r["question"], known),
                "notes": [v.get("note", "") for v in vs],
            })

    summary: dict[str, dict[str, int]] = {}
    for s in systems:
        a = per_answer[s]
        ans = [x for x in a if x["answerable"]]
        unans = [x for x in a if not x["answerable"]]
        follow = [x for x in a if x["dialog"] and x["turn"] > 1]
        ms = sorted(x["ms"] for x in a)
        summary[s] = {
            "answers": len(a),
            "correct_full": sum(x["correct"] == 2 for x in a),
            "correct_answerable": sum(x["correct"] == 2 for x in ans), "answerable": len(ans),
            "honest_unanswerable": sum(x["correct"] == 2 for x in unans), "unanswerable": len(unans),
            "followup_correct": sum(x["correct"] == 2 for x in follow), "followups": len(follow),
            "followup_understood": sum(bool(x["followup"]) for x in follow),
            "wrong": sum(x["correct"] == 0 for x in a),
            "invented": sum(x["invented"] for x in a),
            "unknown_numbers": sum(bool(x["unknown_numbers"]) for x in a),
            "markdown": sum(x["markdown"] for x in a),
            "contact_echo": sum(x["contact_echo"] for x in a),
            "median_ms": int(statistics.median(ms)),
            "p90_ms": ms[max(0, -(-9 * len(ms) // 10) - 1)],
            "median_prompt_tokens": int(statistics.median(x["prompt_tokens"] for x in a)),
            "median_completion_tokens": int(statistics.median(x["completion_tokens"] for x in a)),
            "median_chars": int(statistics.median(x["chars"] for x in a)),
        }
    (run / "report.json").write_text(json.dumps({"set": test_set.name, "systems": systems, "summary": summary,
                                                 "answers": per_answer}, ensure_ascii=False, indent=1),
                                     encoding="utf-8")

    n_turns = len(tests)
    head = f"| {test_set.name}: {n_turns} реплик | " + " | ".join(NAMES.get(s, s) for s in systems) + " |"
    lines = [head, "|---" * (len(systems) + 1) + "|"]

    def row(label: str, fn: Callable[[dict[str, int]], str]) -> None:
        lines.append(f"| {label} | " + " | ".join(fn(summary[s]) for s in systems) + " |")

    row("верный ответ (медиана трёх судей — 2 из 2)", lambda x: f"{x['correct_full']} из {x['answers']}")
    row("на вопросы, ответ на которые есть в базе", lambda x: f"{x['correct_answerable']} из {x['answerable']}")
    row("честный отказ, когда ответа в базе нет", lambda x: f"{x['honest_unanswerable']} из {x['unanswerable']}")
    row("уточняющие реплики диалога: верный ответ", lambda x: f"{x['followup_correct']} из {x['followups']}")
    row("уточняющие реплики: понял, о чём речь", lambda x: f"{x['followup_understood']} из {x['followups']}")
    row("неверный ответ (медиана 0)", lambda x: str(x["wrong"]))
    row("ответов с выдумкой (большинство судей)", lambda x: str(x["invented"]))
    row("ответов с числом не из базы и не из вопроса", lambda x: str(x["unknown_numbers"]))
    row("ответов с разметкой Markdown", lambda x: str(x["markdown"]))
    row("повторил телефон или почту посетителя", lambda x: str(x["contact_echo"]))
    row("время ответа, медиана / 90-й процентиль, мс", lambda x: f"{x['median_ms']} / {x['p90_ms']}")
    row("токенов промпта, медиана", lambda x: str(x["median_prompt_tokens"]))
    row("длина ответа, медиана знаков", lambda x: str(x["median_chars"]))
    (run / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["packets", "report"])
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--set", type=Path, required=True)
    parser.add_argument("--systems", nargs="+", required=True)
    args = parser.parse_args()
    (make_packets if args.command == "packets" else make_report)(args.run, args.set, args.systems)


if __name__ == "__main__":
    main()
