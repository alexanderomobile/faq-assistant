"""График итогов оценки в SVG без сторонних библиотек: assets/results.svg.

    python eval/chart.py --run eval/runs/b --systems lesson nomem norewrite mine --out assets/results.svg

Четыре показателя, у каждого своя шкала от 0 до 100 % (одна ось, никаких двух шкал на одном графике),
у каждой полосы подписаны система и значение, поэтому цвет — только дополнительная подсказка.
Цвета проверены валидатором палитры на различимость при дальтонизме.
"""
from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path

COLORS = {"lesson": "#eb6834", "nomem": "#1baf7a", "norewrite": "#eda100", "mine": "#2a78d6",
          "mine-v1": "#1baf7a", "nomem-v1": "#eda100"}
NAMES = {"lesson": "код урока как есть", "nomem": "без памяти диалога", "norewrite": "без переписывания уточнений",
         "mine": "мой помощник", "mine-v1": "версия 1", "nomem-v1": "версия 1 без памяти"}
METRICS = [
    ("верный ответ, все реплики", "correct_full", "answers"),
    ("уточняющие реплики диалога: верный ответ", "followup_correct", "followups"),
    ("честный отказ, когда ответа в базе нет", "honest_unanswerable", "unanswerable"),
    ("ответы с выдумкой (меньше — лучше)", "invented", "answers"),
]
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e2dc", "#fcfcfb"


def render(summary: dict[str, dict[str, int]], systems: list[str], title: str, note: str) -> str:
    width, left, right = 820, 230, 120
    bar_h, gap, panel_gap = 18, 6, 34
    plot_w = width - left - right
    y = 64
    parts: list[str] = []
    for label, num_key, den_key in METRICS:
        parts.append(f'<text x="24" y="{y}" font-size="14" font-weight="700" fill="{INK}">{escape(label)}</text>')
        y += 12
        top = y
        for s in systems:
            num, den = summary[s][num_key], summary[s][den_key]
            share = num / den if den else 0.0
            w = max(2.0, plot_w * share)
            parts.append(f'<text x="{left - 10}" y="{y + bar_h - 5}" font-size="12" fill="{MUTED}" '
                         f'text-anchor="end">{escape(NAMES.get(s, s))}</text>')
            parts.append(f'<rect x="{left}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="4" fill="{COLORS.get(s, "#888")}">'
                         f'<title>{escape(NAMES.get(s, s))}: {num} из {den}</title></rect>')
            parts.append(f'<text x="{left + w + 8:.1f}" y="{y + bar_h - 5}" font-size="12" fill="{INK}">'
                         f'{num} из {den} · {round(100 * share)} %</text>')
            y += bar_h + gap
        for frac in (0.0, 0.5, 1.0):  # редкая сетка под полосами
            x = left + plot_w * frac
            parts.insert(len(parts) - 3 * len(systems), f'<line x1="{x:.1f}" y1="{top - 2}" x2="{x:.1f}" '
                         f'y2="{y - gap + 2}" stroke="{GRID}" stroke-width="1"/>')
        y += panel_gap - gap
    height = y + 24
    body = "\n  ".join(parts)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'font-family="system-ui, -apple-system, \'Segoe UI\', Roboto, Arial, sans-serif">\n'
            f'  <title>{escape(title)}</title>\n'
            f'  <rect width="{width}" height="{height}" rx="12" fill="{SURFACE}"/>\n'
            f'  <text x="24" y="34" font-size="16" font-weight="700" fill="{INK}">{escape(title)}</text>\n'
            f'  {body}\n'
            f'  <text x="24" y="{height - 12}" font-size="11" fill="{MUTED}">{escape(note)}</text>\n</svg>\n')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--systems", nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--title", default="Слепая оценка тремя судьями: доля реплик")
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    report = json.loads((args.run / "report.json").read_text(encoding="utf-8"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(report["summary"], args.systems, args.title, args.note), encoding="utf-8")
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
