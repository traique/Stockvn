"""v3.5 – ghi bản sao lên Google Sheets (sheets.py): tắt khi thiếu biến; JWT RS256 đúng; tab tự tạo kèm dòng tiêu đề; ghi
gộp theo con trỏ, chạy lại không ghi trùng; ghi lỗi thì con trỏ giữ nguyên và lượt sau ghi lại; ô chữ không chứa khoá/token/
URL có mật khẩu; không bao giờ đụng tab proposals; tổng kết ngày một lần mỗi ngày."""
import base64
import json
import os
import shutil
import sys
import time
import traceback
from types import SimpleNamespace as NS

D = "/tmp/okx_test_sheets"
shutil.rmtree(D, ignore_errors=True)
SECRET_TOKEN = "123456789:AAFakeTelegramTokenForRedactTest"
os.environ.update(MODE="paper", DATA_DIR=D, AI_ENABLED="false", DATABASE_URL="", TELEGRAM_TOKEN=SECRET_TOKEN,
                  SHEETS_ID="", SHEETS_SA_JSON="", GRID_ENABLED="true", GRID_PAIRS="BTC-USDT")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bot.config import cfg
from bot import sheets as SH
from bot.storage import Store

FAILED = []


def section(name):
    def run(fn):
        try:
            fn()
            print("✓", name)
        except Exception:
            FAILED.append(name)
            print("✗", name, "\n" + traceback.format_exc(limit=4))
        return fn
    return run


def make_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_key_bytes if False else k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                            serialization.NoEncryption()).decode()
    return k, pem


KEY, PEM = make_key()
SA = {"type": "service_account", "client_email": "bot@proj.iam.gserviceaccount.com", "private_key": PEM,
      "token_uri": "https://oauth2.googleapis.com/token"}


class Resp:
    def __init__(self, code=200, data=None, text=""):
        self.status_code, self._data, self.text = code, data if data is not None else {}, text
    def json(self): return self._data


class FakeHTTP:
    """Google giả: ghi nhớ mọi lời gọi; tabs / rows là trạng thái bảng tính; fail_append = số lần append lỗi 503 còn lại."""
    def __init__(self):
        self.calls, self.tabs, self.rows, self.fail_append, self.tokens = [], ["proposals"], {}, 0, 0
    def post(self, url, data=None, json=None, params=None, headers=None, timeout=None):
        self.calls.append(("POST", url, data, json, params, headers))
        if url == SH.TOKEN_URL:
            self.tokens += 1
            assert data["grant_type"].endswith("jwt-bearer") and data["assertion"].count(".") == 2
            return Resp(200, {"access_token": "ya29.FAKE_ACCESS_TOKEN_abcdefghijklmnop", "expires_in": 3599})
        assert headers and headers["Authorization"].startswith("Bearer ya29."), headers
        if url.endswith(":batchUpdate"):
            title = json["requests"][0]["addSheet"]["properties"]["title"]
            self.tabs.append(title)
            return Resp(200, {})
        if ":append" in url:
            if self.fail_append > 0:
                self.fail_append -= 1
                return Resp(503, {}, "backend error " + SECRET_TOKEN)
            title = url.split("/values/")[1].split("!")[0]
            assert title in self.tabs, title
            self.rows.setdefault(title, []).extend(json["values"])
            return Resp(200, {"updates": {"updatedRows": len(json["values"])}})
        raise AssertionError("POST lạ: " + url)
    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, None, None, params, headers))
        assert headers["Authorization"].startswith("Bearer ")
        return Resp(200, {"sheets": [{"properties": {"title": t}} for t in self.tabs]})


@section("tắt khi thiếu biến: không luồng, không gọi mạng; SA JSON hỏng → tắt có lý do; SA base64 đọc được")
def _():
    st = Store()
    bot = NS(store=st)
    ex = SH.SheetsExporter(bot)
    assert not ex.enabled and ex.start() is None
    ex.tick()                                       # không lỗi, không làm gì
    cfg.SHEETS_ID, cfg.SHEETS_SA_JSON = "sheet1", "{bad json"
    ex2 = SH.SheetsExporter(bot)
    assert not ex2.enabled and "không hợp lệ" in ex2.last_error
    assert SH.load_sa(base64.b64encode(json.dumps(SA).encode()).decode())["client_email"] == SA["client_email"]
    assert SH.load_sa(json.dumps(SA))["client_email"] == SA["client_email"] and SH.load_sa("") is None
    cfg.SHEETS_ID, cfg.SHEETS_SA_JSON = "", ""


