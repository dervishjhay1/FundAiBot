"""
FundAiBot — Async task queue.
Wraps all AI work in tasks processed by a pool of workers.
Prevents one slow request from blocking all others.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Awaitable, Any

from config.settings import MAX_QUEUE_SIZE, QUEUE_TIMEOUT, MAX_CONCURRENT_TASKS
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class Task:
    user_id: int
    kind: str                               # "chat" | "image"
    payload: dict = field(default_factory=dict)
    callback: Callable[..., Awaitable[Any]] | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)


class QueueManager:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[Task] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._active_users: set[int] = set()
        self._semaphore: asyncio.Semaphore | None = None
        self._processed = 0
        self._errors = 0

    async def start(self) -> None:
        """Launch worker pool. Call once after the event loop starts."""
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
        for i in range(MAX_CONCURRENT_TASKS):
            asyncio.create_task(self._worker(i))
        log.info("Queue started with %d workers", MAX_CONCURRENT_TASKS)

    async def enqueue(self, task: Task) -> bool:
        if self._queue.full():
            log.warning("Queue full — dropped task from user %s", task.user_id)
            return False
        await self._queue.put(task)
        log.debug("Enqueued %s task for user %s (queue size=%d)", task.kind, task.user_id, self._queue.qsize())
        return True

    async def _worker(self, worker_id: int) -> None:
        log.debug("Worker %d ready", worker_id)
        while True:
            task = await self._queue.get()
            self._active_users.add(task.user_id)
            try:
                if task.callback:
                    async with self._semaphore:
                        await asyncio.wait_for(task.callback(task), timeout=QUEUE_TIMEOUT)
                self._processed += 1
            except asyncio.TimeoutError:
                log.error("Task timeout for user %s (%s)", task.user_id, task.kind)
                self._errors += 1
            except Exception as exc:
                log.exception("Worker %d error for user %s: %s", worker_id, task.user_id, exc)
                self._errors += 1
            finally:
                self._active_users.discard(task.user_id)
                self._queue.task_done()

    def stats(self) -> dict:
        return {
            "queue_size": self._queue.qsize(),
            "active_users": len(self._active_users),
            "processed": self._processed,
            "errors": self._errors,
        }

    def is_busy(self, user_id: int) -> bool:
        return user_id in self._active_users


queue_manager = QueueManager()
