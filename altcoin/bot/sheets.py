"""Ghi bản sao dữ liệu lên Google Sheets (bảng tính BotLab) – v3.5, Phần C1 của kế hoạch 29/11/2026.

Sheets CHỈ là bản sao để đọc trên điện thoại (Claude trong app Claude đọc qua connector Google); database chính vẫn là
SQLite / Postgres. Bot KHÔNG BAO GIỜ đọc ngược cấu hình hay tab `proposals` từ Sheets.

Tab (mỗi tab tối đa một lần gọi append mỗi lượt, dưới hạn mức ~60 lần ghi/phút của Google):
  alt_trades   lệnh đã đóng của sổ main và noai (R, phí, lý do thoát, phán quyết AI)        mỗi giờ
  alt_signals  tín hiệu đã có kết quả (cả tín hiệu bóng) kèm nguồn                            mỗi giờ
  alt_equity   vốn mỗi 15 phút của main, noai, từng sổ grid và đường HODL (gộp một tab)       mỗi giờ
  grid_runs, grid_fills                                                                      mỗi giờ
  alt_summary  chỉ số tính sẵn theo sổ, phiên bản, tham số đang chạy                          07:05 hằng ngày (SHEETS_SUMMARY_TIME)
Mỗi dòng có khoá ở cột đầu; con trỏ (khoá cuối đã ghi của từng tab) lưu trong kv "sheets_cursor" nên khởi động lại không ghi
trùng. Ghi lỗi (mạng, hạn mức, 5xx) → con trỏ giữ nguyên, lượt sau thử lại, ghi sự kiện WARNING – không làm dừng hay chậm bot
(luồng riêng, chỉ leader). Mọi ô chữ đi qua eventlog.redact (không khoá, token, URL có mật khẩu). Không lưu nến 1 phút.

Xác thực: service account (SHEETS_SA_JSON = JSON khoá, nguyên văn hoặc base64; SHEETS_ID = id bảng tính; trong Stockvn:
ALT_SHEETS_ID, ALT_SHEETS_SA_JSON qua allowlist ALT_). Ký JWT RS256 bằng `cryptography` (ccxt kéo theo sẵn) rồi đổi lấy
access token; gọi REST v4 bằng requests – không thêm thư viện Google (ổ Botkeep 1 GB). Thiếu biến → tính năng tắt, bot như cũ.
"""
import base64
import json
import logging
import threading
import time
from .config import cfg
from .eventlog import redact

log = logging.getLogger("sheets")
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPE = "https://www.googleapis.com/auth/spreadsheets"
FORBIDDEN_TABS = ("proposals",)          # không bao giờ đọc / ghi
TABS = {
    "alt_trades": ["key", "book", "id", "symbol", "side", "opened_at", "closed_at", "entry", "exit", "sl0", "notional0", "pnl",
                   "r", "fee", "reason_close", "exit_mode", "score", "risk_mult", "ai_model", "ai_conf", "ai_verdict", "sid"],
    "alt_signals": ["key", "sid", "ts", "symbol", "side", "score", "source", "outcome", "outcome_r", "r_hybrid", "r_fixed",
                    "r_trail", "r_sl25_trail", "ai_decision", "ai_conf", "entry", "sl", "tp", "rejected", "setup", "hour",
                    "triggers", "btc_trend_1d"],
    "alt_equity": ["key", "ts", "series", "value", "hodl"],
    "grid_runs": ["key", "id", "pair", "round", "low", "high", "levels", "spacing", "capital", "start_price", "started_at",
                  "ended_at", "reason"],
    "grid_fills": ["key", "ts", "run_id", "pair", "level", "price", "side", "amount", "fee", "profit"],
    "alt_summary": ["key", "date", "book", "version", "build", "n_trades", "mean_r", "se", "winrate", "equity", "ret_pct",
                    "max_dd_pct", "fees", "fee_share", "paired_diff", "paired_se", "paired_n", "params"],
}


