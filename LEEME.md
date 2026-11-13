# trendbot

Bot de seguimiento de tendencia diario sobre BTC/EUR y ETH/EUR en Binance spot. Solo largo, sin apalancamiento. La prueba (paper) va con 10.000 EUR de mentira; el paso a real, con 100 EUR.

No es asesoramiento financiero. Puede perder dinero; el kill switch limita la pérdida, no la evita.

## Cómo decide

Una vez al día, con la vela diaria ya cerrada:

1. **Señal:** por cada activo votan 12 reglas. Cada regla es una pareja (media de 50, 100, 150 o 200 días; momentum a 20, 30 o 60 días) y vota «dentro» si el cierre supera esa media y el retorno a esos días es positivo. La posición es proporcional a los votos a favor.
2. **Tamaño:** 25 % de volatilidad anual objetivo por activo, tope del 50 % por activo, multiplicado por la fracción de votos. El resto queda en EUR.
3. **Banda:** solo opera si el peso objetivo se aleja más de 10 puntos del actual. Si ninguna regla vota «dentro», sale del todo sin esperar.
4. **Kill switch:** si el equity baja del 75 % del presupuesto, vende todo y se para hasta que lo reactives a mano.

Los parámetros están en `bot/config.py` y no se optimizan sobre el backtest. Que voten 12 reglas en vez de una es precisamente para no depender de haber acertado con una pareja de números.

### Por qué 12 reglas (investigación del 11 nov 2026)

Se compararon variantes con datos que la estrategia nunca había visto: BTC y ETH en USDT de 2018 a 2020 (incluye la caída del 80 %), otras 7 monedas de una en una, costes hasta 8 veces mayores y ejecución con retraso.

- La regla única original (media 100 + momentum 30) ya era sólida: Sharpe 0,83 frente a 0,12 de comprar y mantener en 2018-2020, mejor que comprar y mantener en 6 de 9 monedas, y aguanta costes 4 veces mayores.
- El voto de 12 reglas no gana más de forma demostrable (diferencia de Sharpe +0,03; intervalo del 90 %: −0,12 a +0,21), pero no pierde en ningún conjunto de datos, baja un poco la peor caída, paga menos comisiones y aguanta mejor los costes altos. Sobre todo, quita la dependencia de una sola pareja de parámetros: las reglas sueltas van de Sharpe 1,02 a 1,40.
- Descartado por no aguantar fuera de muestra o por ser elegir a posteriori: añadir una tercera moneda, cambiar la ventana de volatilidad, la banda o el objetivo de volatilidad.
- Ejecutar un día tarde cuesta ~0,1 de Sharpe y sube la peor caída del 34 % al 43 %: importa que el bot corra cada noche.

## Despliegue (servidor con Docker)

El bot corre en un contenedor que se lanza solo cada hora (minuto 7) y solo actúa una vez por vela diaria. No expone puertos, corre sin root, con el sistema de ficheros en solo lectura y con límite de memoria. Lo que guarda (libro, operaciones, informes) vive en `state/`, `data/` y `reports/`, fuera del contenedor.

```bash
cp .env.example .env              # BOT_MODE=paper por defecto
docker compose up -d --build     # crea la imagen y levanta el contenedor
docker compose logs -f            # Ctrl+C para salir; el bot sigue corriendo
```

Comandos útiles:

```bash
docker compose run --rm trendbot python -m bot.backtest                       # backtest con datos reales de Binance
docker compose run --rm trendbot python -m bot.run --dry-run                  # qué haría hoy, sin tocar nada
docker compose run --rm trendbot python -m bot.run --status                   # libro, caja, últimas operaciones
docker compose run --rm trendbot python -m pytest -q -p no:cacheprovider      # tests
docker compose run --rm trendbot python -m bot.notify                         # mensaje de prueba a Telegram
docker compose exec trendbot python -m bot.dashboard                          # la foto de datos que usa el panel
```

## BananaTrading (el panel)

Una app nativa de Mac que enseña cómo va el bot: el dinero, qué vota hoy cada regla, el diario de la prueba y el backtest. Solo mira: no puede operar ni cambiar nada.

```bash
sh panel/instalar-app.sh     # compila la app y la deja en Aplicaciones
```

Luego se abre como cualquier app (Launchpad, Spotlight o la carpeta Aplicaciones). La app lleva dentro su copia del panel: después de cambiar `panel/index.html` o actualizar el repo, vuelve a ejecutar el instalador. Necesita `swiftc` (`xcode-select --install`).

Cómo va por dentro:

