"""Точка входа. Запускается вручную или по расписанию на Render.

Порядок работы:
  1. Забрать отчёт по площадкам из API Директа.
  2. Обогатить данными Метрики (если подключена).
  3. Посчитать средние по аккаунту и оценить каждую площадку.
  4. Собрать отчёт.
  5. В режиме apply — записать запреты в кампании.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ROOT, ConfigError, load_settings  # noqa: E402
from direct_api import DirectApiError, DirectClient  # noqa: E402
from metrika_api import MetrikaApiError, MetrikaClient  # noqa: E402
from notify import send_telegram  # noqa: E402
from report import build_html, write_csv  # noqa: E402
from scoring import Verdict, assess, build_benchmarks  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("assistant")

# В Cloud Functions файловая система только для чтения, писать можно лишь в /tmp.
OUT_DIR = Path(os.getenv("OUT_DIR") or (ROOT / "out"))


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    rules = settings.rules
    days = rules.get("period", {}).get("days", 90)
    econ = rules.get("economics", {})

    log.info("Режим: %s", "ЗАПИСЬ В ДИРЕКТ" if settings.apply_changes else "только анализ")

    # --- 1. Директ ---
    direct = DirectClient(settings.direct_token, settings.direct_client_login)
    try:
        log.info("Запрашиваю отчёт по площадкам за %s дней...", days)
        rows = direct.fetch_placements(days=days, include_vat=econ.get("include_vat", True))
    except DirectApiError as exc:
        log.error("Директ: %s", exc)
        return 3

    if not rows:
        log.warning(
            "Директ вернул пустой отчёт. Вероятные причины: за период не было показов "
            "в сетях, либо у аккаунта нет кампаний РСЯ."
        )
        return 0
    log.info("Получено строк по площадкам: %s", len(rows))

    # --- 2. Метрика ---
    metrika_data: dict = {}
    if settings.metrika_enabled:
        try:
            log.info("Запрашиваю поведение по площадкам из Метрики...")
            client = MetrikaClient(
                settings.metrika_token, settings.metrika_counter_id, settings.metrika_goal_ids
            )
            metrika_data = client.fetch_placements(days=days)
        except MetrikaApiError as exc:
            # Метрика — обогащение, а не обязательный источник. Не роняем прогон.
            log.warning("Метрика недоступна, продолжаю на данных Директа: %s", exc)
    else:
        log.info("Метрика не настроена — работаю на данных одного Директа.")

    # --- 3. Оценка ---
    bench = build_benchmarks(rows, econ.get("target_cpa"))
    if not bench.target_cpa:
        log.warning(
            "Целевой CPA не задан и посчитать средний не из чего (нет конверсий). "
            "Часть правил будет отключена. Заполни economics.target_cpa в config.yaml."
        )
    log.info(
        "Средние по аккаунту: CTR %.2f%%, CPA %.0f ₽, целевой CPA %.0f ₽",
        bench.avg_ctr, bench.avg_cpa, bench.target_cpa,
    )

    results = assess(rows, metrika_data, bench, rules)
    blocked = [r for r in results if r.verdict == Verdict.BLOCK]
    wasted = sum(r.row.cost for r in blocked)
    log.info("На запрет: %s площадок, расход по ним %.0f ₽", len(blocked), wasted)

    # --- 4. Запись в Директ ---
    applied: list[str] = []
    if settings.apply_changes and blocked:
        applied = apply_blocks(direct, blocked, rules)

    # --- 5. Отчёт ---
    OUT_DIR.mkdir(exist_ok=True)
    html_path = OUT_DIR / "otchet-ploshchadki.html"
    csv_path = OUT_DIR / "ploshchadki.csv"

    html_path.write_text(
        build_html(results, bench, settings.mode, bool(metrika_data), days, applied),
        encoding="utf-8",
    )
    write_csv(results, csv_path)
    log.info("Отчёт готов: %s", html_path)

    if settings.telegram_bot_token and settings.telegram_chat_id:
        share = (wasted / bench.total_cost * 100) if bench.total_cost else 0
        summary = (
            f"<b>Чистка площадок РСЯ</b>\n"
            f"Проанализировано: {len(results)} площадок\n"
            f"На запрет: <b>{len(blocked)}</b>\n"
            f"Сливают: <b>{wasted:,.0f} ₽</b> ({share:.1f}% расхода)\n".replace(",", " ")
            + ("Запреты записаны в Директ." if applied else "Режим анализа, изменений нет.")
        )
        send_telegram(settings.telegram_bot_token, settings.telegram_chat_id, summary, html_path)

    return 0


def apply_blocks(direct: DirectClient, blocked, rules) -> list[str]:
    """Дописывает площадки в списки запрещённых, кампания за кампанией."""
    safety = rules.get("safety", {})
    limit = safety.get("max_excluded_per_campaign", 900)
    excluded_campaigns = {str(c).lower() for c in safety.get("excluded_campaigns") or []}

    try:
        campaigns = direct.get_campaigns()
    except DirectApiError as exc:
        log.error("Не удалось получить кампании, запись отменена: %s", exc)
        return []

    current = {
        str(c["Id"]): (c.get("ExcludedSites", {}) or {}).get("Items", []) or []
        for c in campaigns
    }
    names = {str(c["Id"]): c.get("Name", "") for c in campaigns}

    by_campaign: dict[str, list[str]] = {}
    for item in blocked:
        by_campaign.setdefault(item.row.campaign_id, []).append(item.row.placement)

    updated: list[str] = []
    for campaign_id, sites in by_campaign.items():
        name = names.get(campaign_id, "")
        if campaign_id in excluded_campaigns or any(
            token and token in name.lower() for token in excluded_campaigns
        ):
            log.info("Кампания %s (%s) в списке неприкосновенных — пропускаю", campaign_id, name)
            continue
        if campaign_id not in current:
            log.warning("Кампания %s не найдена среди активных — пропускаю", campaign_id)
            continue

        # Список заменяется целиком, поэтому объединяем со старым.
        existing = current[campaign_id]
        merged = list(dict.fromkeys(existing + sites))

        if len(merged) > limit:
            free = max(0, limit - len(existing))
            log.warning(
                "Кампания %s (%s): лимит запрещённых площадок почти исчерпан "
                "(%s из %s). Добавлю только %s новых. Стоит проредить старый список.",
                campaign_id, name, len(existing), limit, free,
            )
            merged = existing + [s for s in sites if s not in existing][:free]

        if merged == existing:
            continue

        try:
            direct.update_excluded_sites(campaign_id, merged)
            added = len(merged) - len(existing)
            log.info("Кампания %s (%s): добавлено %s площадок", campaign_id, name, added)
            updated.append(campaign_id)
        except DirectApiError as exc:
            log.error("Кампания %s: запись не удалась — %s", campaign_id, exc)

    return updated


if __name__ == "__main__":
    raise SystemExit(main())
