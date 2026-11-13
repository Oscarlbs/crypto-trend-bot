"""Alertas por Telegram (opcional). Sin token configurado, solo imprime.

Uso:  python -m bot.notify              manda un mensaje de prueba
      python -m bot.notify --chat-id    dice tu chat id (escríbele antes /start a tu bot)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


RETRY_WAITS = (5, 20)  # segundos antes de cada reintento si se corta la conexión


class Offline(RuntimeError):
    """No se llegó a Telegram (sin red, corte, tiempo agotado): vale la pena reintentar."""


def _call(token: str, method: str, payload: dict | None = None) -> dict:
    """Llama a la API de Telegram. Los errores nunca llevan el token dentro."""
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=15).read() or b"{}")
    except urllib.error.HTTPError as exc:
        # Telegram explica el motivo en el cuerpo: "Unauthorized", "chat not found"...
        try:
            reason = json.loads(exc.read()).get("description") or exc.reason
        except Exception:
            reason = exc.reason
        raise RuntimeError(f"Telegram respondió {exc.code}: {reason}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise Offline(f"sin conexión con Telegram: {getattr(exc, 'reason', exc)}") from None


def notify(text: str) -> bool:
    """Devuelve False solo si había Telegram configurado y el envío falló (p. ej. sin red)."""
    print(text, flush=True)
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return True
    # Un corte de red suelto no debe comerse el aviso: se reintenta un par de veces. Si Telegram
    # contesta con un error (token o chat mal), reintentar no arregla nada y se deja.
    for wait in (*RETRY_WAITS, None):
        try:
            _call(token, "sendMessage", {"chat_id": chat, "text": text})
            return True
        except Offline as exc:
            if wait is None:
                print(f"[notify] Telegram falló: {exc}", flush=True)
                return False
            print(f"[notify] Telegram sin conexión, reintento en {wait} s", flush=True)
            time.sleep(wait)
        except Exception as exc:  # una alerta fallida no debe tumbar el job
            print(f"[notify] Telegram falló: {exc}", flush=True)
            return False
    return False


def chat_ids(token: str) -> dict[str, str]:
    """Chats que le han escrito al bot hace poco: {chat id: nombre}."""
    found: dict[str, str] = {}
    for update in _call(token, "getUpdates").get("result") or []:
        chat = (update.get("message") or update.get("channel_post") or {}).get("chat") or {}
        if "id" in chat:
            found[str(chat["id"])] = chat.get("title") or chat.get("first_name") or chat.get("username") or ""
    return found


def main() -> None:
    from .config import load_env

    ap = argparse.ArgumentParser(description="Prueba las alertas de Telegram")
    ap.add_argument("--chat-id", action="store_true", help="muestra el chat id de quien le ha escrito al bot")
    args = ap.parse_args()
    load_env()
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token:
        sys.exit("Falta TELEGRAM_TOKEN en .env (te lo da @BotFather al crear el bot)")

    if args.chat_id:
        try:
            found = chat_ids(token)
        except RuntimeError as exc:
            sys.exit(f"No se pudo consultar: {exc}")
        if not found:
            sys.exit("Nadie le ha escrito al bot todavía: abre tu bot en Telegram, mándale /start y repite")
        for cid, name in found.items():
            print(f"{name or 'chat'}:  TELEGRAM_CHAT_ID={cid}")
        return

    if not chat:
        sys.exit("Falta TELEGRAM_CHAT_ID en .env (sácalo con: python -m bot.notify --chat-id)")
    mode = os.environ.get("BOT_MODE", "paper")
    if not notify(f"trendbot [{mode}] prueba de alertas: si lees esto, Telegram funciona."):
        sys.exit(1)
    print("Enviado. Mira Telegram.")


if __name__ == "__main__":
    main()
