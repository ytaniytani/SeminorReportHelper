"""Web UI の API を、実際のジョブ実行を通して確認する。

Whisper と LLM だけをモックし、アップロード → 生成 → 編集 → 出力までを
HTTP 経由で通す。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from conftest import ScriptedProvider
from fastapi.testclient import TestClient

from seminar_report import pipeline as pipeline_module
from seminar_report.models import Transcript
from seminar_report.web.app import app


@pytest.fixture
def client(monkeypatch, tmp_path: Path, transcript: Transcript) -> TestClient:
    monkeypatch.setattr(pipeline_module, "transcribe", lambda *a, **k: transcript)
    monkeypatch.setattr(pipeline_module, "get_provider", lambda *a, **k: ScriptedProvider())

    settings = pipeline_module.get_settings()
    monkeypatch.setattr(settings, "jobs_dir", tmp_path / "jobs")
    return TestClient(app)


def _wait_for_job(client: TestClient, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["status"] in ("done", "failed"):
            return data
        time.sleep(0.2)
    raise AssertionError("ジョブが時間内に終わりませんでした")


def _submit(client: TestClient, video: Path, **form) -> str:
    with video.open("rb") as fh:
        response = client.post(
            "/api/jobs",
            files={"video": (video.name, fh, "video/mp4")},
            data={"detail": "standard", **form},
        )
    assert response.status_code == 200, response.text
    return response.json()["job_id"]


def test_index_and_config_are_served(client: TestClient) -> None:
    assert client.get("/").status_code == 200
    config = client.get("/api/config").json()
    assert config["providers"] == ["claude", "openai", "ollama"]
    assert [p["value"] for p in config["presets"]] == ["brief", "standard", "detailed"]


def test_full_job_flow(client: TestClient, sample_video: Path) -> None:
    job_id = _submit(client, sample_video)
    data = _wait_for_job(client, job_id)

    assert data["status"] == "done", data["error"]
    report = data["report"]
    assert report["title"] == "社内AI活用セミナー"
    assert len(report["sections"]) == 2
    assert report["char_count"] > 0

    # 画像が生成され、配信できる
    included = [c for c in report["captures"] if c["included"]]
    assert included
    image = client.get(included[0]["url"])
    assert image.status_code == 200
    assert len(image.content) > 0

    # ZIP がダウンロードできる
    export = client.get(f"/api/jobs/{job_id}/export")
    assert export.status_code == 200
    assert export.content[:2] == b"PK"


def test_patch_applies_edits(client: TestClient, sample_video: Path) -> None:
    job_id = _submit(client, sample_video)
    report = _wait_for_job(client, job_id)["report"]

    excluded = [c["marker_id"] for c in report["captures"]]
    response = client.patch(
        f"/api/jobs/{job_id}",
        json={
            "title": "編集後のタイトル",
            "overview": "書き換えた概要。",
            "key_points": ["新しい要点"],
            "sections": [{"title": "新見出し", "body": "書き換えた本文。"}],
            "excluded_captures": excluded,
        },
    )
    assert response.status_code == 200

    updated = response.json()["report"]
    assert updated["title"] == "編集後のタイトル"
    assert updated["overview"] == "書き換えた概要。"
    assert updated["key_points"] == ["新しい要点"]
    assert updated["sections"][0]["title"] == "新見出し"
    assert all(not c["included"] for c in updated["captures"])

    # 出力ファイルにも反映されている
    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["report"]["title"] == "編集後のタイトル"


def test_swap_recovers_an_unselected_capture(client: TestClient, sample_video: Path) -> None:
    """自動選抜されなかったキャプチャも、候補を選べば採用できる。"""
    job_id = _submit(client, sample_video)
    report = _wait_for_job(client, job_id)["report"]

    unselected = [c for c in report["captures"] if not c["included"]]
    assert unselected, "テスト前提が崩れている(全て採用されてしまった)"
    target = unselected[0]
    assert target["candidate_count"] > 0, "候補が残っていない"

    response = client.post(
        f"/api/jobs/{job_id}/swap",
        json={"marker_id": target["marker_id"], "candidate_index": 1},
    )
    assert response.status_code == 200

    after = client.get(f"/api/jobs/{job_id}").json()["report"]
    recovered = next(c for c in after["captures"] if c["marker_id"] == target["marker_id"])
    assert recovered["included"]
    assert client.get(recovered["url"]).status_code == 200


def test_swap_rejects_out_of_range_index(client: TestClient, sample_video: Path) -> None:
    job_id = _submit(client, sample_video)
    report = _wait_for_job(client, job_id)["report"]
    response = client.post(
        f"/api/jobs/{job_id}/swap",
        json={"marker_id": report["captures"][0]["marker_id"], "candidate_index": 999},
    )
    assert response.status_code == 400


def test_missing_job_returns_404(client: TestClient) -> None:
    assert client.get("/api/jobs/unknown").status_code == 404
    assert client.get("/api/jobs/unknown/export").status_code == 404


def test_job_list_allows_recovery_after_disconnect(
    client: TestClient, sample_video: Path
) -> None:
    """接続が切れても、一覧から走行中/完了済みのジョブに戻れること。"""
    job_id = _submit(client, sample_video)
    _wait_for_job(client, job_id)

    jobs = client.get("/api/jobs").json()["jobs"]
    entry = next(j for j in jobs if j["job_id"] == job_id)

    assert entry["status"] == "done"
    assert entry["title"] == "社内AI活用セミナー"
    assert entry["video"] == sample_video.name


def test_job_list_is_newest_first(client: TestClient, sample_video: Path) -> None:
    first = _submit(client, sample_video)
    _wait_for_job(client, first)
    second = _submit(client, sample_video)
    _wait_for_job(client, second)

    ids = [j["job_id"] for j in client.get("/api/jobs").json()["jobs"]]
    assert ids.index(second) < ids.index(first)


def test_events_stream_reports_completion(client: TestClient, sample_video: Path) -> None:
    """SSE が進捗イベントと終了通知を届けること。"""
    job_id = _submit(client, sample_video)
    with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())

    assert "event: end" in body
    assert '"status": "done"' in body or '"status":"done"' in body
    # 各工程の進捗が流れている
    assert '"step":"render"' in body


def test_image_path_traversal_is_blocked(client: TestClient, sample_video: Path) -> None:
    job_id = _submit(client, sample_video)
    _wait_for_job(client, job_id)
    response = client.get(f"/api/jobs/{job_id}/images/..%2f..%2freport.json")
    assert response.status_code == 404


def test_publish_requires_configuration(client: TestClient, sample_video: Path) -> None:
    job_id = _submit(client, sample_video)
    _wait_for_job(client, job_id)
    response = client.post(f"/api/jobs/{job_id}/publish", json={"space_key": "ENG"})
    # .env 未設定の環境では 400 で理由が返る
    assert response.status_code == 400
    assert "CONFLUENCE" in response.json()["detail"]
