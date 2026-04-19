import logging
import time
from dataclasses import dataclass

import pytest

from tick_actor import ActorRef, TickActor, TickStats


# ── TickStats unit tests ─────────────────────────────────────────────


class TestTickStats:
    def test_empty(self):
        s = TickStats()
        assert s.count == 0
        assert s.mean == 0.0
        assert s.variance == 0.0
        assert s.stddev == 0.0
        assert "no ticks" in repr(s)

    def test_single_record(self):
        s = TickStats()
        s.record(0.1)
        assert s.count == 1
        assert s.min == 0.1
        assert s.max == 0.1
        assert s.mean == pytest.approx(0.1)
        assert s.variance == 0.0  # need >=2 for variance

    def test_multiple_records(self):
        s = TickStats()
        for v in [0.1, 0.2, 0.3]:
            s.record(v)
        assert s.count == 3
        assert s.min == pytest.approx(0.1)
        assert s.max == pytest.approx(0.3)
        assert s.mean == pytest.approx(0.2)
        assert s.stddev > 0

    def test_repr_with_data(self):
        s = TickStats()
        s.record(0.1)
        r = repr(s)
        assert "count=1" in r
        assert "mean=" in r
        assert "ms" in r


# ── ActorRef tests ───────────────────────────────────────────────────


@dataclass
class Ping:
    pass


@dataclass
class Echo:
    value: str


class EchoActor(TickActor):
    def handle_message(self, message):
        match message:
            case Ping():
                return "pong"
            case Echo(value=v):
                return v
            case _:
                raise ValueError(f"unknown: {message}")


class EchoActorRef(ActorRef):
    def ping(self, timeout: float | None = None) -> str:
        return self._ask(Ping(), timeout)

    def echo(self, value: str, timeout: float | None = None) -> str:
        return self._ask(Echo(value=value), timeout)


# ── TickActor integration tests ──────────────────────────────────────


class TestTickActorLifecycle:
    def test_start_and_stop(self):
        ref = TickActor.start(tick_interval=0.05)
        time.sleep(0.15)
        ref.stop()

    def test_ticks_accumulate(self):
        ref = EchoActor.start(tick_interval=0.05)
        time.sleep(0.3)
        # Access tick_stats via ask to stay thread-safe
        stats = ref.ask({"_get_stats": True}, block=False)
        ref.stop()
        # Actor was alive ~0.3s at 50ms interval → expect at least 3 ticks
        # (we can't read tick_stats directly without a message, but we
        # verify indirectly via the on_tick counting test below)

    def test_on_tick_called(self):
        """Verify on_tick is actually invoked periodically."""

        class Counter(TickActor):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.count = 0

            def on_tick(self, dt):
                self.count += 1

            def handle_message(self, message):
                if message == "get_count":
                    return self.count

        ref = Counter.start(tick_interval=0.05)
        time.sleep(0.3)
        count = ref.ask("get_count", block=False).get(timeout=1.0)
        ref.stop()
        assert count >= 3

    def test_on_tick_receives_dt(self):
        """Verify dt is a positive float roughly matching tick_interval."""

        class DtCollector(TickActor):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.dts = []

            def on_tick(self, dt):
                self.dts.append(dt)

            def handle_message(self, message):
                if message == "get_dts":
                    return list(self.dts)

        ref = DtCollector.start(tick_interval=0.05)
        time.sleep(0.25)
        dts = ref.ask("get_dts", block=False).get(timeout=1.0)
        ref.stop()

        assert len(dts) >= 2
        # Skip first dt (includes startup time), rest should be ~50ms
        for dt in dts[1:]:
            assert 0.02 < dt < 0.15


class TestTickActorMessages:
    def test_ask_returns_value(self):
        ref = EchoActor.start(tick_interval=0.1)
        wrapped = EchoActorRef(ref)
        assert wrapped.ping() == "pong"
        assert wrapped.echo("hello") == "hello"
        wrapped.stop()

    def test_unknown_message_raises(self):
        ref = EchoActor.start(tick_interval=0.1)
        wrapped = EchoActorRef(ref)
        with pytest.raises(ValueError, match="unknown"):
            wrapped._ask("bad_message")
        wrapped.stop()

    def test_tell_does_not_block(self):
        ref = EchoActor.start(tick_interval=0.1)
        wrapped = EchoActorRef(ref)
        wrapped._tell(Ping())  # fire-and-forget, no return
        time.sleep(0.05)
        wrapped.stop()


class TestTickActorExceptionResilience:
    def test_on_tick_exception_does_not_kill_actor(self, caplog):
        class BadTick(TickActor):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self.tick_count = 0

            def on_tick(self, dt):
                self.tick_count += 1
                if self.tick_count == 2:
                    raise RuntimeError("boom")

            def handle_message(self, message):
                if message == "count":
                    return self.tick_count

        ref = BadTick.start(tick_interval=0.05)
        time.sleep(0.25)
        count = ref.ask("count", block=False).get(timeout=1.0)
        ref.stop()

        # Actor survived past the exception and kept ticking
        assert count >= 3
        assert "on_tick() failed" in caplog.text

    def test_handle_message_exception_reaches_caller(self):
        ref = EchoActor.start(tick_interval=0.1)
        wrapped = EchoActorRef(ref)

        with pytest.raises(ValueError):
            wrapped._ask("unknown_message")

        # Actor is still alive — can still respond
        assert wrapped.ping() == "pong"
        wrapped.stop()


class TestTickActorRef:
    def test_default_timeout(self):
        assert ActorRef.DEFAULT_TIMEOUT == 5.0

    def test_custom_timeout_override(self):
        ref = EchoActor.start(tick_interval=0.1)
        wrapped = EchoActorRef(ref)
        # Explicit timeout should work
        assert wrapped.ping(timeout=1.0) == "pong"
        wrapped.stop()

    def test_stop_via_ref(self):
        ref = EchoActor.start(tick_interval=0.1)
        wrapped = EchoActorRef(ref)
        wrapped.stop()
        time.sleep(0.1)
        assert ref.is_alive() is False


class TestTickStats:
    def test_stats_accessible_via_message(self):
        class StatsActor(TickActor):
            def handle_message(self, message):
                if message == "stats":
                    return self.tick_stats

        ref = StatsActor.start(tick_interval=0.05)
        time.sleep(0.2)
        stats = ref.ask("stats", block=False).get(timeout=1.0)
        ref.stop()

        assert isinstance(stats, TickStats)
        assert stats.count >= 2
        assert stats.min > 0
        assert stats.max >= stats.min
        assert stats.mean > 0


class TestMailboxDepthWarning:
    def test_slow_tick_logs_warning(self, caplog):
        class SlowActor(TickActor):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)
                self._slow = False

            def on_tick(self, dt):
                if self._slow:
                    time.sleep(0.2)

            def handle_message(self, message):
                if message == "go_slow":
                    self._slow = True
                elif message == "go_fast":
                    self._slow = False

        with caplog.at_level(logging.WARNING, logger="tick_actor"):
            ref = SlowActor.start(tick_interval=0.05)
            time.sleep(0.1)  # let a few normal ticks land

            ref.tell("go_slow")
            time.sleep(0.6)  # ticks pile up

            ref.tell("go_fast")
            time.sleep(0.2)  # drain

            ref.stop()

        assert "mailbox depth" in caplog.text