def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def load_sa(raw):
    """JSON service account từ chuỗi nguyên văn hoặc base64 (một dòng trên panel)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.startswith("{"):
        raw = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
    sa = json.loads(raw)
    for k in ("client_email", "private_key"):
        if not sa.get(k):
            raise ValueError(f"SHEETS_SA_JSON thiếu trường {k}")
    return sa


def sign_jwt(sa, now=None, scope=SCOPE):
    """JWT RS256 của service account (claims chuẩn của Google: iss, scope, aud, iat, exp)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    now = int(time.time() if now is None else now)
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {"iss": sa["client_email"], "scope": scope, "aud": sa.get("token_uri") or TOKEN_URL, "iat": now, "exp": now + 3600}
    msg = _b64url(json.dumps(header, separators=(",", ":")).encode()) + "." + \
        _b64url(json.dumps(claims, separators=(",", ":")).encode())
    key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    sig = key.sign(msg.encode(), padding.PKCS1v15(), hashes.SHA256())
    return msg + "." + _b64url(sig)


class SheetsClient:
    """REST v4 tối giản: token, danh sách tab, tạo tab, append. http: module có .post/.get (requests) – test thay bằng giả."""

    def __init__(self, sheet_id, sa, http=None):
        self.sheet_id, self.sa = sheet_id, sa
        if http is None:
            import requests
            http = requests
        self.http = http
        self._token, self._token_exp = None, 0
        self._tabs = None
        self._header = {}          # tab vừa tạo mà chưa ghi dòng tiêu đề (ghi cùng lần append dữ liệu đầu tiên)

    def token(self):
        if self._token and time.time() < self._token_exp - 300:
            return self._token
        r = self.http.post(TOKEN_URL, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                            "assertion": sign_jwt(self.sa)}, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"token {r.status_code}: {redact(r.text[:200])}")
        d = r.json()
        self._token, self._token_exp = d["access_token"], time.time() + int(d.get("expires_in") or 3600)
        return self._token

    def _h(self):
        return {"Authorization": f"Bearer {self.token()}"}

    def tabs(self, force=False):
        if self._tabs is None or force:
            r = self.http.get(f"{API}/{self.sheet_id}", params={"fields": "sheets.properties.title"}, headers=self._h(), timeout=20)
            if r.status_code != 200:
                raise RuntimeError(f"đọc danh sách tab {r.status_code}: {redact(r.text[:200])}")
            self._tabs = [s["properties"]["title"] for s in r.json().get("sheets", [])]
        return self._tabs

    def ensure_tab(self, title, header):
        if title in FORBIDDEN_TABS:
            raise ValueError(f"tab {title} là của Claude, bot không ghi")
        if title in self.tabs():
            return False
        r = self.http.post(f"{API}/{self.sheet_id}:batchUpdate", json={"requests": [{"addSheet": {"properties": {"title": title}}}]},
                           headers=self._h(), timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"tạo tab {title} {r.status_code}: {redact(r.text[:200])}")
        self._tabs.append(title)
        self._header[title] = list(header)
        return True

    def append(self, title, rows):
        if title in FORBIDDEN_TABS:
            raise ValueError(f"tab {title} là của Claude, bot không ghi")
        if not rows:
            return 0
        if title in self._header:
            rows = [self._header[title]] + list(rows)      # tiêu đề đi cùng lần ghi đầu: ghi lỗi thì lượt sau vẫn có tiêu đề
        r = self.http.post(f"{API}/{self.sheet_id}/values/{title}!A1:append",
                           params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                           json={"values": rows}, headers=self._h(), timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"append {title} {r.status_code}: {redact(r.text[:200])}")
        self._header.pop(title, None)
        return len(rows)


