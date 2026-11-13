#!/usr/bin/env python3
"""BananaTrading: panel del bot en tu Mac.

Arranca una web solo en este ordenador (127.0.0.1) y trae los datos del servidor por SSH, con tu
clave de siempre. En el servidor no se abre ningún puerto: el panel solo le pide al bot su foto de
datos (python -m bot.dashboard), que lee y no toca nada.

Uso:  python3 panel/server.py --open
      python3 panel/server.py --file foto.json     (sin servidor: enseña una foto guardada)
Solo usa la biblioteca estándar y funciona con el python3 que trae macOS.
"""
import argparse
import base64
import json
import os
import shlex
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
APP = "bananatrading"


def control_dir():
    """Carpeta privada y de ruta corta para reutilizar la conexión SSH.

    macOS no admite rutas de más de ~100 caracteres para esto, y dentro de ~/.ssh se pasa con
    nombres de usuario largos. Si la carpeta no es nuestra o la puede leer otro, no se usa.
    """
    path = "/tmp/banana-%d" % os.getuid()
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    except OSError:
        return None
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        return None
    return path


class Source:
    """De dónde sale la foto: del servidor por SSH o de un fichero local."""

    def __init__(self, host, remote_dir, file=None):
        self.host, self.remote_dir, self.file = host, remote_dir, file
        self.lock = threading.Lock()  # una sola petición al servidor a la vez

    def _ssh(self, remote_command, timeout):
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        shared = control_dir()
        if shared:
            # Reutiliza la conexión unos minutos: las siguientes peticiones van al instante
            cmd += ["-o", "ControlMaster=auto", "-o", "ControlPersist=300", "-o", "ControlPath=%s/%%C" % shared]
        cmd += [self.host, remote_command]
        try:
            done = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError("El servidor ha tardado demasiado en responder")
        if done.returncode != 0:
            detail = done.stderr.decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError(detail[-1] if detail else "No se pudo conectar con el servidor (código %d)" % done.returncode)
        return done.stdout.decode("utf-8")

    def _json(self, text):
        # docker compose puede escribir avisos antes: la foto es la última línea
        lines = [line for line in text.splitlines() if line.strip().startswith("{")]
        if not lines:
            raise RuntimeError("El servidor no ha devuelto datos")
        json.loads(lines[-1])  # si no es JSON válido, que falle aquí
        return lines[-1]

    def saved(self):
        """La última foto guardada: rápida, puede tener unos minutos."""
        if self.file:
            with open(self.file, encoding="utf-8") as fh:
                return self._json(fh.read())
        with self.lock:
            return self._json(self._ssh("cat %s/reports/dashboard.json" % shlex.quote(self.remote_dir), 25))

    def fresh(self):
        """Una foto nueva, con los precios de ahora mismo."""
        if self.file:
            return self.saved()
        with self.lock:
            return self._json(self._ssh(
                "cd %s && docker compose exec -T trendbot python -m bot.dashboard" % shlex.quote(self.remote_dir), 90))


def page(mascot_file):
    """La página del panel, con la foto del plátano dentro si existe panel/mascota-local.png (no va a git)."""
    with open(os.path.join(HERE, "index.html"), "rb") as fh:
        html = fh.read()
    if mascot_file and os.path.isfile(mascot_file):
        with open(mascot_file, "rb") as fh:
            data = base64.b64encode(fh.read())
        html = html.replace(b"\nconst MASCOTA = '';\n", b"\nconst MASCOTA = 'data:image/png;base64," + data + b"';\n", 1)
    return html


class Handler(BaseHTTPRequestHandler):
    source = None
    mascot_file = os.path.join(HERE, "mascota-local.png")
    allowed_hosts = ()
    last_seen = time.monotonic()

    def log_message(self, fmt, *args):
        pass

    def setup(self):
        super().setup()
        Handler.last_seen = time.monotonic()

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _guard(self):
        # Solo se atiende a esta máquina y por su nombre: ninguna otra web puede usar el panel
        if self.headers.get("Host", "") not in self.allowed_hosts:
            self._send(403, json.dumps({"error": "Solo desde este ordenador"}))
            return False
        return True

    def _data(self, getter):
        try:
            self._send(200, getter())
        except Exception as exc:
            self._send(502, json.dumps({"error": str(exc)}, ensure_ascii=False))

    def do_GET(self):
        if not self._guard():
            return
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, page(self.mascot_file), "text/html; charset=utf-8")
        elif path == "/api/ping":
            self._send(200, json.dumps({"app": APP}))
        elif path == "/api/foto":
            self._data(self.source.saved)
        else:
            self._send(404, json.dumps({"error": "No existe"}))

    def do_POST(self):
        if not self._guard():
            return
        # Las páginas de otros sitios no pueden mandar esta cabecera sin permiso: evita que una web ajena dispare la actualización
        if self.path != "/api/actualizar" or self.headers.get("X-Banana") != "1":
            self._send(404, json.dumps({"error": "No existe"}))
            return
        self._data(self.source.fresh)


def already_running(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % port, timeout=2) as res:
            return json.loads(res.read()).get("app") == APP
    except Exception:
        return False


def open_window(url):
    chrome = "/Applications/Google Chrome.app"
    if sys.platform == "darwin" and os.path.exists(chrome):
        # Ventana propia, sin pestañas ni barra de direcciones: se ve como una app
        subprocess.Popen(["open", "-na", chrome, "--args", "--app=" + url])
    else:
        webbrowser.open(url)


def main():
    ap = argparse.ArgumentParser(description="BananaTrading: panel del bot")
    ap.add_argument("--host", default="usuario@tu-servidor", help="servidor por SSH")
    ap.add_argument("--dir", default="/srv/apps/trendbot", help="carpeta del bot en el servidor")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--file", help="enseña una foto guardada en vez de conectar con el servidor")
    ap.add_argument("--open", action="store_true", help="abre la ventana del panel")
    ap.add_argument("--idle-exit", type=int, default=0, metavar="SEG",
                    help="se cierra solo tras tantos segundos sin que ninguna ventana lo use (0 = nunca)")
    args = ap.parse_args()

    url = "http://127.0.0.1:%d/" % args.port
    if already_running(args.port):
        if args.open:
            open_window(url)
        print("BananaTrading ya estaba en marcha: " + url)
        return

    Handler.source = Source(args.host, args.dir, args.file)
    Handler.allowed_hosts = ("127.0.0.1:%d" % args.port, "localhost:%d" % args.port)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("BananaTrading en " + url + "  (Ctrl+C para cerrar)")
    if args.open:
        open_window(url)
    if args.idle_exit > 0:
        def watchdog():
            # La ventana abierta da señales de vida cada pocos minutos; al cerrarla, el panel se apaga solo
            while time.monotonic() - Handler.last_seen < args.idle_exit:
                time.sleep(15)
            server.shutdown()
        threading.Thread(target=watchdog, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
