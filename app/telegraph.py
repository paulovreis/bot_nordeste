from __future__ import annotations

import logging
from datetime import datetime

import httpx

log = logging.getLogger(__name__)


class TelegraphClient:
    def __init__(self, access_token: str, client: httpx.AsyncClient):
        self._token = access_token
        self._client = client

    async def create_page(
        self,
        *,
        title: str,
        author_name: str,
        content_nodes: list[dict],
    ) -> str:
        # https://telegra.ph/api#createPage
        payload = {
            "access_token": self._token,
            "title": title,
            "author_name": author_name,
            "content": content_nodes,
            "return_content": False,
        }
        r = await self._client.post("https://api.telegra.ph/createPage", json=payload)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegraph_error:{data}")
        return data["result"]["url"]


def build_content(
    *,
    title: str,
    source_url: str,
    source_name: str,
    published_at: datetime,
    snippet: str,
    image_url: str | None,
) -> list[dict]:
    # Telegraph content format: list of Node objects.
    published_str = published_at.strftime("%d/%m/%Y %H:%M")

    nodes: list[dict] = []
    if image_url:
        nodes.append({"tag": "img", "attrs": {"src": image_url}})

    nodes.append({"tag": "p", "children": [snippet]})

    nodes.append(
        {
            "tag": "p",
            "children": [
                {"tag": "b", "children": ["Fonte: "]},
                source_name,
                " — ",
                {"tag": "b", "children": ["Publicado: "]},
                published_str,
            ],
        }
    )

    nodes.append(
        {
            "tag": "p",
            "children": [
                {"tag": "a", "attrs": {"href": source_url}, "children": ["Leia na fonte"]}
            ],
        }
    )

    return nodes
