"""Сборка HTML- и CSV-отчёта по результатам оценки."""
from __future__ import annotations

import csv
import html
from datetime import date
from pathlib import Path

from scoring import Assessment, Benchmarks, Verdict

CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       margin: 0; padding: 32px; background: #f6f7f9; color: #16181d; line-height: 1.5; }
@media (prefers-color-scheme: dark) {
  body { background: #14161a; color: #e8eaed; }
  .card, table { background: #1d2025 !important; }
  th { background: #24282e !important; }
  td, th { border-color: #2c3037 !important; }
  .muted { color: #9aa0a6 !important; }
}
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 32px 0 12px; }
.muted { color: #6b7280; font-size: 13px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
        gap: 12px; margin: 24px 0; }
.card { background: #fff; border: 1px solid #e3e5e8; border-radius: 10px; padding: 14px 16px; }
.card .label { font-size: 12px; color: #6b7280; margin-bottom: 6px; }
.card .value { font-size: 22px; font-weight: 600; letter-spacing: -0.01em; }
table { width: 100%; border-collapse: collapse; background: #fff; font-size: 13px;
        border-radius: 10px; overflow: hidden; border: 1px solid #e3e5e8; }
th { background: #f0f1f3; text-align: left; padding: 9px 10px; font-weight: 600;
     border-bottom: 1px solid #e3e5e8; white-space: nowrap; }
td { padding: 9px 10px; border-bottom: 1px solid #eceef0; vertical-align: top; }
td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 11px;
       font-weight: 600; white-space: nowrap; }
.tag.block { background: #fde8e8; color: #a02b2b; }
.tag.watch { background: #fdf1dd; color: #8a5a10; }
.tag.donor { background: #e3f4e8; color: #1f6b38; }
.reasons { font-size: 12px; color: #4b5563; }
.reasons li { margin-bottom: 2px; }
.notice { background: #fff; border-left: 3px solid #4b5563; padding: 12px 16px;
          border-radius: 6px; margin: 20px 0; font-size: 13px; }
ul { margin: 4px 0; padding-left: 18px; }
"""


def _num(value) -> str:
    return f"{value:,.0f}".replace(",", "\u202f")


def _money(value: float) -> str:
    return _num(value) + " ₽"


def _rows_html(items: list[Assessment]) -> str:
    out = []
    for item in items:
        reasons = "".join(f"<li>{html.escape(t)}</li>" for t in item.triggers)
        tag_class = {
            Verdict.BLOCK: "block",
            Verdict.WATCH: "watch",
            Verdict.DONOR: "donor",
        }.get(item.verdict, "")
        out.append(
            f"""<tr>
<td><strong>{html.escape(item.row.placement)}</strong><br>
    <span class="muted">{html.escape(item.row.campaign_name)}</span></td>
<td><span class="tag {tag_class}">{item.verdict.value}</span></td>
<td class="num">{_num(item.row.impressions)}</td>
<td class="num">{_num(item.row.clicks)}</td>
<td class="num">{item.row.ctr:.2f}%</td>
<td class="num">{_money(item.row.cost)}</td>
<td class="num">{item.conversions:.0f}</td>
<td class="num">{_money(item.cpa) if item.cpa else "—"}</td>
<td class="num">{item.bounce_rate:.0f}%</td>
<td class="reasons"><ul>{reasons}</ul></td>
</tr>"""
        )
    return "".join(out)


def _table(items: list[Assessment]) -> str:
    if not items:
        return '<p class="muted">Ничего не попало в эту категорию.</p>'
    return f"""<table>
<thead><tr>
<th>Площадка</th><th>Вердикт</th><th>Показы</th><th>Клики</th><th>CTR</th>
<th>Расход</th><th>Конв.</th><th>CPA</th><th>Отказы</th><th>Почему</th>
</tr></thead>
<tbody>{_rows_html(items)}</tbody>
</table>"""


def build_html(
    results: list[Assessment],
    bench: Benchmarks,
    mode: str,
    metrika_used: bool,
    days: int,
    applied: list[str] | None = None,
) -> str:
    blocked = sorted(
        [r for r in results if r.verdict == Verdict.BLOCK],
        key=lambda r: r.row.cost,
        reverse=True,
    )
    watch = sorted(
        [r for r in results if r.verdict == Verdict.WATCH and r.triggers],
        key=lambda r: r.row.cost,
        reverse=True,
    )[:60]
    donors = sorted(
        [r for r in results if r.verdict == Verdict.DONOR],
        key=lambda r: r.row.cost,
        reverse=True,
    )[:30]

    wasted = sum(r.row.cost for r in blocked)
    share = (wasted / bench.total_cost * 100) if bench.total_cost else 0

    if mode == "apply":
        notice = (
            f"<strong>Режим: запись включена.</strong> Запрещённые площадки записаны "
            f"в Директ — всего {len(applied or [])} кампаний обновлено. "
            "Дай кампаниям 7–14 дней на переобучение стратегии, прежде чем оценивать результат."
        )
    else:
        notice = (
            "<strong>Режим: только анализ.</strong> В Директе ничего не изменено. "
            "Чтобы ассистент сам записывал запреты, поставь переменную "
            "<code>MODE=apply</code> в настройках Render."
        )

    source = (
        "Директ + Метрика (конверсии и отказы взяты из Метрики)"
        if metrika_used
        else "только Директ — Метрика не подключена, точность ниже"
    )

    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Чистка площадок РСЯ — {date.today().isoformat()}</title>
<style>{CSS}</style></head>
<body>
<h1>Чистка площадок РСЯ</h1>
<p class="muted">Период: последние {days} дней &nbsp;·&nbsp; Источник данных: {source}
 &nbsp;·&nbsp; Отчёт от {date.today().strftime("%d.%m.%Y")}</p>

<div class="notice">{notice}</div>

<div class="grid">
  <div class="card"><div class="label">Площадок в анализе</div>
    <div class="value">{_num(len(results))}</div></div>
  <div class="card"><div class="label">На запрет</div>
    <div class="value">{_num(len(blocked))}</div></div>
  <div class="card"><div class="label">Сливают бюджет</div>
    <div class="value">{_money(wasted)}</div></div>
  <div class="card"><div class="label">Доля расхода</div>
    <div class="value">{share:.1f}%</div></div>
  <div class="card"><div class="label">Средний CTR</div>
    <div class="value">{bench.avg_ctr:.2f}%</div></div>
  <div class="card"><div class="label">Целевой CPA</div>
    <div class="value">{_money(bench.target_cpa)}</div></div>
</div>

<h2>Запретить — {len(blocked)}</h2>
<p class="muted">Совпало минимум два независимых признака слива при достаточной статистике.</p>
{_table(blocked)}

<h2>Под наблюдением — показаны {len(watch)}</h2>
<p class="muted">Есть тревожные признаки, но данных пока мало либо площадка
всё-таки приносит конверсии. Отключать рано.</p>
{_table(watch)}

<h2>Доноры — {len(donors)}</h2>
<p class="muted">Работают лучше целевого CPA. Сюда имеет смысл добавлять бюджет,
а не отнимать.</p>
{_table(donors)}
</body></html>"""


def write_csv(results: list[Assessment], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(
            ["Площадка", "Кампания", "Вердикт", "Показы", "Клики", "CTR %",
             "Расход", "Конверсии", "CPA", "Отказы %", "Причины"]
        )
        for r in results:
            writer.writerow([
                r.row.placement, r.row.campaign_name, r.verdict.value,
                r.row.impressions, r.row.clicks, f"{r.row.ctr:.2f}",
                f"{r.row.cost:.2f}", f"{r.conversions:.0f}",
                f"{r.cpa:.2f}" if r.cpa else "", f"{r.bounce_rate:.1f}",
                " | ".join(r.triggers),
            ])
