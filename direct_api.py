"""Клиент API Яндекс Директа v5: отчёт по площадкам и запись запрещённых площадок."""
from __future__ import annotations

import csv
import io
import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.direct.yandex.com/json/v5"
REPORTS_URL = f"{BASE_URL}/reports"
CAMPAIGNS_URL = f"{BASE_URL}/campaigns"

# Поля отчёта по площадкам. Placement — домен или пакет мобильного приложения.
REPORT_FIELDS = [
    "CampaignId",
    "CampaignName",
    "Placement",
    "AdNetworkType",
    "Impressions",
    "Clicks",
    "Ctr",
    "Cost",
    "AvgCpc",
    "Conversions",
    "CostPerConversion",
    "ConversionRate",
    "BounceRate",
    "AvgPageviews",
]

# Максимум ожидания оффлайн-отчёта: Директ ставит большие отчёты в очередь.
MAX_REPORT_WAIT_SECONDS = 900


class DirectApiError(RuntimeError):
    pass


@dataclass
class PlacementRow:
    campaign_id: str
    campaign_name: str
    placement: str
    network: str
    impressions: int
    clicks: int
    ctr: float
    cost: float
    avg_cpc: float
    conversions: float
    cost_per_conversion: float
    conversion_rate: float
    bounce_rate: float
    avg_pageviews: float

    @property
    def is_mobile_app(self) -> bool:
        """Мобильные приложения приходят как пакеты вида com.vendor.app или id123456."""
        p = self.placement.lower()
        return p.startswith("com.") or p.startswith("id") and p[2:].isdigit()


def _to_float(value: str) -> float:
    value = (value or "").strip().replace(",", ".").replace("--", "")
    if not value:
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _to_int(value: str) -> int:
    return int(_to_float(value))


