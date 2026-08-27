"""Отправка короткой сводки в Telegram. Полностью опциональна."""
from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)


def send_telegram(bot_token: str, chat_id: str, text: str, html_path=None) -> bool:
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=30,
        )
        if resp.status_code != 200:
            log.warning("Telegram отклонил сообщение: %s", resp.text[:300])
            return False

        if html_path:
            with open(html_path, "rb") as fh:
                requests.post(
                    f"https://api.telegram.org/bot{bot_token}/sendDocument",
                    data={"chat_id": chat_id},
                    files={"document": ("otchet-ploshchadki.html", fh, "text/html")},
                    timeout=60,
                )
        return True
    except requests.RequestException as exc:
        log.warning("Не удалось отправить в Telegram: %s", exc)
        return False
