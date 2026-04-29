from __future__ import annotations

import json
import logging

import httpx

log = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self, bot_token: str, client: httpx.AsyncClient):
        self._token = bot_token
        self._client = client
        self._base = f"https://api.telegram.org/bot{bot_token}"

    async def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str = "HTML",
        disable_web_page_preview: bool = False,
    ) -> str | None:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        r = await self._client.post(f"{self._base}/sendMessage", json=payload)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram_error:{data}")
        return str(data["result"]["message_id"]) if data.get("result") else None

    async def send_photo(
        self,
        *,
        chat_id: str,
        photo_url: str,
        caption: str,
        reply_markup: dict | None = None,
        parse_mode: str = "HTML",
    ) -> str | None:
        payload = {
            "chat_id": chat_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": parse_mode,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        r = await self._client.post(f"{self._base}/sendPhoto", json=payload)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram_error:{data}")
        return str(data["result"]["message_id"]) if data.get("result") else None

    async def send_photo_upload(
        self,
        *,
        chat_id: str,
        photo_bytes: bytes,
        filename: str = "photo.jpg",
        content_type: str = "image/jpeg",
        caption: str,
        reply_markup: dict | None = None,
        parse_mode: str = "HTML",
    ) -> str | None:
        fields: list = [
            ("chat_id", (None, chat_id)),
            ("caption", (None, caption)),
            ("parse_mode", (None, parse_mode)),
            ("photo", (filename, photo_bytes, content_type)),
        ]
        if reply_markup:
            fields.append(("reply_markup", (None, json.dumps(reply_markup))))
        r = await self._client.post(f"{self._base}/sendPhoto", files=fields)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram_error:{data}")
        return str(data["result"]["message_id"]) if data.get("result") else None


def build_inline_button(label: str, url: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": label, "url": url},
            ]
        ]
    }
