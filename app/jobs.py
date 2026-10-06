"""오래 걸리는 작업(AI 요청, 음성 처리, 자막 정렬)의 진행 상태와 취소."""
from __future__ import annotations

import asyncio
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from .util import UserError, new_id


@dataclass
class Job:
    id: str
    kind: str
    label: str
    status: str = "running"  # running | done | error | cancelled
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str = ""
    hint: str = ""
    created: float = field(default_factory=time.time)
    finished: float | None = None
    cancel_flag: threading.Event = field(default_factory=threading.Event)
    task: asyncio.Task | None = None

    def report(self, progress: float | None = None, message: str | None = None) -> None:
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))
        if message is not None:
            self.message = message

    def cancelled(self) -> bool:
        return self.cancel_flag.is_set()

    def check(self) -> None:
        if self.cancel_flag.is_set():
            raise UserError("사용자가 작업을 취소했습니다.", status=499)

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label, "status": self.status,
                "progress": round(self.progress, 3), "message": self.message,
                "result": self.result if self.status == "done" else None,
                "error": self.error, "hint": self.hint,
                "elapsed": round((self.finished or time.time()) - self.created, 1)}


_jobs: dict[str, Job] = {}


def _cleanup() -> None:
    cutoff = time.time() - 3600
    for jid in [j.id for j in _jobs.values() if j.finished and j.finished < cutoff]:
        _jobs.pop(jid, None)


def _fail(job: Job, e: BaseException) -> None:
    job.finished = time.time()
    if isinstance(e, asyncio.CancelledError) or (isinstance(e, UserError) and e.status == 499):
        job.status = "cancelled"
        job.error = "작업을 취소했습니다."
        return
    job.status = "error"
    if isinstance(e, UserError):
        job.error, job.hint = e.message, e.hint
    else:
        traceback.print_exc()
        job.error = f"예상하지 못한 오류가 발생했습니다: {type(e).__name__}"
        job.hint = "같은 문제가 반복되면 프로그램 창(검은 콘솔)의 오류 내용을 확인하세요."


def start_thread_job(kind: str, label: str, fn: Callable[[Job], Any]) -> Job:
    """동기 함수 fn(job)을 별도 스레드에서 실행."""
    _cleanup()
    job = Job(id=new_id("job"), kind=kind, label=label)
    _jobs[job.id] = job

    def runner():
        try:
            res = fn(job)
            if job.cancelled():
                raise UserError("사용자가 작업을 취소했습니다.", status=499)
            job.result = res
            job.status = "done"
            job.progress = 1.0
            job.finished = time.time()
        except BaseException as e:  # noqa: BLE001
            _fail(job, e)

    threading.Thread(target=runner, daemon=True).start()
    return job


def start_async_job(kind: str, label: str, coro_fn: Callable[[Job], Any]) -> Job:
    """비동기 함수 coro_fn(job)을 이벤트 루프에서 실행. 취소 시 Task를 cancel한다."""
    _cleanup()
    job = Job(id=new_id("job"), kind=kind, label=label)
    _jobs[job.id] = job

    async def runner():
        try:
            res = await coro_fn(job)
            job.result = res
            job.status = "done"
            job.progress = 1.0
            job.finished = time.time()
        except BaseException as e:  # noqa: BLE001
            _fail(job, e)

    job.task = asyncio.get_running_loop().create_task(runner())
    return job


def get(job_id: str) -> Job:
    job = _jobs.get(job_id)
    if not job:
        raise UserError("작업을 찾을 수 없습니다(프로그램을 다시 시작했다면 작업이 사라졌을 수 있습니다).", status=404)
    return job


def cancel(job_id: str) -> Job:
    job = get(job_id)
    job.cancel_flag.set()
    if job.task and not job.task.done():
        job.task.cancel()
    return job
