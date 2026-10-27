"""Single-LLM gateway (ADR-0003, TRD 05 §5.2): strict priority lanes RT > INTERACTIVE > BATCH, one call at a time.

- A higher-lane arrival cuts the running lower-lane stream (closing the Ollama connection stops generation) and that
  call goes back to the *front* of its own lane. The caller never sees it except as extra `queue_ms`/`preempted`;
  no format or attempt budget is consumed.
- Deadlines are absolute (epoch ms). Waiting + expected run time past the deadline -> DeadlineExceeded at once; a
  running call that reaches its deadline is cut.
- `pause_lane("BATCH")` holds BATCH out of the GPU while STT runs (gpu_policy=stt_preempts_batch, TRD 05 §3).
- The backend raising LLMUnavailable marks the LLM down; requests fail fast while the last failure is < 15 s old
  (OQ-23) so the worker stops claiming LLM jobs instead of burning attempts.
"""
import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

LANES = ("RT", "INTERACTIVE", "BATCH")
_RANK = {lane: i for i, lane in enumerate(LANES)}
DOWN_FOR_SEC = 15.0

ChatFn = Callable[[list[dict], dict, dict], Awaitable[tuple[str, dict]]]


class LLMUnavailable(Exception):
    """Connection refused, 5xx, model not loaded."""


class DeadlineExceeded(Exception):
    pass


@dataclass
class _Waiter:
    lane: str
    granted: asyncio.Future
    seq: int


@dataclass
class _Running:
    lane: str
    task: asyncio.Task
    preempted: bool = False


@dataclass
class CallStats:
    queue_ms: int = 0
    preempted: int = 0
    run_ms: int = 0


@dataclass
class Gateway:
    chat: ChatFn
    clock: Callable[[], float] = time.time
    down_for_sec: float = DOWN_FOR_SEC
    _waiting: dict[str, deque] = field(default_factory=lambda: {lane: deque() for lane in LANES})
    _running: _Running | None = None
    _paused: dict[str, int] = field(default_factory=lambda: {lane: 0 for lane in LANES})
    _seq: int = 0
    _down_at: float | None = None
    _est_ms: dict[str, float] = field(default_factory=dict)  # EWMA run time per task key

    # ── public ──
    def is_down(self) -> bool:
        return self._down_at is not None and self.clock() - self._down_at < self.down_for_sec

    def mark_up(self) -> None:
        self._down_at = None

    def mark_down(self) -> None:
        self._down_at = self.clock()

    def snapshot(self) -> dict:
        return {"running": self._running.lane if self._running else None,
                "waiting": {lane: len(q) for lane, q in self._waiting.items()},
                "paused": [lane for lane, n in self._paused.items() if n]}

    def estimate_ms(self, key: str, default_ms: float) -> float:
        return self._est_ms.get(key, default_ms)

    async def call(self, lane: str, messages: list[dict], schema: dict, options: dict, *, deadline_ms: int,
                   stats: CallStats, est_key: str = "", est_default_ms: float = 1000.0) -> tuple[str, dict]:
        """One LLM chat completion under the lane/deadline rules. Raises LLMUnavailable or DeadlineExceeded."""
        if lane not in _RANK:
            raise ValueError(lane)
        front = False
        while True:
            if self.is_down():
                raise LLMUnavailable("recent failure")
            est = self.estimate_ms(est_key, est_default_ms)
            t_wait = self.clock()
            if t_wait * 1000 + est > deadline_ms:
                raise DeadlineExceeded("no time left to run")
            await self._acquire(lane, front=front, give_up_at_ms=deadline_ms - est)
            stats.queue_ms += int((self.clock() - t_wait) * 1000)
            t_run = self.clock()
            task = asyncio.create_task(self.chat(messages, schema, options))
            run = self._running = _Running(lane, task)
            self._dispatch()  # a higher lane may already be waiting (it arrived while we were being granted)
            try:
                remaining = deadline_ms / 1000 - self.clock()
                done, _ = await asyncio.wait({task}, timeout=max(0.0, remaining))
                if not done:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise DeadlineExceeded("cut at deadline")
                if task.cancelled() and run.preempted:
                    stats.preempted += 1
                    front = True
                    continue
                try:
                    content, usage = task.result()
                except LLMUnavailable:
                    self.mark_down()
                    raise
                self.mark_up()
                ran = (self.clock() - t_run) * 1000
                stats.run_ms += int(ran)
                if est_key:
                    old = self._est_ms.get(est_key)
                    self._est_ms[est_key] = ran if old is None else 0.7 * old + 0.3 * ran
                return content, usage
            finally:
                if not task.done():
                    task.cancel()  # our caller was cancelled (client went away): stop generating
                if self._running is run:
                    self._running = None
                self._dispatch()

    @asynccontextmanager
    async def pause_lane(self, lane: str):
        """Keep `lane` off the GPU for the duration (and cut it if it is running now)."""
        self._paused[lane] += 1
        if self._running and self._running.lane == lane:
            self._preempt()
        try:
            yield
        finally:
            self._paused[lane] -= 1
            self._dispatch()

    # ── scheduling ──
    async def _acquire(self, lane: str, *, front: bool, give_up_at_ms: float) -> None:
        loop = asyncio.get_running_loop()
        self._seq += 1
        w = _Waiter(lane, loop.create_future(), self._seq)
        (self._waiting[lane].appendleft if front else self._waiting[lane].append)(w)
        self._dispatch()
        try:
            timeout = max(0.0, give_up_at_ms / 1000 - self.clock())
            await asyncio.wait_for(asyncio.shield(w.granted), timeout=timeout)
        except TimeoutError:
            if w.granted.done() and not w.granted.cancelled():
                return  # granted at the same instant: take it (the caller re-checks the deadline while running)
            self._remove(w)
            raise DeadlineExceeded("deadline passed while waiting") from None
        except asyncio.CancelledError:
            if w.granted.done() and not w.granted.cancelled():
                self._running = None  # we were granted but the caller is gone: free the slot
            self._remove(w)
            self._dispatch()
            raise

    def _remove(self, w: _Waiter) -> None:
        try:
            self._waiting[w.lane].remove(w)
        except ValueError:
            pass
        if not w.granted.done():
            w.granted.cancel()

    def _next_lane(self) -> str | None:
        for lane in LANES:
            if self._waiting[lane] and not self._paused[lane]:
                return lane
        return None

    def _dispatch(self) -> None:
        nxt = self._next_lane()
        if nxt is None:
            return
        if self._running is None:
            w = self._waiting[nxt].popleft()
            # Reserve the slot until the waiter creates its task; a placeholder keeps others out meanwhile.
            self._running = _Running(nxt, _DONE_TASK())
            w.granted.set_result(True)
            return
        if _RANK[nxt] < _RANK[self._running.lane] and not self._running.preempted:
            self._preempt()

    def _preempt(self) -> None:
        run = self._running
        if run is None or run.preempted:
            return
        run.preempted = True
        if not run.task.done():
            run.task.cancel()


def _DONE_TASK() -> asyncio.Task:
    """A finished placeholder task occupying the slot between grant and the real call."""
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    fut.set_result(None)
    return fut  # type: ignore[return-value]
