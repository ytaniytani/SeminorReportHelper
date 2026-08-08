"""ジョブ管理。ローカル単一ユーザー前提のプロセス内実装。

外部のキュー(Celery 等)は持ち込まない。スレッドプールで走らせ、進捗は
ジョブごとのイベント配列に追記し、SSE 側がそれを追いかける方式にしている。
"""

from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from seminar_report.models import JobStatus, JobStep, Progress
from seminar_report.pipeline import PipelineOptions, PipelineResult, run_pipeline


class JobCancelled(Exception):
    """利用者が中止を要求したことを表す。失敗として扱わないための専用の型。"""


@dataclass
class Job:
    id: str
    video_path: Path
    output_dir: Path
    options: PipelineOptions
    status: JobStatus = JobStatus.PENDING
    events: list[Progress] = field(default_factory=list)
    result: PipelineResult | None = None
    error: str | None = None
    traceback: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def add_event(self, step: JobStep, ratio: float, detail: str) -> None:
        with self._lock:
            self.events.append(Progress(step=step, ratio=ratio, detail=detail))

    def events_since(self, index: int) -> list[Progress]:
        with self._lock:
            return self.events[index:]

    def request_cancel(self) -> None:
        """中止を要求する。実際に止まるのは次の関門(進捗報告)を通ったとき。"""
        self._cancel.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def raise_if_cancelled(self) -> None:
        if self._cancel.is_set():
            raise JobCancelled

    @property
    def finished(self) -> bool:
        return self.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)


class JobManager:
    def __init__(self, max_workers: int = 2) -> None:
        self._jobs: dict[str, Job] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.Lock()

    def create(
        self, video_path: Path, output_dir: Path, options: PipelineOptions
    ) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            video_path=video_path,
            output_dir=output_dir,
            options=options,
        )
        with self._lock:
            self._jobs[job.id] = job
        self._executor.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[Job]:
        """新しい順のジョブ一覧。dict は挿入順を保つのでそれを逆に辿る。"""
        with self._lock:
            return list(reversed(list(self._jobs.values())))[:limit]

    def cancel(self, job: Job) -> bool:
        """中止を要求する。既に終わっていれば False。

        実行中のスレッドは強制終了できない(Python の制約)ため、フラグを立てて
        パイプライン側が次の関門に到達したときに自ら降りてもらう方式にしている。
        """
        if job.finished:
            return False
        job.request_cancel()
        return True

    def _run(self, job: Job) -> None:
        # 同時実行数の上限を超えた分は executor のキューで待つ。待っている間に
        # 中止された場合は、そもそも処理を始めない。
        if job.cancel_requested:
            job.status = JobStatus.CANCELLED
            return

        job.status = JobStatus.RUNNING

        def on_progress(step: JobStep, ratio: float, detail: str) -> None:
            # 進捗報告は文字起こしのセグメントごと・LLM の区間ごと・画像 1 枚ごとに
            # 呼ばれる。ここを中止の関門にすると、重い工程の途中でも数秒以内に降りられる。
            job.raise_if_cancelled()
            job.add_event(step, ratio, detail)

        try:
            job.result = run_pipeline(
                job.video_path, job.output_dir, job.options, on_progress
            )
            job.status = JobStatus.DONE
        except JobCancelled:
            # 利用者の意思による停止。エラー表示にもスタック出力にもしない。
            job.status = JobStatus.CANCELLED
        except Exception as exc:  # noqa: BLE001 - UI に理由を返すため握る
            job.error = f"{type(exc).__name__}: {exc}"
            # 原因究明にはスタックが要る。ターミナルにしか出さないと
            # 「ブラウザには一行だけ」で詰まるため、UI からも辿れるようにする。
            job.traceback = traceback.format_exc()
            job.status = JobStatus.FAILED
            traceback.print_exc()

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)


manager = JobManager()
