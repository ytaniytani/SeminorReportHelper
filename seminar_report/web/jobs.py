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

    def add_event(self, step: JobStep, ratio: float, detail: str) -> None:
        with self._lock:
            self.events.append(Progress(step=step, ratio=ratio, detail=detail))

    def events_since(self, index: int) -> list[Progress]:
        with self._lock:
            return self.events[index:]

    @property
    def finished(self) -> bool:
        return self.status in (JobStatus.DONE, JobStatus.FAILED)


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

    def _run(self, job: Job) -> None:
        job.status = JobStatus.RUNNING
        try:
            job.result = run_pipeline(
                job.video_path, job.output_dir, job.options, job.add_event
            )
            job.status = JobStatus.DONE
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
