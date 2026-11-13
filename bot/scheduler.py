"""Bucle para Docker: lanza el job una vez por hora (minuto 7) y sobrevive a errores puntuales.

`docker stop` manda SIGTERM: si llega mientras duerme, sale al momento; si llega a mitad
de una ejecución (p. ej. mandando órdenes), termina esa ejecución y luego sale.

Uso:  python -m bot.scheduler
"""
from __future__ import annotations

import signal
import time
import traceback
from datetime import datetime, timedelta, timezone

from .config import from_env
from .notify import notify
from .run import run

MINUTE = 7


def seconds_until_next(now: datetime, minute: int = MINUTE) -> float:
    target = now.replace(minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(hours=1)
    return (target - now).total_seconds()


class Loop:
    def __init__(self, job, notifier=notify, sleep=time.sleep, clock=None):
        self.job = job
        self.notifier = notifier
        self.sleep = sleep
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.busy = False
        self.stop = False
        self.last_error: str | None = None

    def on_signal(self, signum, frame) -> None:
        self.stop = True
        if not self.busy:
            raise SystemExit(0)

    def tick(self) -> None:
        self.busy = True
        try:
            self.job()
            self.last_error = None
        except Exception as exc:
            traceback.print_exc()
            msg = f"{type(exc).__name__}: {exc}"
            if msg != self.last_error:  # no repetir el mismo aviso cada hora
                self.notifier(f"trendbot ERROR: {msg}")
                self.last_error = msg
        finally:
            self.busy = False

    def forever(self, max_ticks: int | None = None) -> None:
        ticks = 0
        while not self.stop:
            self.tick()
            ticks += 1
            if self.stop or (max_ticks is not None and ticks >= max_ticks):
                break
            self.sleep(seconds_until_next(self.clock()))


def main() -> None:
    cfg = from_env()
    print(f"trendbot scheduler arrancado | modo {cfg.mode} | presupuesto {cfg.budget:.2f}", flush=True)
    loop = Loop(lambda: run(cfg))
    signal.signal(signal.SIGTERM, loop.on_signal)
    signal.signal(signal.SIGINT, loop.on_signal)
    loop.forever()
    print("trendbot scheduler parado", flush=True)


if __name__ == "__main__":
    main()
