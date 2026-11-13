#!/bin/sh
# Compila e instala BananaTrading, la app del Mac con el panel del bot.
#
# Uso:  sh panel/instalar-app.sh [usuario@servidor] [carpeta-del-bot-en-el-servidor] [carpeta-de-destino]
#       Sin argumentos: usuario@tu-servidor, /srv/apps/trendbot y /Applications.
#
# La app lleva dentro su copia del panel (index.html): tras cambiarlo, vuelve a ejecutar esto.
# Necesita las herramientas de línea de comandos de Xcode (swiftc): xcode-select --install
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${1:-usuario@tu-servidor}"
DIR="${2:-/srv/apps/trendbot}"
DEST="${3:-/Applications}"
mkdir -p "$DEST" 2>/dev/null || true   # una carpeta de destino pedida a mano puede no existir aún
[ -w "$DEST" ] || DEST="$HOME/Applications"
mkdir -p "$DEST"

command -v swiftc >/dev/null || { echo "Falta swiftc. Instálalo con: xcode-select --install"; exit 1; }
VERSION="$(git -C "$REPO" log -1 --format=%cd --date=format:%Y.%m.%d 2>/dev/null || echo 1.0)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
APP="$TMP/BananaTrading.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "Compilando…"
swiftc -O -swift-version 5 -target "$(uname -m)-apple-macos12.0" \
  -framework Cocoa -framework WebKit \
  "$REPO/panel/app/BananaTrading.swift" -o "$APP/Contents/MacOS/BananaTrading"
cp "$REPO/panel/index.html" "$APP/Contents/Resources/index.html"
# El plátano de dentro de la app: si existe panel/mascota-local.png (fuera de git) va incrustado en el panel
if [ -f "$REPO/panel/mascota-local.png" ]; then
  sips -Z 480 "$REPO/panel/mascota-local.png" --out "$TMP/mascota.png" >/dev/null
  printf "const MASCOTA = 'data:image/png;base64,%s';" "$(base64 < "$TMP/mascota.png" | tr -d '\n')" > "$TMP/mascota.js"
  awk -v f="$TMP/mascota.js" 'BEGIN { getline linea < f } $0 == "const MASCOTA = '"''"';" { print linea; next } { print }' \
    "$REPO/panel/index.html" > "$APP/Contents/Resources/index.html"
  grep -q "^const MASCOTA = 'data:image/png" "$APP/Contents/Resources/index.html" || echo "Aviso: no se pudo meter el plátano en el panel; saldrá el dibujo."
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>BananaTrading</string>
  <key>CFBundleDisplayName</key><string>BananaTrading</string>
  <key>CFBundleIdentifier</key><string>com.oscarlbs.bananatrading</string>
  <key>CFBundleExecutable</key><string>BananaTrading</string>
  <key>CFBundleIconFile</key><string>icono.icns</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.finance</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>Solo mira: no puede operar ni cambiar nada del bot.</string>
  <key>BananaHost</key><string>$HOST</string>
  <key>BananaDir</key><string>$DIR</string>
</dict></plist>
PLIST

# Icono: si existe panel/icono-local.png (una imagen tuya de 1024x1024, que no va a git) se usa esa;
# si no, el dibujo del plátano de panel/icono.svg
ICONO=""
if [ -f "$REPO/panel/icono-local.png" ]; then
  sips -s format png -z 1024 1024 "$REPO/panel/icono-local.png" --out "$TMP/icono.png" >/dev/null && ICONO="$TMP/icono.png"
elif qlmanage -t -s 1024 -o "$TMP" "$REPO/panel/icono.svg" >/dev/null 2>&1 && [ -f "$TMP/icono.svg.png" ]; then
  ICONO="$TMP/icono.svg.png"
fi
if [ -n "$ICONO" ]; then
  SET="$TMP/icono.iconset"; mkdir "$SET"
  for n in 16 32 128 256 512; do
    sips -z $n $n "$ICONO" --out "$SET/icon_${n}x${n}.png" >/dev/null
    sips -z $((n * 2)) $((n * 2)) "$ICONO" --out "$SET/icon_${n}x${n}@2x.png" >/dev/null
  done
  iconutil -c icns "$SET" -o "$APP/Contents/Resources/icono.icns"
else
  echo "Aviso: no se pudo generar el icono; la app llevará el icono por defecto."
fi

codesign --force --sign - "$APP" >/dev/null 2>&1 || echo "Aviso: no se pudo firmar la app (en este Mac funcionará igual)."

# Fuera la versión anterior (también la de ~/Applications, que era un simple lanzador)
osascript -e 'tell application id "com.oscarlbs.bananatrading" to quit' >/dev/null 2>&1 || true
rm -rf "$DEST/BananaTrading.app"
[ "$DEST" = "$HOME/Applications" ] || rm -rf "$HOME/Applications/BananaTrading.app"
cp -R "$APP" "$DEST/BananaTrading.app"
touch "$DEST/BananaTrading.app"
# Que macOS se entere del icono nuevo sin esperar a su caché
LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
[ -x "$LSREG" ] && "$LSREG" -f "$DEST/BananaTrading.app" >/dev/null 2>&1 || true
echo "Instalada en $DEST/BananaTrading.app (versión $VERSION, servidor $HOST)"
echo "Ábrela desde Launchpad, Spotlight o la carpeta Aplicaciones."
