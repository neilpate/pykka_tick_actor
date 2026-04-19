import logging
import time
from dataclasses import dataclass

from tick_actor import ActorRef, TickActor, TickStats


@dataclass
class GetCount:
    pass


@dataclass
class SetCount:
    value: int


@dataclass
class GetStats:
    pass


@dataclass
class CauseError:
    pass


@dataclass
class SlowTick:
    duration: float


class CounterActorRef(ActorRef):
    def get_count(self, timeout: float | None = None) -> int:
        return self._ask(GetCount(), timeout)

    def set_count(self, value: int) -> None:
        self._ask(SetCount(value=value))

    def get_stats(self, timeout: float | None = None) -> TickStats:
        return self._ask(GetStats(), timeout)

    def cause_error(self) -> None:
        self._ask(CauseError())

    def slow_tick(self, duration: float) -> None:
        self._ask(SlowTick(duration=duration))


class CounterActor(TickActor):
    def __init__(self, tick_interval=0.1):
        super().__init__(tick_interval=tick_interval)
        self.count = 0

    @classmethod
    def create(cls, **kwargs) -> CounterActorRef:
        return CounterActorRef(cls.start(**kwargs))

    def on_start(self):
        print("CounterActor started")
        super().on_start()

    def on_stop(self):
        super().on_stop()
        print("CounterActor stopped")

    def on_tick(self, dt):
        self.count += 1
        duration = getattr(self, '_slow_tick_duration', 0)
        if duration:
            time.sleep(duration)
        print(f"tick #{self.count}  dt={dt * 1000:.2f}ms")

    def handle_message(self, message):
        match message:
            case GetCount():
                return self.count
            case SetCount(value=v):
                self.count = v
            case GetStats():
                return self.tick_stats
            case CauseError():
                raise RuntimeError("intentional error")
            case SlowTick(duration=d):
                self._slow_tick_duration = d
            case _:
                raise ValueError(f"unknown message: {message}")


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    actor = CounterActor.create(tick_interval=0.1)

    time.sleep(0.5)

    count = actor.get_count()
    print(f"\nCount after ~0.5s: {count}")

    actor.set_count(100)
    time.sleep(0.3)

    count = actor.get_count(timeout=0.5)
    print(f"Count after set + ~0.3s (with 500ms timeout): {count}")

    print("\nSending error message (actor should survive)...")
    try:
        actor.cause_error()
    except RuntimeError as e:
        print(f"Caller caught the error: {e}")
    time.sleep(0.3)

    count = actor.get_count()
    print(f"Count after error + ~0.3s: {count} (still ticking!)")

    stats = actor.get_stats()
    print(f"Tick stats: {stats}")

    print("\nMaking on_tick() slow (250ms) to cause mailbox buildup...")
    actor.slow_tick(0.25)
    time.sleep(1)

    actor.slow_tick(0)
    time.sleep(0.3)

    print(f"\nTick stats after slow period: {actor.get_stats()}")

    actor.stop()


if __name__ == "__main__":
    main()