@section("JWT RS256: header/claims chuẩn Google, chữ ký kiểm được bằng khoá công khai")
def _():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    tok = SH.sign_jwt(SA, now=1_800_000_000)
    h, c, s = tok.split(".")
    pad = lambda x: x + "=" * (-len(x) % 4)  # noqa: E731
    assert json.loads(base64.urlsafe_b64decode(pad(h))) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(base64.urlsafe_b64decode(pad(c)))
    assert claims == {"iss": SA["client_email"], "scope": SH.SCOPE, "aud": SH.TOKEN_URL, "iat": 1_800_000_000, "exp": 1_800_003_600}
    KEY.public_key().verify(base64.urlsafe_b64decode(pad(s)), f"{h}.{c}".encode(), padding.PKCS1v15(), hashes.SHA256())


def seed(st):
    """Dữ liệu mẫu: 2 lệnh đóng (main, noai), 1 lệnh mở, 2 tín hiệu có kết quả + 1 chờ, vốn 5 phút, 1 vòng grid + khớp."""
    now = time.time()
    for tb in ("trades", "signals", "equity", "grid_runs", "grid_fills", "grid_equity"):
        st.db.run(f"DELETE FROM {tb}")
    st.db.run("DELETE FROM kv WHERE k='sheets_cursor'")
    for book, pnl, sid in (("main", 0.1, "s1"), ("noai", -0.2, "s1")):
        tid = st.open_trade(dict(mode="paper", book=book, symbol="X/USDT:USDT", side="long", entry=100.0, sl=98.0, sl0=98.0, tp=104,
                                 notional=10.0, notional0=10.0, margin=1, contracts=1, status="open", opened_at=now - 7200, sid=sid,
                                 reason_open="t | AI: key " + SECRET_TOKEN, ai_verdict="LONG 80%"))
        st.close_trade(tid, 101, pnl, 10, 0.02, "TP postgresql://u:secretpw@host/db")
    st.open_trade(dict(mode="paper", book="main", symbol="Y/USDT:USDT", side="long", entry=1, sl=0.9, tp=1.2, notional=5,
                       status="open", opened_at=now))
    for sid, out, src in (("s1", "TP", "taken"), ("s2", "SL", "ai_rejected"), ("s3", "PENDING", "not_sent")):
        st.db.run("INSERT INTO signals(ts, symbol, side, score, taken, sid, source, entry, sl, tp, features, outcome, outcome_r, "
                  "outcome_ts, r_hybrid) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (now - 3600, "X/USDT:USDT", "long", 6.0, 1, sid, src, 100, 98, 104,
                   json.dumps({"setup": "trend", "hour": 21, "triggers": ["pullback"], "rejected": "Bearer " + "x" * 20}),
                   out, 1.0 if out != "PENDING" else None, now - 1800 if out != "PENDING" else None, 1.0))
    t0 = int(now // 900) * 900 - 3600
    for i in range(12):                       # 5 phút × 12 = 1 giờ: 4 khung 15 phút
        st.db.run("INSERT INTO equity(ts, equity, mode, book) VALUES(?,?,?,?)", (t0 + 300 * i, 10 + i * 0.01, "paper", "main"))
        st.db.run("INSERT INTO equity(ts, equity, mode, book) VALUES(?,?,?,?)", (t0 + 300 * i, 10 - i * 0.01, "paper", "noai"))
    rid = st.grid_add_run("BTC-USDT", 1, 100, 200, 11, "arithmetic", 100, 150, t0, {"x": 1})
    st.grid_add_fills([(t0 + 60, rid, "BTC-USDT", 4, 140, "buy", 0.07, 0.01, 0.0), (t0 + 120, rid, "BTC-USDT", 5, 150, "sell", 0.07, 0.01, 0.6)])
    st.grid_add_equity(t0 + 900, rid, "BTC-USDT", 50, 0.3, 150, 95, 99, 0.6)
    return rid


def exporter(st, http):
    bot = NS(store=st, compare=lambda: {"since": 1, "days": 3, "books": {
        "main": {"name": "m", "n": 1, "open": 1, "mean_r": 0.5, "se": 0, "t": 0, "days": 1, "winrate": 100, "equity": 10.1, "ret_pct": 1,
                 "max_dd_pct": 0, "fees": 0.02, "fee_share": 20},
        "noai": {"name": "n", "n": 1, "open": 0, "mean_r": -1, "se": 0, "t": 0, "days": 1, "winrate": 0, "equity": 9.8, "ret_pct": -2,
                 "max_dd_pct": 2, "fees": 0.02, "fee_share": None}}, "paired": {"n": 1, "diff": -1.5, "se": 0}},
             learner=NS(public_state=lambda: {"params": {"min_score": 5.0, "blocks": ["night_session"]}}),
             grid=NS(snapshot=lambda: [{"pair": "BTC-USDT", "state": "đang chạy", "rounds": 1, "value": 99.6, "ret_pct": -0.4,
                                        "max_dd_pct": 1, "fees": 0.02, "vs_hodl_pct": 0.6, "low": 100, "high": 200, "levels": 11,
                                        "hodl_pct": -1, "in_range_pct": 100, "stopped": None}]))
    cfg.SHEETS_ID, cfg.SHEETS_SA_JSON = "sheet1", json.dumps(SA)
    ex = SH.SheetsExporter(bot, client=SH.SheetsClient("sheet1", SA, http=http))
    cfg.SHEETS_ID, cfg.SHEETS_SA_JSON = "", ""
    return ex


@section("ghi gộp mỗi giờ: tab tự tạo kèm tiêu đề, một append mỗi tab, đúng dòng; chạy lại / tiến trình mới không ghi trùng")
def _():
    st = Store()
    rid = seed(st)
    http = FakeHTTP()
    ex = exporter(st, http)
    n = ex.export_hourly()
    assert set(http.tabs) >= {"alt_trades", "alt_signals", "alt_equity", "grid_runs", "grid_fills"}
    for tab in ("alt_trades", "alt_signals", "alt_equity", "grid_runs", "grid_fills"):
        assert http.rows[tab][0] == SH.TABS[tab], tab                       # dòng tiêu đề
        appends = [c for c in http.calls if c[0] == "POST" and f"/values/{tab}!" in c[1]]
        assert len(appends) == 1, (tab, len(appends))                        # tiêu đề + dữ liệu trong MỘT lần gộp
    assert len(http.rows["alt_trades"]) == 3 and {r[1] for r in http.rows["alt_trades"][1:]} == {"main", "noai"}
    assert http.rows["alt_trades"][1][12] == 0.5 or http.rows["alt_trades"][2][12] == 0.5       # R = 0.1 / 0.2
    assert len(http.rows["alt_signals"]) == 3 and {r[1] for r in http.rows["alt_signals"][1:]} == {"s1", "s2"}   # PENDING không ghi
    eq = http.rows["alt_equity"][1:]
    assert sum(1 for r in eq if r[2] == "main") == 4 and sum(1 for r in eq if r[2] == "noai") == 4       # 15 phút, không phải 5
    assert sum(1 for r in eq if r[2].startswith("grid")) == 1 and [r for r in eq if r[2].startswith("grid")][0][4] == 99
    assert len(http.rows["grid_runs"]) == 2 and http.rows["grid_runs"][1][0] == f"{rid}:start"
    assert len(http.rows["grid_fills"]) == 3
    assert n == 5 + 2 + 2 + 9 + 1 + 2, n                                    # 5 dòng tiêu đề + dữ liệu
    assert http.tokens == 1
    # chạy lại: không có gì mới → không append
    before = len(http.calls)
    assert ex.export_hourly() == 0 and len(http.calls) == before
    # tiến trình mới (con trỏ trong kv), có thêm 1 lệnh đóng và vòng grid dừng → chỉ dòng mới
    tid = st.open_trade(dict(mode="paper", book="main", symbol="Z/USDT:USDT", side="short", entry=100.0, sl=102.0, sl0=102.0,
                             tp=96, notional=10.0, notional0=10.0, margin=1, contracts=1, status="open", opened_at=time.time()))
    st.close_trade(tid, 99, 0.05, 5, 0.02, "TP")
    st.grid_save_run(rid, {"x": 2}, ended_at=time.time(), reason="phá vùng")
    ex2 = exporter(st, http)
    assert ex2.export_hourly() == 2
    assert len(http.rows["alt_trades"]) == 4 and http.rows["alt_trades"][-1][3] == "Z/USDT:USDT"
    assert len(http.rows["grid_runs"]) == 3 and http.rows["grid_runs"][-1][0] == f"{rid}:end" and http.rows["grid_runs"][-1][12] == "phá vùng"


@section("ghi lỗi: con trỏ giữ nguyên, không làm dừng bot (tick nuốt lỗi, ghi last_error đã redact), lượt sau ghi lại đủ")
def _():
    st = Store()
    seed(st)
    http = FakeHTTP()
    http.fail_append = 1
    ex = exporter(st, http)
    ex.tick(now=time.time())
    assert ex.last_error and "503" in ex.last_error and SECRET_TOKEN not in ex.last_error, ex.last_error
    assert ex._cursor().get("alt_trades") is None               # tab đầu lỗi → con trỏ chưa đổi
    assert "alt_trades" in http.tabs and len(http.rows.get("alt_trades", [])) == 0     # tab đã tạo, tiêu đề chờ lượt sau
    ex.tick(now=time.time() + 60)                   # lượt phút sau, chưa tới giờ ghi: lỗi vẫn hiện, không "thành công" giả
    assert ex.last_error and "503" in ex.last_error and ex.last_ok is None
    ex.tick(now=time.time() + cfg.SHEETS_INTERVAL + 1)
    assert ex.last_error is None and len(http.rows["alt_trades"]) == 3 and ex._cursor().get("alt_trades")
    assert len(http.rows["alt_signals"]) == 3
    last_id = max(r["id"] for r in st.grid_fills_after(0))
    assert isinstance(ex._cursor().get("grid_fills"), int) and ex._cursor()["grid_fills"] == last_id      # con trỏ theo id


@section("không lộ khoá: mọi ô chữ qua redact (token Telegram, URL có mật khẩu, Bearer); không đọc/ghi tab proposals")
def _():
    st = Store()
    seed(st)
    http = FakeHTTP()
    ex = exporter(st, http)
    ex.export_hourly()
    ex.export_summary("2026-11-29")
    dump = json.dumps(http.rows, ensure_ascii=False)
    assert SECRET_TOKEN not in dump and "secretpw" not in dump and "Bearer xxxxxxxx" not in dump
    assert "***" in dump
    assert "proposals" not in http.rows and not any("proposals" in c[1] for c in http.calls)
    try:
        ex.client.append("proposals", [["x"]])
        raise SystemExit("phải chặn")
    except ValueError:
        pass
    assert all("values/" not in c[1] or c[0] == "POST" for c in http.calls)        # không GET values (không đọc ngược)


@section("alt_summary: một lần mỗi ngày sau SHEETS_SUMMARY_TIME, dòng cho main, noai và grid; tham số đang chạy không chứa khoá")
def _():
    st = Store()
    seed(st)
    http = FakeHTTP()
    ex = exporter(st, http)
    day = time.strftime("%Y-%m-%d")
    early = time.mktime(time.strptime(day + " 06:00", "%Y-%m-%d %H:%M"))
    ex.tick(now=early)
    assert "alt_summary" not in http.rows
    ex.tick(now=early + 2 * 3600)              # 08:00 > 07:05
    rows = http.rows["alt_summary"]
    assert rows[0] == SH.TABS["alt_summary"] and [r[2] for r in rows[1:]] == ["main", "noai", "grid BTC-USDT"], rows
    assert rows[2][14] == -1.5 and json.loads(rows[1][17])["min_score"] == 5.0 and "leverage" in rows[1][17]
    ex.tick(now=early + 3 * 3600)
    assert len(http.rows["alt_summary"]) == 4                 # không ghi lần hai trong ngày
    assert ex.state()["enabled"] and ex.state()["written"]["alt_summary"] == 4


assert not FAILED, f"{len(FAILED)} phần lỗi: {FAILED}"
print("TẤT CẢ OK")