class DirectClient:
    def __init__(self, token: str, client_login: str | None = None):
        self._token = token
        self._client_login = client_login
        self._session = requests.Session()

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept-Language": "ru",
            "Content-Type": "application/json; charset=utf-8",
        }
        if self._client_login:
            headers["Client-Login"] = self._client_login
        if extra:
            headers.update(extra)
        return headers

    # ---------- отчёты ----------

    def fetch_placements(self, days: int, include_vat: bool = True) -> list[PlacementRow]:
        """Забирает отчёт по площадкам за последние `days` дней.

        Директ может отдать отчёт сразу (200) или поставить в очередь (201/202).
        Во втором случае повторяем запрос через интервал из заголовка retryIn.
        """
        date_to = date.today() - timedelta(days=1)
        date_from = date_to - timedelta(days=days - 1)

        body = {
            "params": {
                "SelectionCriteria": {
                    "DateFrom": date_from.isoformat(),
                    "DateTo": date_to.isoformat(),
                    # Только сети: поисковый трафик исказил бы средние по аккаунту.
                    "Filter": [
                        {
                            "Field": "AdNetworkType",
                            "Operator": "EQUALS",
                            "Values": ["AD_NETWORK"],
                        }
                    ],
                },
                "FieldNames": REPORT_FIELDS,
                "ReportName": f"placements-{date_from}-{date_to}-{int(time.time())}",
                "ReportType": "CUSTOM_REPORT",
                "DateRangeType": "CUSTOM_DATE",
                "Format": "TSV",
                "IncludeVAT": "YES" if include_vat else "NO",
            }
        }

        headers = self._headers(
            {
                "processingMode": "auto",
                "returnMoneyInMicros": "false",
                "skipReportHeader": "true",
                "skipReportSummary": "true",
            }
        )

        waited = 0
        while True:
            resp = self._session.post(REPORTS_URL, json=body, headers=headers, timeout=120)

            if resp.status_code == 200:
                return self._parse_tsv(resp.text)

            if resp.status_code in (201, 202):
                retry_in = int(resp.headers.get("retryIn", 10))
                waited += retry_in
                if waited > MAX_REPORT_WAIT_SECONDS:
                    raise DirectApiError(
                        "Директ так и не сформировал отчёт за отведённое время. "
                        "Попробуй уменьшить период в config.yaml."
                    )
                log.info("Отчёт в очереди, повтор через %s с (всего ждём %s с)", retry_in, waited)
                time.sleep(retry_in)
                continue

            if resp.status_code == 400:
                raise DirectApiError(
                    "Директ отклонил запрос отчёта (400). Чаще всего это несовместимый "
                    f"набор полей или неверный период. Ответ: {resp.text[:1000]}"
                )
            if resp.status_code == 401:
                raise DirectApiError(
                    "Директ вернул 401: токен недействителен или истёк. "
                    "Перевыпусти OAuth-токен и обнови переменную DIRECT_TOKEN."
                )
            if resp.status_code == 403:
                raise DirectApiError(
                    "Директ вернул 403: нет доступа к API. Проверь, что заявка на доступ "
                    "к API Директа одобрена, а для агентского аккаунта задан DIRECT_CLIENT_LOGIN."
                )
            raise DirectApiError(f"Неожиданный ответ Директа {resp.status_code}: {resp.text[:1000]}")

    @staticmethod
    def _parse_tsv(payload: str) -> list[PlacementRow]:
        reader = csv.DictReader(io.StringIO(payload), delimiter="\t")
        rows: list[PlacementRow] = []
        for raw in reader:
            placement = (raw.get("Placement") or "").strip()
            # Директ отдаёт "--" там, где площадка не определена.
            if not placement or placement == "--":
                continue
            rows.append(
                PlacementRow(
                    campaign_id=(raw.get("CampaignId") or "").strip(),
                    campaign_name=(raw.get("CampaignName") or "").strip(),
                    placement=placement,
                    network=(raw.get("AdNetworkType") or "").strip(),
                    impressions=_to_int(raw.get("Impressions", "")),
                    clicks=_to_int(raw.get("Clicks", "")),
                    ctr=_to_float(raw.get("Ctr", "")),
                    cost=_to_float(raw.get("Cost", "")),
                    avg_cpc=_to_float(raw.get("AvgCpc", "")),
                    conversions=_to_float(raw.get("Conversions", "")),
                    cost_per_conversion=_to_float(raw.get("CostPerConversion", "")),
                    conversion_rate=_to_float(raw.get("ConversionRate", "")),
                    bounce_rate=_to_float(raw.get("BounceRate", "")),
                    avg_pageviews=_to_float(raw.get("AvgPageviews", "")),
                )
            )
        return rows

    # ---------- кампании ----------

    def get_campaigns(self) -> list[dict]:
        """Список кампаний вместе с текущими запрещёнными площадками."""
        body = {
            "method": "get",
            "params": {
                "SelectionCriteria": {"States": ["ON", "SUSPENDED"]},
                "FieldNames": ["Id", "Name", "Type", "State", "ExcludedSites"],
            },
        }
        resp = self._session.post(CAMPAIGNS_URL, json=body, headers=self._headers(), timeout=60)
        data = resp.json()
        if "error" in data:
            raise DirectApiError(f"campaigns.get: {data['error']}")
        return data.get("result", {}).get("Campaigns", [])

    def update_excluded_sites(self, campaign_id: str, sites: list[str]) -> dict:
        """Записывает полный список запрещённых площадок кампании.

        ВАЖНО: список заменяется целиком, а не дополняется. Поэтому вызывающий код
        обязан передать объединение старых и новых площадок.
        """
        body = {
            "method": "update",
            "params": {
                "Campaigns": [{"Id": int(campaign_id), "ExcludedSites": {"Items": sites}}]
            },
        }
        resp = self._session.post(CAMPAIGNS_URL, json=body, headers=self._headers(), timeout=60)
        data = resp.json()
        if "error" in data:
            raise DirectApiError(f"campaigns.update: {data['error']}")
        return data.get("result", {})