def _cell(v):
    """Giá trị ô: số giữ số, chuỗi đi qua redact, None → ''."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, (dict, list, tuple)):
        v = json.dumps(v, ensure_ascii=False)
    return redact(str(v))[:2000]


def _ts(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts))) if ts else ""


class SheetsExporter:
    """Gom dữ liệu mới từ DB theo con trỏ từng tab và ghi gộp lên Sheets (luồng riêng)."""

    def __init__(self, bot, client=None):
        self.bot, self.store = bot, bot.store
        self.enabled = bool(cfg.SHEETS_ID and cfg.SHEETS_SA_JSON)
        self.client = client
        self.last_ok, self.last_error, self.last_run, self.written = None, None, 0, {}
        self._stop = threading.Event()
        if self.enabled and client is None:
            try:
                self.client = SheetsClient(cfg.SHEETS_ID, load_sa(cfg.SHEETS_SA_JSON))
            except Exception as e:
                self.enabled, self.last_error = False, f"SHEETS_SA_JSON không hợp lệ: {type(e).__name__}"
                log.warning("Sheets tắt: %s", self.last_error)
        if not self.enabled:
            log.info("Sheets: tắt (thiếu SHEETS_ID / SHEETS_SA_JSON)" if not self.last_error else "Sheets: tắt")

    # ---------- con trỏ
    def _cursor(self):
        c = self.store.get("sheets_cursor") or {}
        return c if isinstance(c, dict) else {}

    def _set_cursor(self, tab, value):
        c = self._cursor()
        c[tab] = value
        self.store.set("sheets_cursor", c)

    # ---------- dữ liệu mới của từng tab: (rows, cursor mới)
    def _trades(self, cur):
        last_ts, last_id = (cur or [0, 0])[0], (cur or [0, 0])[1]
        rows = self.store.db.run("SELECT * FROM trades WHERE status='closed' AND closed_at IS NOT NULL AND (closed_at>? OR "
                                 "(closed_at=? AND id>?)) ORDER BY closed_at, id LIMIT 500", (last_ts, last_ts, last_id), fetch=True)
        from .books import trade_r
        out = []
        for t in rows:
            r = trade_r(t)
            out.append([f"{t.get('book') or 'main'}:{t['id']}", t.get("book") or "main", t["id"], t["symbol"], t["side"],
                        _ts(t["opened_at"]), _ts(t["closed_at"]), t["entry"], t["exit"], t.get("sl0") if t.get("sl0") is not None else t["sl"],
                        t.get("notional0") or t["notional"], t["pnl"], round(r, 4) if r is not None else "", t.get("fee"),
                        t.get("reason_close"), t.get("exit_mode"), t.get("score"), t.get("risk_mult"), t.get("ai_model"),
                        t.get("ai_conf"), t.get("ai_verdict"), t.get("sid")])
        return out, ([rows[-1]["closed_at"], rows[-1]["id"]] if rows else cur)

    def _signals(self, cur):
        last_ts, last_sid = (cur or [0, ""])[0], (cur or [0, ""])[1]
        rows = self.store.db.run("SELECT * FROM signals WHERE outcome IS NOT NULL AND outcome NOT IN ('PENDING','DUP') AND "
                                 "outcome_ts IS NOT NULL AND (outcome_ts>? OR (outcome_ts=? AND sid>?)) ORDER BY outcome_ts, sid LIMIT 500",
                                 (last_ts, last_ts, last_sid), fetch=True)
        out = []
        for s in rows:
            try:
                f = json.loads(s.get("features") or "{}")
            except Exception:
                f = {}
            out.append([s["sid"], s["sid"], _ts(s["ts"]), s["symbol"], s["side"], s["score"], s.get("source"), s.get("outcome"),
                        s.get("outcome_r"), s.get("r_hybrid"), s.get("r_fixed"), s.get("r_trail"), s.get("r_sl25_trail"),
                        s.get("ai_decision"), s.get("ai_conf"), s.get("entry"), s.get("sl"), s.get("tp"), f.get("rejected"),
                        f.get("setup"), f.get("hour"), ",".join(f.get("triggers") or []), f.get("btc_trend_1d")])
        return out, ([rows[-1]["outcome_ts"], rows[-1]["sid"]] if rows else cur)

    def _equity(self, cur):
        """Vốn 15 phút: main, noai (bảng equity, ghi mỗi 5 phút → lấy dòng đầu của mỗi khung 15 phút) và từng vòng grid."""
        cur = dict(cur or {})
        out = []
        for book in ("main", "noai"):
            last = float(cur.get(book) or 0)
            rows = self.store.db.run("SELECT ts, equity FROM equity WHERE COALESCE(book,'main')=? AND ts>? ORDER BY ts LIMIT 2000",
                                     (book, last), fetch=True)
            bucket = int(last // 900)
            for r in rows:
                b = int(float(r["ts"]) // 900)
                if b > bucket:
                    bucket = b
                    out.append([f"{book}:{int(r['ts'])}", _ts(r["ts"]), book, r["equity"], ""])
                cur[book] = float(r["ts"])
        for run in self.store.grid_runs(limit=50):
            k = f"grid:{run['pair']}:{run['id']}"
            last = float(cur.get(k) or 0)
            rows = self.store.db.run("SELECT * FROM grid_equity WHERE run_id=? AND ts>? ORDER BY ts LIMIT 2000", (run["id"], last), fetch=True)
            for r in rows:
                out.append([f"{k}:{int(r['ts'])}", _ts(r["ts"]), f"grid {run['pair']} v{run.get('round') or 1}", r["value"], r["hodl"]])
                cur[k] = float(r["ts"])
        return out, cur

    def _grid_runs(self, cur):
        """Mỗi vòng ghi hai lần tối đa: lúc mở (ended_at trống) và lúc dừng (khoá id:end)."""
        done = set(cur or [])
        out = []
        for run in reversed(self.store.grid_runs(limit=50)):
            for key, when in ((f"{run['id']}:start", True), (f"{run['id']}:end", bool(run.get("ended_at")))):
                if when and key not in done:
                    out.append([key, run["id"], run["pair"], run.get("round"), run["low"], run["high"], run["levels"], run["spacing"],
                                run["capital"], run["start_price"], _ts(run["started_at"]), _ts(run.get("ended_at")), run.get("reason")])
                    done.add(key)
        return out, sorted(done)[-200:]

    def _grid_fills(self, cur):
        last = float(cur or 0)
        rows = self.store.db.run("SELECT * FROM grid_fills WHERE ts>? ORDER BY ts, run_id, level LIMIT 2000", (last,), fetch=True)
        out = [[f"{r['run_id']}:{int(r['ts'])}:{r['level']}:{r['side']}", _ts(r["ts"]), r["run_id"], r["pair"], r["level"], r["price"],
                r["side"], r["amount"], r["fee"], r["profit"]] for r in rows]
        return out, (rows[-1]["ts"] if rows else cur)

    def _summary_rows(self, date):
        from . import version as ver
        rows = []
        try:
            c = self.bot.compare()
        except Exception as e:
            log.warning("Sheets: compare lỗi: %s", e)
            c = {"books": {}}
        p = c.get("paired") or {}
        params = {}
        try:
            params = self.bot.learner.public_state()["params"]
        except Exception:
            pass
        params.update({"leverage": cfg.LEVERAGE, "risk_per_trade": cfg.RISK_PER_TRADE, "max_total_risk": cfg.MAX_TOTAL_RISK,
                       "btc_daily_gate": cfg.BTC_DAILY_GATE, "learn_mode": cfg.LEARN_MODE, "mode": cfg.MODE})
        for book, s in c.get("books", {}).items():
            rows.append([f"{date}:{book}", date, book, ver.VERSION, ver.build(), s["n"], s["mean_r"], s["se"], s["winrate"],
                         s["equity"], s["ret_pct"], s["max_dd_pct"], s["fees"], s["fee_share"],
                         p.get("diff") if book == "noai" else "", p.get("se") if book == "noai" else "",
                         p.get("n") if book == "noai" else "", json.dumps(params, ensure_ascii=False)])
        try:
            for m in (self.bot.grid.snapshot() if (self.bot.grid and cfg.GRID_ENABLED) else []):
                if m.get("state") == "chưa mở":
                    continue
                rows.append([f"{date}:grid:{m['pair']}", date, f"grid {m['pair']}", ver.VERSION, ver.build(), m["rounds"], "", "",
                             "", m["value"], m["ret_pct"], m["max_dd_pct"], m["fees"], "", m["vs_hodl_pct"], "", "",
                             json.dumps({"low": m["low"], "high": m["high"], "levels": m["levels"], "hodl_pct": m["hodl_pct"],
                                         "in_range_pct": m["in_range_pct"], "state": m["state"], "stopped": m.get("stopped")},
                                        ensure_ascii=False)])
        except Exception as e:
            log.warning("Sheets: grid snapshot lỗi: %s", e)
        return rows

    # ---------- ghi
    def _write(self, tab, rows):
        if not rows:
            return 0
        self.client.ensure_tab(tab, TABS[tab])
        n = self.client.append(tab, [[_cell(v) for v in r] for r in rows])
        self.written[tab] = self.written.get(tab, 0) + n
        return n

    def export_hourly(self):
        """Mỗi tab: lấy dòng mới theo con trỏ, ghi gộp, rồi mới lưu con trỏ (ghi lỗi → con trỏ giữ nguyên, lượt sau thử lại)."""
        cur = self._cursor()
        total = 0
        for tab, fn in (("alt_trades", self._trades), ("alt_signals", self._signals), ("alt_equity", self._equity),
                        ("grid_runs", self._grid_runs), ("grid_fills", self._grid_fills)):
            rows, new = fn(cur.get(tab))
            if rows:
                total += self._write(tab, rows)
            if new != cur.get(tab):
                self._set_cursor(tab, new)
        return total

    def export_summary(self, date=None):
        date = date or time.strftime("%Y-%m-%d")
        if self._cursor().get("alt_summary") == date:
            return 0
        n = self._write("alt_summary", self._summary_rows(date))
        self._set_cursor("alt_summary", date)
        return n

    def tick(self, now=None):
        """Một lượt: mỗi giờ ghi các tab dữ liệu; sau SHEETS_SUMMARY_TIME ghi tổng kết ngày (một lần mỗi ngày)."""
        if not self.enabled:
            return
        now = time.time() if now is None else now
        try:
            n = 0
            if now - self.last_run >= cfg.SHEETS_INTERVAL:
                n += self.export_hourly()
                self.last_run = now
            if time.strftime("%H:%M", time.localtime(now)) >= cfg.SHEETS_SUMMARY_TIME:
                n += self.export_summary(time.strftime("%Y-%m-%d", time.localtime(now)))
            self.last_ok, self.last_error = now, None
            if n:
                log.info("Sheets: đã ghi %d dòng (%s)", n, ", ".join(f"{k} {v}" for k, v in self.written.items()))
        except Exception as e:
            self.last_error = redact(f"{type(e).__name__}: {e}")[:300]
            self.last_run = now          # không dồn dập thử lại: chờ lượt sau
            log.warning("Sheets ghi lỗi (sẽ thử lại sau %d phút): %s", cfg.SHEETS_INTERVAL // 60, self.last_error)

    def state(self):
        return {"enabled": self.enabled, "last_ok": self.last_ok, "last_error": self.last_error, "written": self.written,
                "cursor": {k: (v if not isinstance(v, list) or len(v) < 10 else len(v)) for k, v in self._cursor().items()}}

    # ---------- luồng
    def start(self, active=None):
        if not self.enabled:
            return None
        active = active or (lambda: True)

        def loop():
            while not self._stop.is_set():
                if active():
                    self.tick()
                self._stop.wait(60)
        t = threading.Thread(target=loop, daemon=True, name="sheets")
        t.start()
        return t

    def stop(self):
        self._stop.set()
