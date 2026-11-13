"""El panel del Mac (panel/server.py). En la imagen del bot no va esa carpeta: ahí estas pruebas se saltan."""
import importlib.util
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parent.parent / "panel" / "server.py"
pytestmark = pytest.mark.skipif(not SERVER.exists(), reason="sin carpeta panel/")


@pytest.fixture
def panel(tmp_path):
    spec = importlib.util.spec_from_file_location("panel_server", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    photo = tmp_path / "foto.json"
    photo.write_text('aviso de docker\n{"version": 1, "cuenta": {"equity": 100}}\n')
    mod.Handler.source = mod.Source("nadie@localhost", "/srv/apps/trendbot", str(photo))
    server = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    port = server.server_address[1]
    mod.Handler.allowed_hosts = (f"127.0.0.1:{port}",)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield mod, f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def ask(url, method="GET", headers=None):
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return res.status, res.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_serves_the_page_and_the_photo(panel):
    mod, base = panel
    code, page = ask(base + "/")
    assert code == 200 and "BananaTrading" in page
    assert ask(base + "/api/ping") == (200, '{"app": "bananatrading"}')
    code, body = ask(base + "/api/foto")
    assert code == 200 and json.loads(body)["cuenta"]["equity"] == 100  # se queda con la línea de datos
    code, body = ask(base + "/api/actualizar", "POST", {"X-Banana": "1"})
    assert code == 200 and json.loads(body)["version"] == 1


def test_only_this_computer_and_only_the_panel_itself(panel):
    mod, base = panel
    assert ask(base + "/", headers={"Host": "malo.example"})[0] == 403        # otra web apuntando aquí
    assert ask(base + "/api/actualizar", "POST")[0] == 404                    # sin la cabecera del panel
    assert ask(base + "/api/actualizar")[0] == 404                            # actualizar nunca por GET
    assert ask(base + "/../server.py")[0] == 404 and ask(base + "/server.py")[0] == 404


def test_errors_are_explained_not_crashes(panel, tmp_path):
    mod, base = panel
    mod.Handler.source = mod.Source("nadie@localhost", "/srv/apps/trendbot", str(tmp_path / "no-existe.json"))
    code, body = ask(base + "/api/foto")
    assert code == 502 and "error" in json.loads(body)
    with pytest.raises(RuntimeError, match="no ha devuelto datos"):
        mod.Source("x", "y")._json("solo avisos\n")


def test_the_remote_commands_are_fixed_and_quoted(panel, monkeypatch):
    mod, _ = panel
    seen = []

    def fake_run(cmd, capture_output, timeout):
        seen.append(cmd)
        return type("Done", (), {"returncode": 0, "stdout": b'{"ok": true}\n', "stderr": b""})()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    src = mod.Source("oscar@servidor", "/srv/apps/mi bot; rm -rf x")
    src.saved()
    src.fresh()
    assert all(c[0] == "ssh" and "BatchMode=yes" in c and c[-2] == "oscar@servidor" for c in seen)
    # La ruta de la conexión compartida tiene que caber en un socket de macOS (límite ~104) con
    # los 40 caracteres del %C y los 17 que añade ssh mientras la crea
    control = [o for o in seen[0] if o.startswith("ControlPath=")]
    assert control and len(control[0].split("=", 1)[1].replace("%C", "x" * 40)) + 17 < 100
    shared = control[0].split("=", 1)[1].rsplit("/", 1)[0]
    assert (os.stat(shared).st_mode & 0o777) == 0o700 and os.stat(shared).st_uid == os.getuid()
    assert seen[0][-1] == "cat '/srv/apps/mi bot; rm -rf x'/reports/dashboard.json"
    assert seen[1][-1] == "cd '/srv/apps/mi bot; rm -rf x' && docker compose exec -T trendbot python -m bot.dashboard"


def test_the_banana_photo_goes_inside_the_page_only_if_present(panel, tmp_path):
    mod, base = panel
    mod.Handler.mascot_file = str(tmp_path / "no-hay.png")
    code, page = ask(base + "/")
    assert code == 200 and "\nconst MASCOTA = '';\n" in page        # sin foto: el dibujo de siempre
    photo = tmp_path / "mascota.png"
    photo.write_bytes(b"\x89PNG\r\n\x1a\nplatano")
    mod.Handler.mascot_file = str(photo)
    code, page = ask(base + "/")
    assert code == 200 and "const MASCOTA = 'data:image/png;base64,iVBORw0KGgpwbGF0YW5v';" in page
