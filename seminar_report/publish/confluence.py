"""Confluence Cloud への投稿。

画像は添付ファイルとして参照するが、Confluence は「存在しない添付」への
参照を許さない。そのため必ず次の 3 段階に分ける。

  1. 画像参照を除いた本文でページを作成する
  2. 画像を添付する
  3. 画像参照を含む本文でページを更新する

添付 API は v2 に存在しないため、そこだけ v1 エンドポイントを使う。
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path

import httpx

from seminar_report.config import get_settings
from seminar_report.models import Report
from seminar_report.render.confluence_storage import (
    render_storage,
    render_storage_without_images,
)


class ConfluenceError(RuntimeError):
    pass


@dataclass
class PublishResult:
    page_id: str
    url: str
    attached: int


class ConfluenceClient:
    def __init__(
        self,
        base_url: str | None = None,
        email: str | None = None,
        api_token: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        settings = get_settings()
        base_url = base_url or settings.confluence_base_url
        email = email or settings.confluence_email
        api_token = api_token or settings.confluence_api_token

        if not (base_url and email and api_token):
            raise ConfluenceError(
                "Confluence の接続情報が不足しています。.env の "
                "CONFLUENCE_BASE_URL / CONFLUENCE_EMAIL / CONFLUENCE_API_TOKEN "
                "を設定してください。"
            )

        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(auth=(email, api_token), timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ConfluenceClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = self._client.request(method, f"{self.base_url}{path}", **kwargs)
        if response.status_code >= 400:
            raise ConfluenceError(
                f"Confluence API エラー ({method} {path}): "
                f"{response.status_code} {response.text[:300]}"
            )
        return response

    def resolve_space_id(self, space_key: str) -> str:
        data = self._request(
            "GET", "/wiki/api/v2/spaces", params={"keys": space_key, "limit": 1}
        ).json()
        results = data.get("results") or []
        if not results:
            raise ConfluenceError(f"スペースが見つかりません: {space_key}")
        return str(results[0]["id"])

    def create_page(
        self,
        space_id: str,
        title: str,
        storage_body: str,
        parent_id: str | None = None,
    ) -> dict:
        payload: dict = {
            "spaceId": space_id,
            "status": "current",
            "title": title,
            "body": {"representation": "storage", "value": storage_body},
        }
        if parent_id:
            payload["parentId"] = parent_id
        return self._request("POST", "/wiki/api/v2/pages", json=payload).json()

    def attach_file(self, page_id: str, path: Path) -> None:
        """ページに画像を添付する。同名があれば置き換える。"""
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as fh:
            self._request(
                "PUT",
                f"/wiki/rest/api/content/{page_id}/child/attachment",
                # CSRF チェックを外すためのヘッダ。添付 API では必須。
                headers={"X-Atlassian-Token": "nocheck"},
                files={"file": (path.name, fh, media_type)},
                data={"minorEdit": "true"},
            )

    def update_page(
        self, page_id: str, title: str, storage_body: str, version: int
    ) -> dict:
        payload = {
            "id": page_id,
            "status": "current",
            "title": title,
            "body": {"representation": "storage", "value": storage_body},
            "version": {"number": version, "message": "画像を追加"},
        }
        return self._request("PUT", f"/wiki/api/v2/pages/{page_id}", json=payload).json()

    def page_url(self, page: dict) -> str:
        links = page.get("_links") or {}
        webui = links.get("webui") or ""
        return f"{self.base_url}/wiki{webui}" if webui else f"{self.base_url}/wiki"


def publish_report(
    report: Report,
    space_key: str | None = None,
    title: str | None = None,
    parent_id: str | None = None,
    client: ConfluenceClient | None = None,
) -> PublishResult:
    """レポートを Confluence の新規ページとして公開する。"""
    settings = get_settings()
    space_key = space_key or settings.confluence_space_key
    if not space_key:
        raise ConfluenceError("スペースキーが指定されていません。")

    owns_client = client is None
    client = client or ConfluenceClient()

    try:
        space_id = client.resolve_space_id(space_key)

        images = [
            c.image_path
            for c in report.captures
            if c.included and c.image_path and c.image_path.exists()
        ]

        # 1. 画像参照なしで作成
        body_first = (
            render_storage_without_images(report) if images else render_storage(report)
        )
        page = client.create_page(
            space_id, title or report.title, body_first, parent_id=parent_id
        )
        page_id = str(page["id"])

        if not images:
            return PublishResult(page_id=page_id, url=client.page_url(page), attached=0)

        # 2. 添付
        for path in images:
            client.attach_file(page_id, path)

        # 3. 画像参照を含む本文で更新
        current_version = int((page.get("version") or {}).get("number", 1))
        updated = client.update_page(
            page_id, title or report.title, render_storage(report), current_version + 1
        )
        return PublishResult(
            page_id=page_id, url=client.page_url(updated), attached=len(images)
        )
    finally:
        if owns_client:
            client.close()
