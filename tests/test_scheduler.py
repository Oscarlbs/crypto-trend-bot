from datetime import datetime, timezone

import pytest

from bot.scheduler import Loop, seconds_until_next


def at(h, m, s=0):
    return datetime(2026, 11, 11, h, m, s, tzinfo=timezone.utc)


def test_seconds_until_next():
    assert seconds_until_next(at(10, 0)) == 7 * 60
    assert seconds_until_next(at(10, 7)) == 3600          # justo en el minuto: a la siguiente hora
    assert seconds_until_next(at(10, 30)) == 37 * 60
    assert seconds_until_next(at(23, 59, 30)) == 7 * 60 + 30


def test_errors_do_not_kill_loop_and_alert_once():
    calls, alerts, sleeps = [], [], []

    def job():
        calls.append(1)
        if len(calls) <= 3:
            raise ConnectionError("sin red")

    loop = Loop(job, notifier=alerts.append, sleep=sleeps.append, clock=lambda: at(10, 30))
    loop.forever(max_ticks=5)
    assert len(calls) == 5
    assert alerts == ["trendbot ERROR: ConnectionError: sin red"]   # mismo error: un solo aviso
    assert sleeps == [37 * 60] * 4


def test_new_error_after_recovery_alerts_again():
    errors = iter([ValueError("a"), None, ValueError("a")])
    alerts = []

    def job():
        e = next(errors)
        if e:
            raise e

    Loop(job, notifier=alerts.append, sleep=lambda s: None, clock=lambda: at(1, 0)).forever(max_ticks=3)
    assert len(alerts) == 2


def test_sigterm_while_sleeping_exits_now():
    loop = Loop(lambda: None, sleep=lambda s: None)
    with pytest.raises(SystemExit):
        loop.on_signal(15, None)


def test_sigterm_during_job_finishes_job_then_stops():
    loop = None
    done = []

    def job():
        loop.on_signal(15, None)   # llega a mitad de la ejecución: no debe cortar
        done.append(1)

    loop = Loop(job, sleep=lambda s: pytest.fail("no debería volver a dormir"))
    loop.forever()
    assert done == [1] and loop.stop
