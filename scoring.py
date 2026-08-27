"""Логика оценки площадок.

Главный принцип: абсолютные пороги вроде «CTR ниже 1% — отключать» бесполезны.
В РСЯ CTR 0,4% — норма. Значение имеет отклонение площадки от средних
по конкретному аккаунту при достаточной статистике.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from direct_api import PlacementRow
from metrika_api import MetrikaRow, normalize_placement


def _num(value: float) -> str:
    """Разряды неразрывным пробелом, не трогая запятые в тексте."""
    return f"{value:,.0f}".replace(",", "\u202f")


class Verdict(str, Enum):
    BLOCK = "Запретить"
    WATCH = "Под наблюдением"
    OK = "Норма"
    DONOR = "Донор"


@dataclass
class Benchmarks:
    """Средние по аккаунту — точка отсчёта для всех сравнений."""
    avg_ctr: float
    avg_cpa: float
    target_cpa: float
    total_cost: float
    total_conversions: float
    total_clicks: float
    total_impressions: float


@dataclass
class Assessment:
    row: PlacementRow
    metrika: MetrikaRow | None
    verdict: Verdict
    triggers: list[str] = field(default_factory=list)
    wasted_spend: float = 0.0

    @property
    def conversions(self) -> float:
        """Конверсии Метрики надёжнее — если они есть, берём их."""
        if self.metrika and self.metrika.goal_reaches > 0:
            return self.metrika.goal_reaches
        return self.row.conversions

    @property
    def bounce_rate(self) -> float:
        if self.metrika and self.metrika.visits > 0:
            return self.metrika.bounce_rate
        return self.row.bounce_rate

    @property
    def visits(self) -> float:
        if self.metrika:
            return self.metrika.visits
        return float(self.row.clicks)

    @property
    def cpa(self) -> float:
        conv = self.conversions
        return self.row.cost / conv if conv > 0 else 0.0


def build_benchmarks(rows: list[PlacementRow], target_cpa: float | None) -> Benchmarks:
    total_cost = sum(r.cost for r in rows)
    total_conv = sum(r.conversions for r in rows)
    total_clicks = sum(r.clicks for r in rows)
    total_impr = sum(r.impressions for r in rows)

    avg_ctr = (total_clicks / total_impr * 100) if total_impr else 0.0
    avg_cpa = (total_cost / total_conv) if total_conv else 0.0

    return Benchmarks(
        avg_ctr=avg_ctr,
        avg_cpa=avg_cpa,
        # Если целевой CPA не задан, ориентируемся на текущий средний.
        target_cpa=target_cpa if target_cpa else avg_cpa,
        total_cost=total_cost,
        total_conversions=total_conv,
        total_clicks=total_clicks,
        total_impressions=total_impr,
    )


def _is_significant(row: PlacementRow, bench: Benchmarks, cfg: dict) -> bool:
    """Хватает ли данных, чтобы вообще судить площадку."""
    sig = cfg.get("significance", {})
    if row.impressions >= sig.get("min_impressions", 100) and row.clicks >= sig.get("min_clicks", 10):
        return True
    spend_threshold = sig.get("min_spend_in_cpa", 2.0) * bench.target_cpa
    return bool(bench.target_cpa and row.cost >= spend_threshold)


def assess(
    rows: list[PlacementRow],
    metrika: dict[str, MetrikaRow],
    bench: Benchmarks,
    cfg: dict,
) -> list[Assessment]:
    rules = cfg.get("rules", {})
    safety = cfg.get("safety", {})
    whitelist = {normalize_placement(w) for w in safety.get("whitelist") or []}
    min_triggers = rules.get("min_triggers_to_block", 2)

    results: list[Assessment] = []

    for row in rows:
        key = normalize_placement(row.placement)
        item = Assessment(row=row, metrika=metrika.get(key), verdict=Verdict.OK)

        if key in whitelist:
            item.verdict = Verdict.OK
            item.triggers.append("В белом списке — не трогаем")
            results.append(item)
            continue

        if not _is_significant(row, bench, cfg):
            item.verdict = Verdict.WATCH
            item.triggers.append(
                f"Мало данных: {row.impressions} показов, {row.clicks} кликов — "
                "судить рано"
            )
            results.append(item)
            continue

        conv = item.conversions
        triggers: list[str] = []

        # 1. Потрачено ощутимо, конверсий ноль.
        zero_conv_threshold = rules.get("zero_conversion_spend_in_cpa", 2.0) * bench.target_cpa
        if conv == 0 and bench.target_cpa and row.cost >= zero_conv_threshold:
            triggers.append(
                f"{_num(row.cost)} ₽ потрачено, конверсий ноль "
                f"(порог {_num(zero_conv_threshold)} ₽)"
            )

        # 2. CPA сильно хуже целевого.
        ratio = rules.get("cpa_worse_than_avg_ratio", 2.0)
        if conv > 0 and bench.target_cpa and item.cpa > bench.target_cpa * ratio:
            triggers.append(
                f"CPA {_num(item.cpa)} ₽ против целевых {_num(bench.target_cpa)} ₽ "
                f"(хуже в {item.cpa / bench.target_cpa:.1f} раза)"
            )

        # 3. Отказы — сильнейший индикатор фрода и мискликов.
        if (
            item.bounce_rate > rules.get("bounce_rate_max", 70.0)
            and item.visits >= rules.get("bounce_rate_min_visits", 30)
        ):
            triggers.append(
                f"Отказы {item.bounce_rate:.0f}% при {item.visits:.0f} визитах"
            )

        # 4a. CTR аномально низкий — объявление не попадает в аудиторию.
        share = rules.get("ctr_below_avg_share", 0.3)
        if bench.avg_ctr and row.ctr < bench.avg_ctr * share:
            triggers.append(
                f"CTR {row.ctr:.2f}% против среднего {bench.avg_ctr:.2f}% по аккаунту"
            )

        # 4b. CTR аномально высокий при нуле конверсий — почти всегда накрутка.
        high_ratio = rules.get("ctr_above_avg_ratio", 5.0)
        if bench.avg_ctr and row.ctr > bench.avg_ctr * high_ratio and conv == 0:
            triggers.append(
                f"Подозрительный CTR {row.ctr:.2f}% (в {row.ctr / bench.avg_ctr:.0f} раз "
                "выше среднего) при нулевых конверсиях — похоже на накрутку"
            )

        # 5. Мобильные приложения без конверсий — источник случайных кликов.
        if rules.get("flag_zero_conversion_apps", True) and row.is_mobile_app and conv == 0:
            triggers.append("Мобильное приложение с нулевой конверсией")

        item.triggers = triggers

        is_donor = (
            conv > 0
            and not triggers
            and bench.target_cpa
            and item.cpa < bench.target_cpa * 0.7
        )

        if is_donor:
            item.verdict = Verdict.DONOR
            item.triggers.append(
                f"CPA {_num(item.cpa)} ₽ — заметно лучше целевого "
                f"{_num(bench.target_cpa)} ₽. Кандидат на повышение ставки"
            )
        elif conv > 0 and safety.get("never_block_with_conversions", True):
            # Площадка что-то приносит. Дешевле понизить ставку, чем отрезать совсем.
            item.verdict = Verdict.WATCH if triggers else Verdict.OK
            if triggers:
                item.triggers.append(
                    "Есть конверсии — вместо запрета понизь ставку на этой площадке"
                )
        elif len(triggers) >= min_triggers:
            item.verdict = Verdict.BLOCK
            item.wasted_spend = row.cost
        elif triggers:
            item.verdict = Verdict.WATCH

        results.append(item)

    return _apply_safety_cap(results, cfg)


def _apply_safety_cap(results: list[Assessment], cfg: dict) -> list[Assessment]:
    """Не отключаем больше заданной доли площадок за один заход.

    Массовое отключение сбивает обучение автостратегий: кампания теряет
    значительную часть инвентаря разом и заново уходит в обучение.
    """
    safety = cfg.get("safety", {})
    max_share = safety.get("max_block_share_per_run", 0.25)
    if not results or max_share >= 1:
        return results

    blocked = [r for r in results if r.verdict == Verdict.BLOCK]
    cap = int(len(results) * max_share)
    if len(blocked) <= cap:
        return results

    # Оставляем в запрете самые дорогие — они жгут больше всего бюджета.
    blocked.sort(key=lambda r: r.wasted_spend, reverse=True)
    for item in blocked[cap:]:
        item.verdict = Verdict.WATCH
        item.triggers.append(
            f"Отложено до следующего запуска: лимит {int(max_share * 100)}% "
            "площадок за заход, чтобы не сбить обучение стратегии"
        )
    return results
