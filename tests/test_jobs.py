"""JobManager の実行制御(特に中止)の単体テスト。

パイプラインの中身は差し替え、キューイングと中止の扱いだけを見る。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from seminar_report.models import JobStatus, JobStep
from seminar_report.pipeline import PipelineOptions
from seminar_report.web import jobs as jobs_module
from seminar_report.web.jobs import JobManager


def _wait_until(condition, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def test_cancel_while_queued_never_starts_processing(monkeypatch, tmp_path: Path) -> None:
    """同時実行の上限で待たされているジョブを中止したら、そもそも処理を始めないこと。

    ここが効かないと、中止したはずの動画が順番待ちの後に走り出してしまう。
    """
    first_started = threading.Event()
    release = threading.Event()
    processed: list[str] = []

    def fake_run_pipeline(video, output_dir, options, on_progress=None):
        processed.append(video.name)
        if video.name == "a.mp4":
            first_started.set()
            release.wait(10)
        return object()

    monkeypatch.setattr(jobs_module, "run_pipeline", fake_run_pipeline)

    manager = JobManager(max_workers=1)
    try:
        job_a = manager.create(tmp_path / "a.mp4", tmp_path / "out-a", PipelineOptions())
        assert first_started.wait(10), "1 本目が走り始めませんでした"

        # 1 本目が専有している間に投入されるので、2 本目はキューで待つ
        job_b = manager.create(tmp_path / "b.mp4", tmp_path / "out-b", PipelineOptions())
        assert job_b.status is JobStatus.PENDING
        assert manager.cancel(job_b) is True

        release.set()
        assert _wait_until(lambda: job_a.finished and job_b.finished)
    finally:
        release.set()
        manager.shutdown()

    assert job_b.status is JobStatus.CANCELLED
    assert processed == ["a.mp4"], "中止したはずの動画が処理された"


def test_cancel_during_run_is_not_recorded_as_failure(monkeypatch, tmp_path: Path) -> None:
    """進捗報告の関門で降りたとき、失敗ではなく中止として記録されること。"""
    running = threading.Event()

    def fake_run_pipeline(video, output_dir, options, on_progress=None):
        running.set()
        for index in range(200):
            # 実際のパイプラインと同じく、進捗を報告しながら進む
            on_progress(JobStep.TRANSCRIBE, index / 200, f"{index}")
            time.sleep(0.05)
        return object()

    monkeypatch.setattr(jobs_module, "run_pipeline", fake_run_pipeline)

    manager = JobManager(max_workers=1)
    try:
        job = manager.create(tmp_path / "a.mp4", tmp_path / "out", PipelineOptions())
        assert running.wait(10)
        assert manager.cancel(job) is True
        assert _wait_until(lambda: job.finished)
    finally:
        manager.shutdown()

    assert job.status is JobStatus.CANCELLED
    assert job.error is None
    assert job.traceback is None
    assert job.result is None


def test_cancel_returns_false_for_finished_job(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        jobs_module, "run_pipeline", lambda video, output_dir, options, on_progress=None: object()
    )

    manager = JobManager(max_workers=1)
    try:
        job = manager.create(tmp_path / "a.mp4", tmp_path / "out", PipelineOptions())
        assert _wait_until(lambda: job.finished)
        assert job.status is JobStatus.DONE
        assert manager.cancel(job) is False
    finally:
        manager.shutdown()