- `panel/app/BananaTrading.swift`: una ventana nativa (WebKit) que enseña `panel/index.html` y le trae los datos por SSH con tu clave de siempre. Solo ejecuta dos órdenes fijas en el servidor: leer la última foto (`reports/dashboard.json`) y pedir una nueva (`docker compose exec trendbot python -m bot.dashboard`).
- En el servidor no se abre ningún puerto ni corre nada nuevo.
- `panel/icono.svg`: el plátano del icono. Si pones imágenes tuyas en `panel/icono-local.png` (icono, 1024×1024) y `panel/mascota-local.png` (el plátano con fondo transparente, para dentro de la app), el instalador las usa; no van a git. `panel/server.py`: el mismo panel en un navegador, sin instalar nada (`python3 panel/server.py --open`).

## Alertas por Telegram

Con Telegram configurado, el bot te escribe cada día al procesar la vela (hacia las 00:07 UTC): equity, objetivo y operaciones. También avisa si hay un error, si salta el kill switch o si está parado. **Ese mensaje diario es la señal de vida: si un día no llega, algo va mal.**

1. En Telegram, habla con **@BotFather** → `/newbot` → te da un token.
2. En el servidor, `nano .env` y pega el token en `TELEGRAM_TOKEN=`.
3. Abre tu bot nuevo en Telegram y mándale `/start`.
4. `docker compose run --rm trendbot python -m bot.notify --chat-id` → te dice la línea `TELEGRAM_CHAT_ID=...` para el `.env`.
5. `docker compose run --rm trendbot python -m bot.notify` → te tiene que llegar un mensaje de prueba.
6. `docker compose up -d` para que el bot en marcha coja la configuración nueva.

El token solo vive en `.env`. Quien lo tenga puede escribir como tu bot, pero no leer tu cuenta ni operar.

## Fases

1. **Backtest:** `bot.backtest`. Si sale NO PASA, no se pone dinero.
   - Hecho el 11 nov 2026 con datos reales de Binance en EUR (22 jul 2020 → 10 nov 2026): **PASA**. Sharpe 1,33 frente a 0,84 de comprar y mantener; caída máxima −31,9 % frente a −74,8 %; las 12 reglas sueltas superan a comprar y mantener.
   - Qué esperar: empezando un día cualquiera de ese periodo, el 25 % de las veces el primer año acaba en negativo (mediana +12 %), y el kill switch (−25 %) habría saltado en menos del 1 % de los inicios. Pasa la mayor parte del tiempo por debajo de su máximo: es normal, no una avería.
2. **Paper (dos semanas):** `BOT_MODE=paper`. Precios reales, ejecución simulada, con 10.000 EUR de mentira (`paper_budget` en `bot/config.py`; `BOT_BUDGET` solo cuenta en real). El objetivo es comprobar que corre solo todos los días.
3. **Real con 100 EUR:**
   - En Binance, crea una clave API con **solo** "Enable Spot Trading", retiradas desactivadas y restringida a la IP del servidor.
   - En `.env`: `BOT_MODE=live`, `API_KEY`, `API_SECRET`.
   - Primero `bot.run --dry-run`, luego `docker compose restart`.

El bot lleva su propio libro (`state/live/state.json`) y solo opera con su presupuesto aunque la cuenta tenga más saldo. Antes de operar comprueba que el libro cabe en el saldo libre; si no cuadra, aborta. Si una orden real falla a medias, se para y avisa.

Para reactivarlo tras un paro: revisa la cuenta, pon `"halted": false` en `state/live/state.json` y `docker compose restart`.

## Desarrollo local

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

## Límites conocidos

- Las órdenes reales solo están probadas contra un exchange simulado en los tests: la primera ejecución real se hace mirando.
- El modo paper compra al precio real de venta (ask) y vende al de compra (bid) de ese momento, pero no modela órdenes parciales. El backtest asume 0,05 % de slippage (el spread real medido en BTC/EUR y ETH/EUR ronda el 0,002 %).
- Impuestos: cada venta es ganancia o pérdida patrimonial. `state/live/trades.csv` guarda todas las operaciones.

## Estructura

```
bot/config.py      parámetros y carga de .env
bot/data.py        velas diarias (ccxt, endpoints públicos)
bot/strategy.py    función pura: precios -> pesos objetivo
bot/risk.py        planificación de órdenes, banda, kill switch
bot/broker.py      PaperBroker (simulado) y LiveBroker (ccxt)
bot/state.py       libro del bot (JSON) y registros (CSV)
bot/run.py         job diario idempotente
bot/scheduler.py   bucle del contenedor: cada hora, tolera errores, parada limpia
bot/backtest.py    backtest, comparativa, sensibilidad, veredicto
bot/notify.py      alertas por Telegram (opcional)
bot/dashboard.py   foto de datos para el panel (solo lee)
panel/             BananaTrading: la app del Mac (app/BananaTrading.swift + index.html) y su versión para navegador (server.py)
```
