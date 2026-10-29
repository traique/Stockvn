"""TradingAgents-VN for a single always-on host (Botkeep): web page + Telegram bot.

One process serves both, runs one analysis at a time (a 2 GB RAM pool holds one
graph comfortably, not several) and sends a daily report for a watchlist.
Start it with ``python main_botkeep.py`` (Python 3.12+); configuration is environment only
(see ``.env.example``). The altcoin bot (``altcoin/``) runs beside it as a supervised
child process when ALTCOIN_ENABLED=1 (``altcoin_host``).
"""
