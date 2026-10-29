"""Lưu trữ: SQLite (1 instance) hoặc PostgreSQL dùng chung (nhiều instance: Render + Botkeep).

Đặt DATABASE_URL=postgres://... (Neon / Supabase / Render Postgres free) để 2 instance thấy cùng một dữ liệu:
số dư ảo, lệnh đang mở, lịch sử, bài học AI, và KHOÁ LEADER (chỉ 1 instance được giao dịch tại một thời điểm).
"""
import json
import logging
import os
import sqlite3
import threading
import time
from .config import cfg

log = logging.getLogger("storage")
_lock = threading.RLock()


class _DB:
    """Adapter tối giản: cùng một SQL kiểu '?' cho cả SQLite và Postgres."""

    def __init__(self, url):
        self.pg = bool(url)
        self.url = url
        self.conn = None
        self._connect()

    def _connect(self):
        if self.pg:
            import psycopg
            from psycopg.rows import dict_row
            url = self.url
            if "sslmode=" not in url and "host=/" not in url:
                url += ("&" if "?" in url else "?") + "sslmode=require"
            # prepare_threshold=None: bắt buộc khi đi qua pooler của Supabase (PgBouncer) –
            # pooler không hỗ trợ server-side prepared statement của psycopg.
            self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row,
                                        connect_timeout=20, prepare_threshold=None,
                                        application_name=f"okx-bot-{cfg.INSTANCE_NAME}"[:60])
        else:
            os.makedirs(cfg.DATA_DIR, exist_ok=True)
            self.conn = sqlite3.connect(os.path.join(cfg.DATA_DIR, "bot.db"), check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.isolation_level = None  # autocommit

    def _sql(self, q):
        return q.replace("?", "%s") if self.pg else q

    def run(self, q, params=(), fetch=False):
        """Thực thi 1 câu lệnh; tự kết nối lại 1 lần nếu Postgres rớt mạng."""
        with _lock:
            for attempt in (1, 2):
                try:
                    cur = self.conn.execute(self._sql(q), params)
                    self.fail_count = 0
                    if fetch:
                        rows = cur.fetchall()
                        return [dict(r) for r in rows]
                    return cur
                except Exception as e:
                    self.fail_count = getattr(self, "fail_count", 0) + 1
                    if self.fail_count >= 8:
                        # đĩa đầy / DB chỉ-đọc kéo dài: thoát để run.py khởi động lại sạch (dọn cache, thử lại)
                        log.critical("DB lỗi liên tục (%s) – thoát để khởi động lại", e)
                        os._exit(3)
                    if attempt == 1:
                        log.warning("DB lỗi (%s) – kết nối lại", e)
                        try:
                            self.conn.close()
                        except Exception:
                            pass
                        try:
                            self._connect()
                        except Exception as e2:
                            log.warning("kết nối lại thất bại: %s", e2)
                        continue
                    raise

    def script(self, statements):
        for s in statements:
            self.run(s)


class Store:
    def __init__(self):
        self.db = _DB(cfg.DATABASE_URL)
        self.pg = self.db.pg
        self._init()
        log.info("Storage: %s", "PostgreSQL dùng chung" if self.pg else "SQLite cục bộ")

    def _init(self):
        pk = "SERIAL PRIMARY KEY" if self.pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
        real = "DOUBLE PRECISION" if self.pg else "REAL"
        self.db.script([
            f"""CREATE TABLE IF NOT EXISTS trades(
                id {pk}, mode TEXT, symbol TEXT, side TEXT,
                entry {real}, exit {real}, sl {real}, tp {real},
                notional {real}, margin {real}, contracts {real},
                pnl {real}, pnl_pct {real}, fee {real},
                opened_at {real}, closed_at {real}, status TEXT,
                reason_open TEXT, reason_close TEXT,
                ai_model TEXT, ai_conf INTEGER, score {real}, order_id TEXT)""",
            f"CREATE TABLE IF NOT EXISTS equity(ts {real}, equity {real}, mode TEXT)",
            "CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT)",
            f"""CREATE TABLE IF NOT EXISTS signals(ts {real}, symbol TEXT, side TEXT, score {real},
                ai_decision TEXT, ai_conf INTEGER, ai_reason TEXT, ai_model TEXT, taken INTEGER)""",
            f"""CREATE TABLE IF NOT EXISTS reviews(ts {real}, trade_id INTEGER, symbol TEXT, side TEXT, pnl {real},
                verdict TEXT, lesson TEXT, tags TEXT, model TEXT)""",
            f"CREATE TABLE IF NOT EXISTS locks(name TEXT PRIMARY KEY, owner TEXT, expires_at {real})",
            f"CREATE TABLE IF NOT EXISTS heartbeats(instance TEXT PRIMARY KEY, ts {real}, role TEXT, info TEXT)",
            f"CREATE TABLE IF NOT EXISTS events(ts {real}, level TEXT, src TEXT, msg TEXT)",
            f"""CREATE TABLE IF NOT EXISTS scans(ts {real}, scanned INTEGER, passed INTEGER,
                reasons TEXT, near TEXT, top TEXT)""",
            f"""CREATE TABLE IF NOT EXISTS learn_log(ts {real}, kind TEXT, k TEXT, old TEXT, new TEXT,
                reason TEXT, n INTEGER, exp_r {real}, status TEXT)""",
        ])
        # cột bổ sung (migration nhẹ, an toàn khi chạy lại)
        self._add_cols("signals", [("sid", "TEXT"), ("source", "TEXT"), ("entry", real), ("sl", real), ("tp", real),
                                   ("features", "TEXT"), ("outcome", "TEXT"), ("outcome_r", real), ("outcome_ts", real),
                                   ("r_fixed", real), ("r_hybrid", real), ("r_trail", real), ("r_tp1v", "TEXT")])
        self._add_cols("trades", [("tp1", real), ("tp1_done", "INTEGER"), ("peak", real), ("peak_ts", real),
                                  ("realized", real), ("atr", real), ("notional0", real), ("exit_mode", "TEXT"),
                                  ("sl0", real), ("risk_mult", real), ("tp1_ts", real)])
        for idx in ("CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts)",
                    "CREATE INDEX IF NOT EXISTS ix_signals_ts ON signals(ts)",
                    "CREATE INDEX IF NOT EXISTS ix_scans_ts ON scans(ts)"):
            try:
                self.db.run(idx)
            except Exception:
                pass

    def _add_cols(self, table, cols):
        for col, typ in cols:
            try:
                if self.pg:
                    self.db.run(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {typ}")
                else:
                    have = [r["name"] for r in self.db.run(f"PRAGMA table_info({table})", fetch=True)]
                    if col not in have:
                        self.db.run(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            except Exception as e:
                log.warning("thêm cột %s.%s: %s", table, col, e)

    # ---- kv
    def get(self, k, default=None):
        r = self.db.run("SELECT v FROM kv WHERE k=?", (k,), fetch=True)
        return json.loads(r[0]["v"]) if r else default

    def set(self, k, v):
        if self.pg:
            self.db.run("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=EXCLUDED.v", (k, json.dumps(v)))
        else:
            self.db.run("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, json.dumps(v)))

    # ---- khoá leader (chỉ 1 instance được giao dịch)
    def try_lock(self, name, owner, ttl):
        """Giành hoặc gia hạn khoá. Trả True nếu owner đang giữ khoá sau lệnh này."""
        now = time.time()
        exp = now + ttl
        if self.pg:
            self.db.run("""INSERT INTO locks(name, owner, expires_at) VALUES(?,?,?)
                           ON CONFLICT(name) DO UPDATE SET owner=EXCLUDED.owner, expires_at=EXCLUDED.expires_at
                           WHERE locks.owner=EXCLUDED.owner OR locks.expires_at < ?""", (name, owner, exp, now))
        else:
            self.db.run("INSERT OR IGNORE INTO locks(name, owner, expires_at) VALUES(?,?,?)", (name, owner, exp))
            self.db.run("UPDATE locks SET owner=?, expires_at=? WHERE name=? AND (owner=? OR expires_at<?)",
                        (owner, exp, name, owner, now))
        r = self.db.run("SELECT owner FROM locks WHERE name=?", (name,), fetch=True)
        return bool(r) and r[0]["owner"] == owner

    def release_lock(self, name, owner):
        self.db.run("UPDATE locks SET expires_at=0 WHERE name=? AND owner=?", (name, owner))

    def lock_owner(self, name):
        r = self.db.run("SELECT owner, expires_at FROM locks WHERE name=?", (name,), fetch=True)
        if r and r[0]["expires_at"] > time.time():
            return r[0]["owner"]
        return None

    def heartbeat(self, instance, role, info=""):
        if self.pg:
            self.db.run("""INSERT INTO heartbeats(instance, ts, role, info) VALUES(?,?,?,?)
                           ON CONFLICT(instance) DO UPDATE SET ts=EXCLUDED.ts, role=EXCLUDED.role, info=EXCLUDED.info""",
                        (instance, time.time(), role, info))
        else:
            self.db.run("INSERT OR REPLACE INTO heartbeats(instance, ts, role, info) VALUES(?,?,?,?)",
                        (instance, time.time(), role, info))

    def instances(self):
        return self.db.run("SELECT * FROM heartbeats ORDER BY instance", fetch=True)

    # ---- trades
    def open_trade(self, t):
        cols = ",".join(t.keys())
        q = ",".join("?" * len(t))
        if self.pg:
            r = self.db.run(f"INSERT INTO trades({cols}) VALUES({q}) RETURNING id", list(t.values()), fetch=True)
            return r[0]["id"]
        cur = self.db.run(f"INSERT INTO trades({cols}) VALUES({q})", list(t.values()))
        return cur.lastrowid

    def close_trade(self, tid, exit_price, pnl, pnl_pct, fee, reason):
        self.db.run("""UPDATE trades SET exit=?, pnl=?, pnl_pct=?, fee=?, closed_at=?,
                       status='closed', reason_close=? WHERE id=?""",
                    (exit_price, pnl, pnl_pct, fee, time.time(), reason, tid))

    def update_trade(self, tid, **fields):
        sets = ",".join(f"{k}=?" for k in fields)
        self.db.run(f"UPDATE trades SET {sets} WHERE id=?", list(fields.values()) + [tid])

    def open_trades(self):
        return self.db.run("SELECT * FROM trades WHERE status='open' ORDER BY id", fetch=True)

    def trades(self, limit=200):
        return self.db.run("SELECT * FROM trades ORDER BY opened_at DESC LIMIT ?", (limit,), fetch=True)

    def last_closed(self, n=10):
        """n lệnh đóng gần nhất, mới nhất trước (tính chuỗi thua để co rủi ro)."""
        return self.db.run("SELECT * FROM trades WHERE status='closed' ORDER BY closed_at DESC LIMIT ?", (n,), fetch=True)

    def closed_trades_since(self, ts):
        return self.db.run("SELECT * FROM trades WHERE status='closed' AND closed_at>=?", (ts,), fetch=True)

    # ---- equity
    def log_equity(self, equity):
        self.db.run("INSERT INTO equity VALUES(?,?,?)", (time.time(), equity, cfg.MODE))

    def equity_curve(self, limit=2000):
        rows = self.db.run("SELECT ts, equity FROM equity ORDER BY ts DESC LIMIT ?", (limit,), fetch=True)
        return [(r["ts"], r["equity"]) for r in reversed(rows)]

    # ---- signals
    def log_signal(self, sig, ai, taken, source="ai"):
        """source: taken | ai_override | ai_rejected | ai_error | not_sent | below_score | blocked"""
        import uuid
        feats = {k: v for k, v in (sig.context or {}).items() if k != "last_candles"}
        feats["triggers"] = list(getattr(sig, "triggers", []) or [])
        feats["reasons"] = sig.reasons
        feats["rr"] = round(sig.rr, 2)
        feats["hour"] = time.localtime().tm_hour
        if getattr(sig, "rejected", ""):
            feats["rejected"] = sig.rejected
        self.db.run("""INSERT INTO signals(ts, symbol, side, score, ai_decision, ai_conf, ai_reason, ai_model, taken,
                       sid, source, entry, sl, tp, features, outcome) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            time.time(), sig.symbol, sig.side, sig.score,
            (ai or {}).get("decision"), (ai or {}).get("confidence"),
            (ai or {}).get("reason"), (ai or {}).get("model"), int(taken),
            uuid.uuid4().hex[:12], source, sig.price, sig.sl, sig.tp, json.dumps(feats, ensure_ascii=False), "PENDING"))

    def pending_signals(self, limit=40):
        return self.db.run("SELECT * FROM signals WHERE outcome='PENDING' AND ts < ? ORDER BY ts LIMIT ?",
                           (time.time() - 300, limit), fetch=True)

    def set_outcome(self, sid, outcome, r, r_fixed=None, r_hybrid=None, r_trail=None, r_tp1v=None):
        """r_tp1v: {"1.0": R, "1.3": R, ...} – kết quả hybrid với từng mức TP1 (để tự học chọn TP1 lời nhất)."""
        self.db.run("""UPDATE signals SET outcome=?, outcome_r=?, outcome_ts=?, r_fixed=?, r_hybrid=?, r_trail=?, r_tp1v=?
                       WHERE sid=?""",
                    (outcome, r, time.time(), r_fixed, r_hybrid, r_trail,
                     json.dumps(r_tp1v) if r_tp1v else None, sid))

    def resolved_signals(self, since=0):
        return self.db.run("""SELECT * FROM signals WHERE outcome IS NOT NULL AND outcome NOT IN ('PENDING','NODATA')
                              AND ts>=? ORDER BY ts""", (since,), fetch=True)

    # ---- nhật ký hoạt động
    def add_events(self, rows):
        for r in rows:
            self.db.run("INSERT INTO events VALUES(?,?,?,?)", r)

    def events(self, limit=300, level=None, q=None, before=None):
        sql, p = "SELECT * FROM events WHERE 1=1", []
        if level:
            sql += " AND level=?"; p.append(level)
        if q:
            sql += " AND msg LIKE ?"; p.append(f"%{q}%")
        if before:
            sql += " AND ts<?"; p.append(before)
        sql += " ORDER BY ts DESC LIMIT ?"; p.append(limit)
        return self.db.run(sql, tuple(p), fetch=True)

    # ---- lịch sử quét
    def add_scan(self, s):
        self.db.run("INSERT INTO scans VALUES(?,?,?,?,?,?)", (s["ts"], s["scanned"], s["passed"],
                    json.dumps(s["reasons"], ensure_ascii=False), json.dumps(s["near"], ensure_ascii=False),
                    json.dumps(s["top"], ensure_ascii=False)))

    def scans(self, limit=100):
        rows = self.db.run("SELECT * FROM scans ORDER BY ts DESC LIMIT ?", (limit,), fetch=True)
        for r in rows:
            for k in ("reasons", "near", "top"):
                try:
                    r[k] = json.loads(r[k] or "null")
                except Exception:
                    pass
        return rows

    # ---- nhật ký tự học
    def add_learn(self, kind, k, old, new, reason, n=0, exp_r=0.0, status="applied"):
        self.db.run("INSERT INTO learn_log VALUES(?,?,?,?,?,?,?,?,?)",
                    (time.time(), kind, k, json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False),
                     reason, int(n), float(exp_r), status))

    def learn_log(self, limit=50):
        return self.db.run("SELECT * FROM learn_log ORDER BY ts DESC LIMIT ?", (limit,), fetch=True)

    def set_learn_status(self, ts, status):
        self.db.run("UPDATE learn_log SET status=? WHERE ts=?", (status, ts))

    # ---- dọn dữ liệu cũ
    def prune(self):
        now = time.time()
        self.db.run("DELETE FROM events WHERE ts<?", (now - 14 * 86400,))
        self.db.run("DELETE FROM scans WHERE ts<?", (now - 30 * 86400,))
        self.db.run("DELETE FROM equity WHERE ts<?", (now - 90 * 86400,))

    # ---- xuất dữ liệu
    EXPORTS = {
        "trades": "SELECT * FROM trades ORDER BY opened_at",
        "signals": "SELECT * FROM signals ORDER BY ts",
        "scans": "SELECT * FROM scans ORDER BY ts",
        "events": "SELECT * FROM events ORDER BY ts",
        "equity": "SELECT * FROM equity ORDER BY ts",
        "reviews": "SELECT * FROM reviews ORDER BY ts",
        "learn_log": "SELECT * FROM learn_log ORDER BY ts",
    }

    def export(self, name):
        return self.db.run(self.EXPORTS[name], fetch=True)

    def recent_signals(self, limit=50):
        return self.db.run("SELECT * FROM signals ORDER BY ts DESC LIMIT ?", (limit,), fetch=True)

    # ---- reviews (tổng kết AI)
    def add_review(self, trade, pnl, rev):
        self.db.run("INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?)", (
            time.time(), trade["id"], trade["symbol"], trade["side"], pnl,
            rev.get("verdict"), rev.get("lesson"), ",".join(rev.get("tags") or []), rev.get("model")))

    def recent_lessons(self, n=5):
        rows = self.db.run("SELECT symbol, side, pnl, verdict, lesson FROM reviews ORDER BY ts DESC LIMIT ?", (n,), fetch=True)
        return [f"{r['side'].upper()} {r['symbol']} {'+' if r['pnl']>0 else ''}{r['pnl']:.2f} [{r['verdict']}]: {r['lesson']}" for r in rows]

    def reviews(self, limit=30):
        return self.db.run("SELECT * FROM reviews ORDER BY ts DESC LIMIT ?", (limit,), fetch=True)

    def stats(self):
        rows = self.db.run("SELECT pnl, pnl_pct FROM trades WHERE status='closed'", fetch=True)
        n = len(rows)
        wins = [r for r in rows if r["pnl"] > 0]
        losses = [r for r in rows if r["pnl"] <= 0]
        gross_win = sum(r["pnl"] for r in wins)
        gross_loss = -sum(r["pnl"] for r in losses)
        return {
            "trades": n, "wins": len(wins), "losses": len(losses),
            "winrate": round(100 * len(wins) / n, 1) if n else 0,
            "total_pnl": round(sum(r["pnl"] for r in rows), 4),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else (round(gross_win, 2) if gross_win else 0),
            "avg_win": round(gross_win / len(wins), 4) if wins else 0,
            "avg_loss": round(-gross_loss / len(losses), 4) if losses else 0,
        }
