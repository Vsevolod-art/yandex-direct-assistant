"""Прогон всей цепочки на синтетических данных, без обращения к API."""
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import yaml
from direct_api import PlacementRow
from metrika_api import MetrikaRow
from report import build_html, write_csv
from scoring import Verdict, assess, build_benchmarks

rules = yaml.safe_load(open(ROOT / "config.yaml", encoding="utf-8"))
rules["economics"]["target_cpa"] = 1500.0

random.seed(7)
rows, metrika = [], {}


def add(placement, impr, clicks, cost, conv, bounce, campaign="Кампания РСЯ", cid="111"):
    ctr = clicks / impr * 100 if impr else 0
    rows.append(PlacementRow(
        campaign_id=cid, campaign_name=campaign, placement=placement,
        network="AD_NETWORK", impressions=impr, clicks=clicks, ctr=ctr, cost=cost,
        avg_cpc=cost / clicks if clicks else 0, conversions=conv,
        cost_per_conversion=cost / conv if conv else 0,
        conversion_rate=conv / clicks * 100 if clicks else 0,
        bounce_rate=bounce, avg_pageviews=1.4,
    ))
    metrika[placement] = MetrikaRow(placement, float(clicks), bounce, 1.4, 40.0, float(conv))


# Явный слив: много денег, ноль конверсий, дикие отказы.
add("trash-portal.ru", 40000, 600, 24000, 0, 92)
# Мобильное приложение-мисклик.
add("com.gamedev.puzzle", 90000, 1800, 31000, 0, 95)
# Накрутка: аномальный CTR при нуле конверсий.
add("clickfarm.xyz", 5000, 900, 12000, 0, 88)
# Нормальная площадка.
add("news-portal.ru", 30000, 400, 9000, 7, 42)
# Донор.
add("niche-blog.ru", 12000, 260, 6000, 9, 28)
# Мало данных — не судить.
add("tiny-site.ru", 60, 3, 400, 0, 100)
# Есть конверсии, но CPA плохой — запрещать нельзя, только ставку понижать.
add("expensive.ru", 25000, 500, 20000, 2, 65)
for i in range(40):
    add(f"ordinary-{i}.ru", 8000, 120, 2600, 2, random.randint(30, 60))

bench = build_benchmarks(rows, rules["economics"]["target_cpa"])
results = assess(rows, metrika, bench, rules)
by = {r.row.placement: r for r in results}

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


check(by["trash-portal.ru"].verdict == Verdict.BLOCK, "явный слив должен уйти в запрет")
check(by["com.gamedev.puzzle"].verdict == Verdict.BLOCK, "мусорное приложение должно уйти в запрет")
check(by["tiny-site.ru"].verdict == Verdict.WATCH, "площадка с 3 кликами не должна судиться")
check(by["expensive.ru"].verdict != Verdict.BLOCK, "площадку с конверсиями нельзя запрещать")
check(by["niche-blog.ru"].verdict == Verdict.DONOR, "выгодная площадка должна быть донором")
check(by["news-portal.ru"].verdict in (Verdict.OK, Verdict.DONOR), "нормальная площадка не должна флагаться")

blocked = [r for r in results if r.verdict == Verdict.BLOCK]
cap = int(len(results) * rules["safety"]["max_block_share_per_run"])
check(len(blocked) <= cap, f"нарушен лимит на заход: {len(blocked)} > {cap}")

out = ROOT / "out"
out.mkdir(exist_ok=True)
html = build_html(results, bench, "dry-run", True, 90, [])
(out / "sample-report.html").write_text(html, encoding="utf-8")
write_csv(results, out / "sample.csv")

check("font-family: -apple-system, BlinkMacSystemFont" in html, "CSS повреждён форматированием чисел")
check("<td" in html and "trash-portal.ru" in html, "таблица не отрендерилась")

print(f"Площадок: {len(results)} | запрет: {len(blocked)} | лимит за заход: {cap}")
for r in blocked:
    print(f"  ЗАПРЕТ  {r.row.placement:<24} {r.row.cost:>8.0f} ₽  <- {r.triggers[0]}")
print(f"\nСредний CTR {bench.avg_ctr:.2f}% | средний CPA {bench.avg_cpa:.0f} ₽")

if fails:
    print("\nПРОВАЛЕНО:")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("\nВсе проверки пройдены.")
