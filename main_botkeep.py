"""Entry point for Botkeep (or any always-on host): web page + Telegram bot, and the
altcoin bot beside them.

    python main_botkeep.py

Needs Python 3.12 or newer. Reads its settings from the environment or a .env file
beside it (see .env.example). Serves the web page on SERVER_PORT (Botkeep sets
it), PORT or APP_PORT, opened in the first second so the host never sees a
silent port while the libraries load (vnbot.boot), polls Telegram when TELEGRAM_TOKEN is set, analyses the
watchlist each weekday at DAILY_REPORT_TIME and runs the trading bot after the
close at TRADE_TIME (never before 15:05, when the day's bar is final). With
ALTCOIN_ENABLED=1 it also runs the altcoin bot (altcoin/) as a child process, started
once the stock is up and stopped with it (vnbot.altcoin_host), its pages under /altcoin/
behind the same password (vnbot.hub), its commands (/alt) and notifications through this
process's Telegram bot and Zoom app (vnbot.altcoin_bridge).
"""

import logging
import os
import platform
import signal
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

if sys.version_info < (3, 12):      # the code below uses Python 3.12 syntax: say it plainly, not a SyntaxError
    sys.exit(f"TradingAgents VN cần Python 3.12 trở lên (máy đang chạy Python {platform.python_version()}): "
             "chọn Python 3.12+ cho server rồi khởi động lại.")

from vnbot import settings as settings_module  # noqa: E402

SETTINGS = settings_module.load()   # before tradingagents: fixes its data paths

import threading  # noqa: E402

import uvicorn  # noqa: E402

from vnbot.boot import LateApp  # noqa: E402
import vnbot.version  # noqa: E402,F401 - imported first: its STARTED is the start of the process

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _vn_time(seconds):
    """Log timestamps in Vietnam time, whatever the host's timezone (often UTC)."""
    return datetime.fromtimestamp(seconds, VN_TZ).timetuple()


def main() -> None:
    logging.Formatter.converter = staticmethod(_vn_time)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s+07 %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from vnbot import eventlog

    eventlog.install_redaction()    # tokens, keys and passwords masked in every log line

    web = LateApp()
    from vnbot import altcoin_bridge, altcoin_host, boot, webguard

    throttle = webguard.LoginThrottle()     # one wrong-password counter for both sections
    altcoin = altcoin_host.Supervisor(SETTINGS.data_dir, SETTINGS.public_url)
    bridge = altcoin_bridge.Bridge(altcoin)     # always: /alt then says why the altcoin is off
    app = web
    if altcoin.enabled:
        from vnbot import hub, ui

        boot.health_extra = altcoin.health
        ui.SECTIONS = altcoin.state         # the section switch on every private page
        app = hub.Hub(web, altcoin, SETTINGS, throttle)
        _stop_on_signal(altcoin.stop)
    logging.info("Web: listening on 0.0.0.0:%d (SERVER_PORT=%s, PORT=%s, APP_PORT=%s). If the host says the app is "
                 "not responding, its assigned port must match: set SERVER_PORT to it.", SETTINGS.port,
                 os.environ.get("SERVER_PORT", "-"), os.environ.get("PORT", "-"), os.environ.get("APP_PORT", "-"))
    stock_ready = threading.Event()
    threading.Thread(target=_boot, args=(web, stock_ready, throttle, bridge), name="boot", daemon=True).start()
    altcoin.start(stock_ready)      # its own thread: waits for the stock, then starts the child
    try:
        # asyncio, not uvloop: ccxt (the altcoin's) installs uvloop; the stock keeps the loop it was tested on
        uvicorn.run(app, host="0.0.0.0", port=SETTINGS.port, log_level="warning", access_log=False, loop="asyncio")
    finally:
        altcoin.stop()


def _stop_on_signal(stop) -> None:
    """SIGTERM/SIGINT stop the altcoin child, then end the process by that signal as before.
    Installed before uvicorn.run(): uvicorn puts it back and raises the signal again after its
    graceful shutdown; before that (or without uvicorn) it runs at once."""
    def handler(signum, frame):
        stop()
        signal.signal(signum, signal.SIG_DFL)
        signal.raise_signal(signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handler)


def _boot(web: LateApp, ready: threading.Event | None = None, throttle=None, bridge=None) -> None:
    """Everything but the port: runs while the port already answers."""
    try:
        web.ready(_start(throttle, bridge))
    except Exception as exc:  # noqa: BLE001 - shown on every page and /health, and logged
        logging.exception("Start failed")
        web.failed(f"{type(exc).__name__}: {exc}"[:300])
    finally:
        if ready is not None:
            ready.set()


