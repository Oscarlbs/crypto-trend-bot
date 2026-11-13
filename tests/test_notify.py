import io
import json
import urllib.error

import pytest

from bot import notify as nt
from bot.config import load_env

TOKEN = "123456:SECRETO"


class FakeTelegram:
    """Sustituye a urlopen: guarda lo que se le manda y responde lo que se le diga.
    Con `fails`, las primeras llamadas fallan con `error` y luego responde bien."""

    def __init__(self, reply=None, error=None, fails=None):
        self.reply, self.error, self.calls = reply or {"ok": True}, error, []
        self.fails = fails

    def __call__(self, req, timeout=None):
        self.calls.append((req.full_url, json.loads(req.data)))
        if self.error and (self.fails is None or len(self.calls) <= self.fails):
            raise self.error
        return io.BytesIO(json.dumps(self.reply).encode())


@pytest.fixture
def telegram(monkeypatch):
    def use(**kw):
        fake = FakeTelegram(**kw)
        monkeypatch.setattr(nt.urllib.request, "urlopen", fake)
        return fake

    monkeypatch.setenv("TELEGRAM_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(nt.time, "sleep", lambda s: None)  # los reintentos no esperan en las pruebas
    return use


def test_without_telegram_only_prints(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(nt.urllib.request, "urlopen", lambda *a, **k: pytest.fail("no debe salir a la red"))
    assert nt.notify("hola") is True
    assert "hola" in capsys.readouterr().out


def test_sends_message_to_the_chat(telegram):
    fake = telegram()
    assert nt.notify("equity 100") is True
    url, body = fake.calls[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert body == {"chat_id": "42", "text": "equity 100"}


def test_failure_never_raises_nor_leaks_the_token(telegram, capsys):
    http = urllib.error.HTTPError(f"https://api.telegram.org/bot{TOKEN}/sendMessage", 400, "Bad Request", {},
                                  io.BytesIO(b'{"ok":false,"description":"Bad Request: chat not found"}'))
    for error in (http, urllib.error.URLError("timed out"), ValueError("raro")):
        telegram(error=error)
        assert nt.notify("x") is False
    out = capsys.readouterr().out
    assert "chat not found" in out and "sin conexión" in out
    assert TOKEN not in out and "SECRETO" not in out


def test_chat_ids_from_updates(telegram):
    telegram(reply={"ok": True, "result": [
        {"message": {"chat": {"id": 42, "first_name": "Oscar"}}},
        {"message": {"chat": {"id": 42, "first_name": "Oscar"}}},
        {"channel_post": {"chat": {"id": -100123, "title": "Avisos"}}},
        {"edited_message": {}},
    ]})
    assert nt.chat_ids(TOKEN) == {"42": "Oscar", "-100123": "Avisos"}


def test_cli_test_message_and_missing_config(telegram, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["notify"])
    fake = telegram()
    nt.main()
    assert "prueba de alertas" in fake.calls[0][1]["text"]

    telegram(error=urllib.error.URLError("timed out"))
    with pytest.raises(SystemExit) as exc:
        nt.main()
    assert exc.value.code == 1

    monkeypatch.delenv("TELEGRAM_CHAT_ID")
    with pytest.raises(SystemExit, match="TELEGRAM_CHAT_ID"):
        nt.main()
    monkeypatch.delenv("TELEGRAM_TOKEN")
    with pytest.raises(SystemExit, match="TELEGRAM_TOKEN"):
        nt.main()


def test_env_file_ignores_inline_comments(tmp_path, monkeypatch):
    for key in ("T_MODE", "T_BUDGET", "T_QUOTED", "T_HASH", "T_EMPTY"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "T_MODE=paper        # paper = simulado | live = dinero real\n"
        "T_BUDGET=100   # EUR\n"
        'T_QUOTED="con # dentro"  # comentario\n'
        "T_HASH=abc#def\n"
        "T_EMPTY=\n"
        "# T_NO=1\n"
    )
    load_env(env)
    import os

    assert os.environ["T_MODE"] == "paper"
    assert float(os.environ["T_BUDGET"]) == 100
    assert os.environ["T_QUOTED"] == "con # dentro"
    assert os.environ["T_HASH"] == "abc#def"
    assert os.environ["T_EMPTY"] == ""
    assert "T_NO" not in os.environ
    for key in ("T_MODE", "T_BUDGET", "T_QUOTED", "T_HASH", "T_EMPTY"):
        os.environ.pop(key, None)


def test_a_dropped_connection_is_retried(telegram):
    fake = telegram(error=ConnectionResetError(104, "Connection reset by peer"), fails=2)
    assert nt.notify("equity 10000") is True and len(fake.calls) == 3   # dos cortes y a la tercera va


def test_telegram_errors_are_not_retried(telegram):
    http = urllib.error.HTTPError("https://api.telegram.org/x", 401, "Unauthorized", {}, io.BytesIO(b"{}"))
    fake = telegram(error=http)
    assert nt.notify("x") is False and len(fake.calls) == 1               # token malo: reintentar no sirve
