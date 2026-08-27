"""Клиент Reporting API Яндекс Метрики.

Зачем он нужен, если Директ и так отдаёт отказы с конверсиями: Директ показывает
конверсии по своей модели атрибуции и только по целям, привязанным к кампании.
Метрика даёт поведение по конкретным целям и более честные отказы в разрезе
площадки Директа. Если Метрика недоступна, ассистент продолжает работать
на данных одного Директа — просто с меньшей точностью.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import requests

log = logging.getLogger(__name__)

STAT_URL = "https://api-metrika.yandex.net/stat/v1/data"

# Измерение "Площадка Директа" в модели атрибуции "последний значимый переход".
PLACEMENT_DIMENSION = "ym:s:lastsignDirectPlatform"

MAX_ROWS = 100_000
PAGE_LIMIT = 10_000


class MetrikaApiError(RuntimeError):
    pass


@dataclass
class MetrikaRow:
    placement: str
    visits: float
    bounce_rate: float
    page_depth: float
    avg_duration: float
    goal_reaches: float


class MetrikaClient:
    def __init__(self, token: str, counter_id: str, goal_ids: list[str] | None = None):
        self._token = token
        self._counter_id = counter_id
        self._goal_ids = goal_ids or []
        self._session = requests.Session()

    def _metrics(self) -> list[str]:
        metrics = [
            "ym:s:visits",
            "ym:s:bounceRate",
            "ym:s:pageDepth",
            "ym:s:avgVisitDurationSeconds",
        ]
        # Каждая цель — отдельная метрика достижений.
        for goal_id in self._goal_ids:
            metrics.append(f"ym:s:goal{goal_id}reaches")
        return metrics

    def fetch_placements(self, days: int) -> dict[str, MetrikaRow]:
        date_to = date.today() - timedelta(days=1)
        date_from = date_to - timedelta(days=days - 1)
        metrics = self._metrics()

        rows: dict[str, MetrikaRow] = {}
        offset = 1

        while offset <= MAX_ROWS:
            params = {
                "ids": self._counter_id,
                "metrics": ",".join(metrics),
                "dimensions": PLACEMENT_DIMENSION,
                "date1": date_from.isoformat(),
                "date2": date_to.isoformat(),
                "limit": PAGE_LIMIT,
                "offset": offset,
                "accuracy": "full",
            }
            resp = self._session.get(
                STAT_URL,
                params=params,
                headers={"Authorization": f"OAuth {self._token}"},
                timeout=120,
            )

            if resp.status_code == 401:
                raise MetrikaApiError(
                    "Метрика вернула 401: токен недействителен. Проверь METRIKA_TOKEN."
                )
            if resp.status_code == 403:
                raise MetrikaApiError(
                    "Метрика вернула 403: у токена нет доступа к счётчику "
                    f"{self._counter_id}. Проверь METRIKA_COUNTER_ID и права доступа."
                )
            if resp.status_code != 200:
                raise MetrikaApiError(
                    f"Метрика вернула {resp.status_code}: {resp.text[:500]}"
                )

            payload = resp.json()
            data = payload.get("data", [])
            if not data:
                break

            for item in data:
                name = (item.get("dimensions") or [{}])[0].get("name") or ""
                name = name.strip()
                if not name:
                    continue
                values = item.get("metrics", [])
                goal_total = sum(values[4:]) if len(values) > 4 else 0.0
                rows[_normalize(name)] = MetrikaRow(
                    placement=name,
                    visits=_at(values, 0),
                    bounce_rate=_at(values, 1),
                    page_depth=_at(values, 2),
                    avg_duration=_at(values, 3),
                    goal_reaches=goal_total,
                )

            if len(data) < PAGE_LIMIT:
                break
            offset += PAGE_LIMIT

        log.info("Метрика: получено площадок — %s", len(rows))
        return rows


def _at(values: list, index: int) -> float:
    try:
        return float(values[index])
    except (IndexError, TypeError, ValueError):
        return 0.0


def normalize_placement(name: str) -> str:
    return _normalize(name)


def _normalize(name: str) -> str:
    """Директ и Метрика пишут домены немного по-разному — приводим к общему виду."""
    name = (name or "").strip().lower()
    for prefix in ("https://", "http://", "www."):
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name.rstrip("/")