def _start(throttle=None, bridge=None):
    from tradingagents.llm_clients.fallback_client import configured_order
    from vnbot.channels import Switches
    from vnbot.intraday import IntradayMonitor
    from vnbot.jobs import JobQueue, prune_cache
    from vnbot.service import DailyScheduler, Watchlist
    from vnbot.telegram_bot import TelegramBot
    from vnbot.trader import TradingBot
    from vnbot.web import create_app
    from vnbot.zoom import ZoomChannel

    from vnbot import version

    logging.info("TradingAgents VN %s (released %s), build %s: %s", version.VERSION, version.RELEASED,
                 version.build(), "the released code" if version.is_release()
                 else f"NOT the released code ({version.RELEASE_BUILD}): files missing, mixed or edited")
    order = configured_order()
    if not order:
        logging.warning("No AI provider configured: set ROUTER9_BASE_URL+ROUTER9_API_KEY or GROQ/OPENROUTER/XAI keys")
    else:
        logging.info("AI provider chain: %s", " -> ".join(order))
    if not SETTINGS.web_password:
        logging.warning("WEB_PASSWORD is not set: the web page stays locked until it is (Telegram still works)")
    if SETTINGS.telegram_token and not SETTINGS.telegram_allowed_ids:
        logging.warning("TELEGRAM_ALLOWED_IDS is empty: the bot only answers /id until you add your id")
    logging.info("Removed %d stale cache files", prune_cache(SETTINGS))
    from vnbot import eventlog, store as history

    # The history store (every decision and why it is not a BUY, the shadow results, the
    # activity log): SQLite under DATA_DIR, or the shared PostgreSQL of DATABASE_URL.
    eventlog.install(history.open_store(SETTINGS.data_dir))

    jobs = JobQueue(SETTINGS)
    watchlist = Watchlist(SETTINGS)
    bot = TelegramBot(SETTINGS, jobs, watchlist)
    switches = Switches(SETTINGS.data_dir, {"intraday": SETTINGS.intraday_alerts})   # the switches on /ai
    bot.switches = switches
    bot.alt = bridge                    # /alt <lệnh>: the altcoin bot (vnbot.altcoin_bridge)
    zoom = ZoomChannel(state_path=SETTINGS.data_dir / "zoom_state.json")
    zoom.public_url = SETTINGS.public_url
    if zoom.enabled:
        bot.zoom = zoom
        zoom.handler = bot.zoom_command
        zoom.alert_owner = bot.broadcast
    logging.info("Zoom: notifications %s, commands %s", "on" if zoom.notify_on else "off",
                 "on (/webhook/zoom)" if zoom.commands_on else "off")
    trader = TradingBot(SETTINGS, watchlist, notify=bot.broadcast)
    bot.trader = trader
    bot.taisan = trader.taisan          # the owner's real-money portfolio (/ts, the /taisan tab)
    from vnbot import ai_pick

    # The AI's look at the news when a rule alert fires on a holding (only with 9Router set)
    trader.taisan.on_alert = ai_pick.alert_hook(SETTINGS.data_dir, bot.broadcast)
    jobs.start()
    bot.start()
    _announce_version(bot.broadcast)
    DailyScheduler(SETTINGS, jobs, watchlist, bot=trader, notify=bot.broadcast, zoom_notify=bot.zoom_first).start()
    # Always running, whatever TRADE_MODE: the switch on /ai (default INTRADAY_ALERTS) turns the price
    # alerts on and off; the 14:20 reminder of a Tài sản exit ticket goes out from here regardless.
    IntradayMonitor(SETTINGS, trader, bot.broadcast, switches=switches, watchlist=watchlist.get).start()
    logging.info("Intraday alerts: %s, every %d min during the session (no AI); exit-ticket reminders always",
                 "on" if switches.on("intraday") else "off", SETTINGS.intraday_interval_min)
    logging.info("Trading bot: mode %s, calibration %s, runs daily at %s (Vietnam time); watchlist report at %s",
                 SETTINGS.trade_mode, "on" if SETTINGS.calibrate else "off", SETTINGS.trade_time,
                 SETTINGS.daily_report_time)

    if bridge is not None:
        bridge.attach(bot)              # the altcoin's notifications: from now on to the stock's channels
    return create_app(SETTINGS, jobs, watchlist, trader=trader, zoom=zoom, switches=switches, throttle=throttle)


def _announce_version(notify) -> None:
    """Tell the owner (Telegram, Zoom) when this start runs another version or build than
    the last one; quiet on an ordinary restart. Never stops the start."""
    from vnbot import version
    from vnbot.jobs import write_text_atomic

    stamp = SETTINGS.data_dir / "last_version.txt"
    try:
        last = stamp.read_text(encoding="utf-8").strip()
    except OSError:
        last = ""
    try:
        notice = version.update_notice(last)
        if notice:
            notify(notice)
        write_text_atomic(stamp, f"{version.VERSION} {version.build()}")
    except Exception:  # noqa: BLE001 - a notice must never stop the bot
        logging.exception("Version notice failed")


if __name__ == "__main__":
    main()
