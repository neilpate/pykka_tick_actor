# pykka-tick-actor

A base class that extends [Pykka](https://pykka.readthedocs.io/) actors with a periodic tick, plus a typed `ActorRef` wrapper for clean caller APIs.

## Why

Pykka actors are message-driven — they only wake up when a message arrives. Sometimes you need an actor to do something periodically (poll a sensor, update a simulation, emit metrics). `TickActor` adds that without losing any of the normal message-handling behaviour.

## What you get

- **`TickActor`** — a `ThreadingActor` subclass with a dedicated tick thread. Override `on_tick(dt)` for periodic work and `handle_message(msg)` for regular messages.
- **`ActorRef`** — a typed wrapper around Pykka's `ActorRef`. Subclass it per actor to expose a clean, typed API with `_ask()` / `_tell()`.
- **`TickStats`** — running min/max/mean/stddev of tick intervals, accessible from inside the actor.
- **Mailbox depth monitoring** — logs a warning when the actor's message queue backs up.
- **Exception resilience** — a failing `on_tick` is logged and swallowed; a failing `handle_message` is logged and re-raised to the caller via the ask future. The actor keeps running either way.
- **Windows timer fix** — automatically calls `timeBeginPeriod(1)` for accurate sub-16ms tick intervals on Windows.

## Quick example

```python
from dataclasses import dataclass
from tick_actor import ActorRef, TickActor

@dataclass
class GetCount:
    pass

class CounterActorRef(ActorRef):
    def get_count(self) -> int:
        return self._ask(GetCount())

class CounterActor(TickActor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.count = 0

    @classmethod
    def create(cls, **kwargs) -> CounterActorRef:
        return CounterActorRef(cls.start(**kwargs))

    def on_tick(self, dt):
        self.count += 1

    def handle_message(self, message):
        match message:
            case GetCount():
                return self.count

actor = CounterActor.create(tick_interval=0.1)
# ... later ...
print(actor.get_count())
actor.stop()
```

## Running the demo

```
uv run main.py
```

## Running the tests

```
uv run pytest test_tick_actor.py -v
```

## Requirements

- Python >= 3.14
- Pykka >= 4.0