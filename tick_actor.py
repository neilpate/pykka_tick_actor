import ctypes
import logging
import math
import os
import queue
import threading
import time
from typing import Any, cast

import pykka

_IS_WINDOWS = os.name == "nt"

_TICK = object()  # unforgeable sentinel for tick messages

logger = logging.getLogger(__name__)


class TickStats:
    """Running statistics for tick delta times."""

    __slots__ = ("count", "min", "max", "_sum", "_sum_sq")

    def __init__(self):
        self.count = 0
        self.min = math.inf
        self.max = -math.inf
        self._sum = 0.0
        self._sum_sq = 0.0

    def record(self, dt):
        self.count += 1
        if dt < self.min:
            self.min = dt
        if dt > self.max:
            self.max = dt
        self._sum += dt
        self._sum_sq += dt * dt

    @property
    def mean(self):
        return self._sum / self.count if self.count else 0.0

    @property
    def variance(self):
        if self.count < 2:
            return 0.0
        return (self._sum_sq - self._sum * self._sum / self.count) / (
            self.count - 1
        )

    @property
    def stddev(self):
        return math.sqrt(self.variance)

    def __repr__(self):
        if self.count == 0:
            return "TickStats(no ticks)"
        return (
            f"TickStats(count={self.count}, "
            f"mean={self.mean * 1000:.2f}ms, "
            f"min={self.min * 1000:.2f}ms, "
            f"max={self.max * 1000:.2f}ms, "
            f"stddev={self.stddev * 1000:.2f}ms)"
        )


class ActorRef:
    """Typed base class for actor references.

    Subclass this for each actor, adding typed methods that call
    ``self._ask()`` or ``self._tell()``.
    """

    DEFAULT_TIMEOUT: float = 5.0

    def __init__(self, ref: pykka.ActorRef):
        self._ref = ref

    def _ask(self, message: object, timeout: float | None = None) -> Any:
        if timeout is None:
            timeout = self.DEFAULT_TIMEOUT
        return self._ref.ask(message, block=False).get(timeout=timeout)

    def _tell(self, message: object) -> None:
        self._ref.tell(message)

    def stop(self) -> None:
        self._ref.stop()


class TickActor(pykka.ThreadingActor):
    """A Pykka actor with a periodic tick.

    Subclasses should override ``on_tick(dt)`` for periodic work and
    ``handle_message()`` for regular messages.  Do not override
    ``on_receive()`` directly.

    ``on_tick(dt)`` receives *dt* — the wall-clock seconds since the
    previous tick (or since startup for the first tick).

    If you override ``on_start()`` or ``on_stop()``, call ``super()``
    to keep the tick machinery running.
    """

    def __init__(self, tick_interval=0.1):
        super().__init__()
        self.tick_interval = tick_interval
        self._stop_event = threading.Event()
        self._tick_thread = None
        self._last_tick_time: float = 0.0
        self.tick_stats = TickStats()

    # -- lifecycle -----------------------------------------------------

    def on_start(self):
        self._last_tick_time = time.monotonic()
        self._tick_thread = threading.Thread(
            target=self._tick_loop, daemon=True
        )
        self._tick_thread.start()

    def on_stop(self):
        self._stop_event.set()
        if self._tick_thread is not None:
            self._tick_thread.join(timeout=2.0)

    # -- message dispatch (not meant to be overridden) -----------------

    def on_receive(self, message):
        if message is _TICK:
            now = time.monotonic()
            dt = now - self._last_tick_time
            self._last_tick_time = now
            self.tick_stats.record(dt)
            try:
                self.on_tick(dt)
            except Exception:
                logger.exception("on_tick() failed")
            return None
        try:
            return self.handle_message(message)
        except Exception:
            logger.exception("handle_message() failed")
            raise

    # -- subclass hooks ------------------------------------------------

    def on_tick(self, dt):
        """Called periodically at ``tick_interval``.

        *dt* is the elapsed seconds since the last tick.  Override in
        subclass.
        """

    def handle_message(self, message) -> Any:
        """Called for every non-tick message.  Override in subclass."""

    # -- internal tick loop (runs in a single dedicated thread) --------

    def _tick_loop(self):
        if _IS_WINDOWS:
            ctypes.windll.winmm.timeBeginPeriod(1)
        try:
            interval = self.tick_interval
            inbox = cast(queue.Queue, self.actor_inbox)
            while not self._stop_event.wait(interval):
                depth = inbox.qsize()
                if depth > 1:
                    logger.warning("actor mailbox depth: %d", depth)
                try:
                    self.actor_ref.tell(_TICK)
                except pykka.ActorDeadError:
                    break
        finally:
            if _IS_WINDOWS:
                ctypes.windll.winmm.timeEndPeriod(1)
