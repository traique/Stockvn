# Kế hoạch gộp altcoin_bot vào Stockvn

Tài liệu này là kế hoạch, chưa sửa code. Nguồn: repo `traique/stockvn` (v1.14.0) và `traique/altcoin_bot` (nhánh mặc định `ccr-78e45817-g8v8rx`, build `380dfca`), khảo sát ngày 2026-10-29.

Các sự kiện đã kiểm trong code (chỉ đọc):

- **`altcoin/bot/config.py`**
  - Nạp `.env` rồi `bot.env`, tìm ở `_ROOT` hoặc ở cwd. Giá trị trong file ghi đè `os.environ`.
  - Đặt `TZ` rồi gọi `tzset()`.
  - `MODE` chỉ đọc một lần lúc import. Các chỗ dùng nó đọc `cfg.MODE` lúc gọi hàm (`learner.py:112`, `main.py:334`), nên ép `cfg.MODE` sau khi import vẫn có hiệu lực.
- **`dashboard.py:636`** đọc `DASHBOARD_HOST` bằng `os.getenv` lúc gọi.
- **`strategy.py:387` và `storage.py:239`** dùng `time.localtime().tm_hour`. Vì vậy giờ phiên của altcoin phụ thuộc múi giờ của tiến trình.
- **`main()` của altcoin (`main.py:558-566`)**
  - Thoát `sys.exit(1)` khi `MODE=live` mà thiếu `OKX_API_KEY`.
  - Cũng thoát `sys.exit(1)` khi `os.getenv("I_UNDERSTAND_THE_RISK") != "yes"`.
  - Cài `SIGTERM` để ném `KeyboardInterrupt`.
- **`telegram.py`**
  - `enabled = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)`. Xoá token là mất luôn cả phần gửi.
  - Chỉ leader mới poll. Khi nhận `ok:false` thì poll lại ngay, không nghỉ.
- **`storage.try_lock`**: một tiến trình mang **cùng tên owner** sẽ gia hạn được khoá, tức là thành leader thứ hai.
- **Python của stock phải ≥ 3.12.** `vnbot/config_web.py:80` và `vnbot/taisan_web.py:553` dùng f-string lồng kiểu PEP 701, nên báo SyntaxError trên 3.11. Code altcoin vẫn biên dịch được trên 3.11.
- **Bản dàn dựng gộp:**
  - 161 file, 20 thư mục, 2.492.462 B.
  - Zip theo đúng spec của builder (không ghi mục thư mục): 899.730 B.
  - Fingerprint đo trên bản dàn dựng: stock `7eaa3df`, altcoin `380dfca`. Cả hai bằng `RELEASE_BUILD`.

---

## Chấm điểm 3 phương án (1–10, cao = tốt)

| Tiêu chí | lean-inprocess | isolation-first | unified-shell |
|---|---|---|---|
| Giữ đủ mọi thứ của 2 bên | 9 | **10** | 9 |
| Giới hạn Botkeep (250 file / 4 MB), RAM và đĩa 2 GB | **9** | 8 | **9** |
| Rủi ro hồi quy cho bot stock đang chạy thật | 5 | **9** | 6 |
| An toàn chế độ live | 8 | **9** | **9** |
| Công sức (cao = ít việc) | 5 | **7** | 3 |
| Vận hành (1 lần deploy, log, khởi động lại) | 8 | 7 | **9** |
| **Tổng** | 44 | **50** | 45 |

- **lean-inprocess** (chạy chung tiến trình)
  - Phải sửa khoảng 15 file altcoin.
  - `TZ=Asia/Ho_Chi_Minh` áp cho cả tiến trình, làm đổi khoảng 18 lời gọi giờ "naive" của stock.
  - Altcoin có thể chặn GIL hoặc event loop của stock.
- **isolation-first** (tiến trình con)
  - `bot/` giữ nguyên từng byte. Stock không bị đổi TZ, env hay logging.
  - Cái giá: phải viết proxy và supervisor, cộng một lớp seam monkeypatch có test ghim.
- **unified-shell**
  - Trải nghiệm thống nhất nhất.
  - Nhiều việc nhất và chạm nhiều code stock nhất.

**Chọn: isolation-first làm xương sống.** Ghép thêm các ý sau:

| Ý ghép | Lấy từ |
|---|---|
| Child giữ `TIMEZONE` riêng và kiểm múi giờ thật | unified-shell |
| Bộ đóng gói nghiêm ngặt: không ghi mục thư mục, kiểm file + thư mục ≤ 250, zip tái lập được, test mồi nhử | unified-shell |
| `/health` có khối `altcoin` và `rss_mb` | unified-shell |
| Điều kiện live `ALT_LIVE_ACK=<tên instance>` | unified-shell |
| Chuyển hướng tên miền cũ (`ALTCOIN_LEGACY_HOSTS`) | unified-shell |
| Chỉ khởi động altcoin sau khi stock sẵn sàng | lean, unified |
| ≤ 8 request đồng thời vào `/altcoin` | lean |
| Không bao giờ để altcoin chạy trên DB rỗng | lean (thêm chốt chặn trong code) |
| `pytest.ini` với `python_files = test_suites.py` | lean |
| Kiểm DB stock xem đã lẫn bảng altcoin chưa | lean |
| Bổ sung khi rà soát (xem các mục 1, 4–8) | Đường dẫn chuẩn hoá ở Hub, viết lại `Location` mọi scheme, token Hub↔child, Telegram chỉ gửi, `oom_score_adj`, chặn trùng tên instance, kiểm DB theo bảng, chặn live khi peer đang paper |

---

## 1. Kiến trúc và mô hình tiến trình / luồng

```
Botkeep: python main_botkeep.py                    (lệnh khởi động GIỮ NGUYÊN; Python ≥ 3.12)
PID A – stock, giữ cổng công khai duy nhất (SERVER_PORT > PORT > APP_PORT > 8080)
 ├─ main thread: cài handler SIGTERM/SIGINT → uvicorn.run(Hub, 0.0.0.0, loop="asyncio", access_log=False)
 │   Hub (vnbot/hub.py; lúc import chỉ dùng stdlib → cổng vẫn mở trong <1 s)
 │    ├─ GET/HEAD /altcoin        → 308 Location: /altcoin/ (tương đối, giữ query)
 │    ├─ /altcoin/*               → chuẩn hoá đường dẫn (400 nếu bất thường) → Basic WEB_PASSWORD
 │    │                             + LoginThrottle dùng chung, same-origin cho POST, body ≤ 64 KB, ≤ 8 đồng thời
 │    │                             → httpx stream → http://127.0.0.1:<cổng tạm>/<đường dẫn chuẩn>
 │    │                             → child chưa chạy: trang 503 "Altcoin đang khởi động / tắt" (Retry-After 5)
 │    └─ mọi đường dẫn khác + lifespan → LateApp (vnbot/boot.py, giữ nguyên) → create_app() của stock
 ├─ thread "boot" và các thread stock (giữ nguyên)
 ├─ thread "altcoin-supervisor" (daemon; vnbot/altcoin_host.py): kiểm tra trước → chờ stock (≤ 60 s) → spawn → watchdog
 └─ thread "altcoin-log": đọc stdout của child (utf-8, errors="replace") → "[altcoin] …" đã che bí mật + giữ 200 dòng
PID B – python -u <ROOT>/altcoin/hosted.py --port <tạm> --parent-pid <A> …   (cwd=<ROOT>/altcoin, env dựng riêng)
 └─ main() của bot.main NGUYÊN BẢN; mọi signal.signal, sys.exit, os._exit(3), tzset, ghi os.environ,
    basicConfig và handler gắn root logger chỉ có tác dụng trong PID B.
```

**Vì sao stock giữ cổng công khai:**

- `LateApp` đã bảo đảm mở cổng trong giây đầu.
- Health check của Botkeep đang trỏ vào stock.
- Khoảng 60 URL stock, các link Telegram/Zoom đã gửi đi và các test đều giả định stock nằm ở `/`.

Hub bọc **bên ngoài** `LateApp`. Nhờ vậy `/altcoin` vẫn chạy khi stock đang khởi động, và cả khi `_start()` của stock lỗi.

### Supervisor (`vnbot/altcoin_host.py`, `ROOT = Path(__file__).resolve().parents[1]`)

**Kiểm tra trước khi spawn** (sai thì chuyển trạng thái, không lặp khởi động lại):

| Điều kiện | Trạng thái |
|---|---|
| `ALTCOIN_ENABLED != 1` (mặc định 0, **vĩnh viễn**) | `off` |
| `find_spec("ccxt")` hoặc `find_spec("httpx")` trả None | Có `ALTCOIN_AUTO_INSTALL=1`: chạy logic `ensure_deps` của `run.py` ngay trong thread supervisor (`pip --no-cache-dir`, lần lượt thử `--user`, thường, `--break-system-packages`; sau đó xoá `~/.cache/pip`) rồi kiểm lại. Không có: `disabled: thiếu thư viện`, kèm lệnh gợi ý `pip install --no-cache-dir ccxt==4.5.90`. |
| `ALT_DATA_DIR` trùng `DATA_DIR` của stock | `disabled` |
| SQLite (không có `ALT_DATABASE_URL`) mà có `ALT_PEER_URL` | `disabled: peer cần ALT_DATABASE_URL chung`. Áp cho cả paper, vì nếu không sẽ có hai leader và bão 409 Telegram. |
| DB stock đã có `trades`/`locks`/`learn_log` (truy vấn chỉ đọc `information_schema`) | `disabled` + WARNING "DB đang dùng chung" |

**Cổng.** Bind `127.0.0.1:0`, đọc số cổng, đóng lại, rồi truyền qua `--port`. Mỗi lần spawn một cổng mới.

**Spawn.**

- Lệnh: `Popen([sys.executable, "-u", str(ROOT/"altcoin"/"hosted.py"), "--port", …, "--parent-pid", str(os.getpid()), "--prefix", "/altcoin", "--public-url", …, "--data-dir", …, "--mode", paper|live, "--instance", …, "--deny-sha", …, "--deny-db", …, ["--allow-empty-db"]], cwd=str(ROOT/"altcoin"), env=<§6>, stdin=DEVNULL, stdout=PIPE, stderr=STDOUT, close_fds=True)`.
- Dùng đường dẫn tuyệt đối. Nếu dùng đường dẫn tương đối kèm `cwd`, Python sẽ tìm `altcoin/altcoin/hosted.py` và thoát mã 2. cwd phải là `altcoin/` để `.env` gốc của stock không bao giờ bị nạp.
- Chỉ spawn từ thread supervisor sống lâu, vì `PR_SET_PDEATHSIG` bắn theo luồng tạo child.
- `HUB_TOKEN` (ngẫu nhiên mỗi lần spawn) đi qua env, không qua argv.
- Vòng supervisor bọc `try/except`: lỗi thì ghi log rồi chạy lại vòng.

**Sẵn sàng.** Gọi `GET /health` của child mỗi 2 s, tối đa 120 s, thì chuyển `running`. Quá hạn (ví dụ tranh cổng làm dashboard không bind được trong khi bot vẫn chạy) thì SIGTERM, chờ 15 s, SIGKILL, rồi spawn lại với cổng mới. Lần này tính là một lần restart.

**Watchdog.** `GET /health` mỗi 30 s, timeout 10 s. Trượt 4 lần liên tiếp thì SIGTERM, chờ 15 s, SIGKILL, rồi khởi động lại.

**Mã thoát của child:**

| Mã | Ý nghĩa | Supervisor xử lý |
|---|---|---|
| 78 | Cấu hình sai | `disabled: <lý do>`, không restart |
| 75 | Tạm thời, ví dụ trùng tên instance | Thử lại sau `LEADER_TTL + 10` s |
| -9 | Bị kill | Ghi "bị kill (OOM?)" |
| Mã khác | Lỗi | Backoff 20 s (giống `run.py:67`), nhân đôi tới 600 s. Đặt lại sau 15 phút chạy khoẻ. |

**Dừng.**

- `main_botkeep.py` cài handler `SIGTERM`/`SIGINT` **trước** `uvicorn.run()`. uvicorn (≥ 0.29, đã thấy ở 0.54) khôi phục handler này rồi `raise_signal` sau khi tắt êm. Handler gọi `stop()`, sau đó trả về `SIG_DFL` và tự gửi lại tín hiệu.
- Hub bọc `receive` để bắt `lifespan.shutdown` và gọi `stop()` qua `asyncio.to_thread`.
- Vẫn giữ `finally: stop()` cho trường hợp `uvicorn.run()` trả về bình thường.
- `stop()` gửi SIGTERM, chờ 15 s cho altcoin nhả leader lock, rồi SIGKILL.

**`state()`.** Là một dict bất biến, được thay nguyên khối (gán nguyên tử). `/health` đọc mà không cần khoá, chỉ thêm bước đọc `/proc/<pid>/statm`. Các trường:

`{state, reason, pid, port, started, restarts, last_exit, version, build, leader, mode, telegram, dropped_secrets, rss_mb}`

Child gửi trạng thái riêng qua một dòng stdout `@@hosted {json}`, do thread `altcoin-log` phân tích.

### `altcoin/hosted.py` (mới, nằm ngoài fingerprint altcoin = `bot/**` + `run.py`)

1. **Tự chết theo cha, chết trước khi OOM.**
   - `prctl(PR_SET_PDEATHSIG, SIGTERM)`.
   - So `os.getppid()` với `--parent-pid` ngay sau `prctl`, rồi mỗi 5 s. Lệch thì `os.kill(os.getpid(), SIGTERM)` để `main()` thoát êm và nhả lock; sau 20 s mà chưa thoát thì `os._exit(0)`.
   - Ghi `800` vào `/proc/self/oom_score_adj`, để kernel giết altcoin trước stock.
2. **Nạp cấu hình.** `import bot.config`, đọc `altcoin/.env` và `altcoin/bot.env`, quy tắc file-wins chỉ áp trong child. Sau đó **kiểm múi giờ**: so `time.localtime().tm_gmtoff` với `ZoneInfo(TIMEZONE).utcoffset(now)`.
   - Lệch thì đặt `TZ=":<đường dẫn zoneinfo của gói tzdata>"` rồi `tzset()`.
   - Vẫn lệch thì thoát 78.
3. **Niêm phong và thứ tự ưu tiên** (bảng ở §6).
   - `cfg.DASHBOARD_PORT = port`, `os.environ["DASHBOARD_HOST"] = "127.0.0.1"`.
   - `cfg.DATA_DIR`, `cfg.PUBLIC_URL`, `cfg.INSTANCE_NAME` lấy từ argv.
   - Các khoá nhạy cảm lấy giá trị panel đè lên `bot.env`, mỗi khoá bị đè ghi một WARNING.
   - `DASHBOARD_USER`/`DASHBOARD_PASS` bị xoá trừ khi có `ALT_DASHBOARD_*`.
   - Giá trị `__BOTKEEP_KEEP_SECRET_*__` bị bỏ. Ghi WARNING liệt kê các khoá, và đưa danh sách vào `dropped_secrets`.
4. **Chế độ.** `cfg.MODE = args.mode`; nếu file ghi khác thì ghi log ERROR.
   - **paper:** xoá `cfg.OKX_*` và `os.environ["I_UNDERSTAND_THE_RISK"]`.
   - **live:**
     - đặt `cfg.OKX_*` từ giá trị panel (bỏ qua key trong `bot.env`);
     - đặt `os.environ["I_UNDERSTAND_THE_RISK"] = "yes"` (sau `import bot.config`, nên thắng giá trị `no` trong `bot.env`);
     - chạy lại cả hai kiểm tra của `main()`, sai thì thoát 78. Như vậy không bao giờ chạm tới `sys.exit(1)`.
5. **Chặn va chạm.** Kiểm trên cfg hiệu lực, chỉ đọc, trước khi tạo `Store()`:

   | Kiểm tra | Kết quả |
   |---|---|
   | `sha256(TELEGRAM_TOKEN.strip())` nằm trong `--deny-sha` (stock cũng `strip`) | Vá `bot.telegram.Telegram.start` thành no-op: **vẫn gửi, không poll**. Báo `telegram: send-only`. Seam này mất thì xoá token (an toàn hơn 409). |
   | `DATABASE_URL` đã chuẩn hoá trùng `--deny-db` | 78 |
   | DB Postgres có bảng `market` hoặc `fills` trong `public` (cùng DB stock qua endpoint khác) | 78 |
   | SQLite + `PEER_URL` (kể cả lấy từ `bot.env`) | 78 |
   | DB rỗng: SQLite chưa có file, hoặc kv thiếu cả `start_equity` lẫn `paper_balance`, và không có `--allow-empty-db` | 78 "DB rỗng – chép DB cũ hoặc đặt ALTCOIN_FRESH_START=1" |
   | `heartbeats` có cùng tên instance, `ts` trong `LEADER_TTL`, `host=` khác `os.uname().nodename` | 75 "tên instance đang được dùng" |
   | live, và một instance khác trong `LEADER_TTL` có `paper=True` | 78 |

6. **Thích nghi web** (chi tiết ở §4). Middleware từ chối mọi request thiếu `X-Hub-Token` đúng, trừ `GET /health`. Mỗi seam có bước tự kiểm: thiếu thuộc tính thì ghi log lỗi và chạy giảm chức năng.
7. **`--check`.** In cấu hình đã niêm phong ra JSON. Không gọi mạng, không import ccxt.
8. `from bot.main import main; main()`.

### Đánh đổi

- **Tốn:**
  - child tốn ≈155 MB RSS (đo), trong đó ≈80 MB là phần trội so với chạy chung tiến trình (ước tính);
  - ≈1 ms cho chặng localhost;
  - ≈450 dòng code host;
  - child mất 2–5 s mới sẵn sàng (`import ccxt` mất 2,3 s lúc nguội);
  - seam có thể gãy khi altcoin đổi tên hàm;
  - mỗi lần deploy hoặc restart stock thì altcoin cũng restart.
- **Được:**
  - cô lập lỗi hai chiều (kể cả OOM, nhờ `oom_score_adj`);
  - `bot/` giống từng byte bản trên Render;
  - stock giữ nguyên TZ, env, logging;
  - child không bao giờ thấy `WEB_PASSWORD`, `ROUTER9_*` hay bí mật khác của stock;
  - `git subtree pull` vào `bot/**` và `run.py` vẫn sạch cho tới khi repo cũ được archive. Các file ngoài `bot/` đã sửa (`README.md`, `check_db.py`, `requirements.txt`, `render.yaml` đã dời) phải giải xung đột tay.

### File stock bị sửa / thêm

- **Sửa:**
  - `main_botkeep.py`: kiểm `sys.version_info >= (3,12)` (sai thì ghi thông báo rõ rồi thoát 1); handler tín hiệu; supervisor; `loop="asyncio"`; docstring.
  - `vnbot/ui.py`, `vnbot/web.py`, `vnbot/boot.py`, `vnbot/eventlog.py`, `vnbot/version.py`, `vnbot/__init__.py` (docstring), `requirements.txt`, `README.md`.
- **Mới:**
  - `vnbot/hub.py`, `vnbot/altcoin_host.py`.
  - `vnbot/webguard.py`. Đây là **refactor code bảo mật**, không phải dời nguyên: các closure `password_in`/`guard`/`same_origin` (`web.py:474–532`) được tách thành hàm thuần `check_basic(header, expected, throttle, client)` và `same_origin_ok(headers, allowed_hosts)`. `create_app` uỷ quyền cho chúng và giữ `app.state.login_throttle`.

---

## 2. Cây thư mục đích

```
/  (traique/stockvn)
├── main_botkeep.py                 Hub + supervisor + kiểm Python 3.12 + handler tín hiệu
├── vnbot/                          stock + hub.py, altcoin_host.py, webguard.py (mới)
├── tradingagents/                  giữ nguyên
├── requirements.txt                stock + "ccxt==4.5.90" + "httpx>=0.27"
├── requirements-dev.txt            giữ nguyên
├── pytest.ini                      MỚI: testpaths = tests ; python_files = test_suites.py
├── render.yaml                     ← altcoin/render.yaml (git mv) + rootDir: altcoin + buildFilter
├── .env.example                    stock + mục "ALTCOIN (ALT_* / ALTCOIN_*)"
├── .gitignore                      stock + bot.env, .bt_cache/, bt_result*.json, !altcoin/dist/
├── check_db.py                     stock, giữ nguyên
├── README.md                       stock + mục "Hai phần" + sửa dòng cũ + Python 3.12
├── LICENSE, BAO_CAO_RA_SOAT_2026-10-23.md
├── scripts/build_botkeep_bundle.py MỚI
├── docs/ALTCOIN_TRONG_STOCKVN.md   MỚI
├── tests/  run_all.py, test_suites.py, fixtures/, suites/ (22 stock + 5 mới:
│           suite_bundle, suite_altcoin_scripts, suite_altcoin_host, suite_altcoin_proxy, suite_altcoin_web)
└── altcoin/                        git subtree từ altcoin_bot (8 commit) + nhánh ccr-46f71b1d-mqcst3
    ├── bot/                        21 .py, KHÔNG sửa (build 380dfca)
    ├── run.py                      giữ (Render/VPS; nằm trong fingerprint)
    ├── hosted.py                   MỚI (ngoài fingerprint)
    ├── test_engine.py test_learner.py test_risk.py test_smoke.py test_strategy.py test_web.py
    ├── tools/ kronos_analyze.py kronos_eval.py requirements-kronos.txt README.md backtest_sessions.py
    ├── README.md (+5 dòng đầu)  .env.example  botkeep.env  check_db.py (sửa)  requirements.txt (ghim ccxt)  .gitignore
    └── dist/okx-ai-bot-v3.2.0.zip  tracked; không bao giờ vào gói Botkeep
```

| File altcoin | Vị trí sau gộp | Ghi chú |
|---|---|---|
| `bot/*.py` (21) | `altcoin/bot/` | Không sửa. `version.py` hash theo `parents[1]` = `altcoin/`, nên build vẫn `380dfca`. |
| `run.py` | `altcoin/run.py` | Phải ship cùng `bot/` để fingerprint không đổi. |
| `requirements.txt` | `altcoin/requirements.txt` | Bộ gọn cho Render. Đổi `ccxt>=4.4` thành `ccxt==4.5.90`. |
| `test_*.py` (6) | `altcoin/test_*.py` | Giữ chỗ để `from bot…` vẫn chạy. Chạy qua `suite_altcoin_scripts`. |
| `tools/*` (4) + `backtest_sessions.py` | `altcoin/tools/` | Chỉ dùng offline, không đóng gói. |
| `render.yaml` | `render.yaml` (gốc) | Blueprint của Render phải ở gốc repo (§14). |
| `botkeep.env` | `altcoin/botkeep.env` | Mẫu cho chế độ độc lập, không sửa. Cảnh báo hồ sơ rủi ro cao (`RISK_PER_TRADE=0.10`) ghi ở docs. |
| `check_db.py` | `altcoin/check_db.py` | Sửa để nạp `.env`/`bot.env` **cạnh nó** và ưu tiên `ALT_DATABASE_URL`. Nếu không, `load_dotenv()` sẽ đi ngược lên `.env` gốc của stock và kiểm nhầm DB stock. |
| `README.md` | `altcoin/README.md` | Thêm 5 dòng đầu trỏ tới docs. |
| `.env.example`, `.gitignore` | `altcoin/` | `.gitignore` lồng nhau là hợp lệ. |
| `dist/okx-ai-bot-v3.2.0.zip` | `altcoin/dist/` | Thêm `!altcoin/dist/` vào `.gitignore` gốc, nếu không quy tắc `dist/` sẽ chặn các zip phát hành sau này. |

---

## 3. Chiến lược git (giữ lịch sử)

Bản clone cục bộ là shallow (`8332222`), nên phải fetch thẳng từ GitHub. Nhánh mặc định của altcoin là `ccr-78e45817-g8v8rx` (8 commit). `main` là tổ tiên của nó và cùng cây file.

```bash
git switch -c merge/altcoin
git remote add altcoin https://github.com/traique/altcoin_bot.git
git fetch altcoin
git subtree add --prefix=altcoin altcoin ccr-78e45817-g8v8rx          # KHÔNG --squash
git subtree merge --prefix=altcoin altcoin/ccr-46f71b1d-mqcst3 \
    -m "altcoin: gộp nhánh chưa merge (tools/backtest_sessions.py)"  # đã thử: không xung đột
git mv altcoin/render.yaml render.yaml && git commit -m "Render: đưa blueprint altcoin lên gốc repo"
# mọi sửa nội dung để ở các commit SAU
```

- **Kiểm tra:**
  - `git log --oneline <commit-subtree-add>^2 | wc -l` = 8;
  - `git log --oneline <commit-subtree-merge>^2 | wc -l` = 10;
  - `git log <commit-subtree-add>^2 -- bot/main.py` thấy 2 commit (v3.1.0, v3.2.0).
- **Cách khác:** `git filter-repo --to-subdirectory-filter altcoin` cho lịch sử liền mạch, nhưng đổi mọi SHA và mất `subtree pull`. Không khuyến nghị.
- **Sau Giai đoạn 6:** đẩy một commit README "đã chuyển sang traique/stockvn" lên repo cũ, rồi **archive** repo đó, không xoá.

---

## 4. Bản đồ URL: hai mục tách biệt

**Nguyên tắc.** Stock ở `/`, giữ nguyên mọi URL. Altcoin ở **`/altcoin/`**, bắt buộc có `/` cuối vì dashboard altcoin dùng link tương đối. Không dùng `/bot` được vì stock đã có `/bot*`.

| Đường dẫn | Chủ | Đăng nhập | Ghi chú |
|---|---|---|---|
| `/`, `/taisan*`, `/ai`, `/thamdinh*`, `/chonbong`, `/tinhieu`, `/dulieu*`, `/bot*`, `/caidat*`, `/phienban`, `/jobs/*`, `/reports/*`, POST `/critic /analyze /quick /watchlist /channels/*` | Stock | Basic + same-origin | giữ nguyên |
| `/health` | Stock | công khai | Mã trạng thái chỉ phụ thuộc stock. JSON thêm `altcoin{state, reason, version, build, leader, mode, telegram, restarts}` và `rss_mb{stock, altcoin}`. |
| `/static/{favicon,logo}.png` | Stock | công khai | giữ nguyên |
| `/docs /privacy /terms /support /data-deletion /zoom /zoom/install /zoom/oauth/callback /zoom/pair /zoom/unpair`, POST `/webhook/zoom`, `/zoom/bot` | Stock | như hiện nay | App Zoom `vnbot` không đổi |
| `/altcoin` | Hub | công khai | 308 → `/altcoin/` |
| `/altcoin/`, `/altcoin/ai`, `/altcoin/caidat`, `/altcoin/phienban`, `/altcoin/zoom` | Altcoin | Basic | `/zoom` in URL Zoom đã có tiền tố |
| `/altcoin/api/state`, `/api/events`, `/api/scans`, `/api/export/{name}`, POST `/api/learn/{a}` | Altcoin | Basic (+ same-origin cho POST) | |
| POST `/altcoin/caidat/kenh/{key}`, `/caidat/zoomtest`, `/zoom/pair` | Altcoin | Basic + same-origin | |
| `/altcoin/login`, `/altcoin/logout` | Altcoin | Basic | vô hại khi cookie đang tắt |
| `/altcoin/docs /privacy /terms /support /data-deletion /zoom/install /zoom/oauth/callback` | Altcoin | công khai | app Zoom `okxbot` |
| POST `/altcoin/webhook/zoom`, `/altcoin/zoom/bot` | Altcoin | công khai, HMAC Zoom; miễn kiểm Origin | |
| `/altcoin/health` | Altcoin, hoặc Hub khi child chưa chạy | công khai | JSON gốc của altcoin, hoặc 503 `{"ok":false,"state":…}`. Đích của `PEER_URL` trên Render. |

Các route trùng tên: bản ở gốc thuộc stock, bản altcoin chuyển sang `/altcoin/<cùng tên>`.

### Chuẩn hoá đường dẫn ở Hub (bắt buộc)

- httpx tự chuẩn hoá dấu chấm, nên `/altcoin/health/../api/export/all.json` sẽ thành `/api/export/all.json`.
- Vì vậy Hub trả **400** với mọi đường dẫn thô (trước khi giải mã) có:
  - đoạn `.` hoặc `..`;
  - `//`;
  - `%2e` hoặc `%2f` (không phân biệt hoa thường);
  - `\`.
- Danh sách công khai so **chính xác** theo method + đường dẫn chuẩn.
- Hub chuyển tiếp đúng đường dẫn chuẩn đó.

### Thích nghi web trong `hosted.py` (không sửa `bot/`; `suite_altcoin_web` ghim)

1. `cfg.PUBLIC_URL` lấy `ALT_PUBLIC_URL`; nếu trống thì lấy `PUBLIC_URL` của stock + `/altcoin`. Giá trị này sửa được `redirect_uri` OAuth (install và callback), 10 URL Marketplace và link `notify.py:450`. Nếu có `ALT_ZOOM_CLIENT_ID` mà không suy ra được `PUBLIC_URL`: log ERROR và hiện cảnh báo ở `/altcoin/zoom`.
2. Bọc `pages.base_url`: khi `PUBLIC_URL` trống thì nối thêm `X-Forwarded-Prefix`.
3. Bọc `pages.page` và `ui.page`: `re.sub(r'\b(href|action|formaction)="/(?!/)', r'\1="/altcoin/', html)`. Các link `https:`, `mailto:`, `zoommtg:` không bị đụng.
4. Thay `pages.RedirectResponse` bằng lớp con thêm tiền tố vào `Location` bắt đầu bằng `/` (`pages.py:312, 325, 330`).
5. Chèn thanh chuyển mục ngay sau `<div class="top-in">` trong `ui.page`. Không chèn vào các trang công khai cho người duyệt Zoom.

### Lưới an toàn ở Hub: viết lại `Location` từ child

- Uvicorn của child tin `127.0.0.1` và nhận `X-Forwarded-Proto`, nên `redirect_slashes` có thể sinh `https://127.0.0.1:<port>/x`.
- Hub viết lại mọi URL tuyệt đối, **với bất kỳ scheme nào**, có netloc là `127.0.0.1:<port>` hoặc host công khai mà đường dẫn chưa có `/altcoin`, thành `/altcoin/x`.
- `/x` chưa có tiền tố cũng thành `/altcoin/x`.
- Link tương đối giữ nguyên.

### Thanh chuyển mục (hai phía, cùng markup)

`<nav class="sections" aria-label="Khu vực"><a href="/" aria-current="true">Cổ phiếu</a><a href="/altcoin/">Altcoin</a></nav>`

- **Phía stock:**
  - `ui.shell(..., sections="")`. Chỉ khi `ui.SECTIONS` khác rỗng mới chèn, và chỉ `main_botkeep` đặt giá trị này, nên harness test stock vẫn ra HTML như cũ.
  - Đặt giữa brand và `<nav aria-label="Điều hướng chính">` (ui.py:591–592), cộng một bản gọn trong `.topbar` (ui.py:594).
  - Dùng lại style `.seg`. Không dùng `<h1>`, dùng `aria-current="true"`. Nhờ vậy giữ được các assert ở `suite_taisan.py:427–433`, `suite_config.py:98`, `suite_store.py:334,345`, `suite_version.py:161–165`.
  - Nhãn Altcoin hiện trạng thái: "(đang khởi động)", "(tắt)", "(lỗi)".
- **Phía altcoin:** do `hosted.py` chèn.
- **Hai mục vẫn tách bạch:** brand riêng ("TradingAgents VN" / "OKX Altcoin Bot"), nav, pill phiên bản, footer, bảng màu, khoá theme (`ta-theme` / `okx-theme`).

### URL bên ngoài phải đổi (HOST = tên miền server stock)

| Ở đâu | Đổi thành |
|---|---|
| App Zoom `okxbot`: OAuth Redirect URL + Allow List | `https://HOST/altcoin/zoom/oauth/callback` |
| App Zoom `okxbot`: Bot endpoint (Dev+Prod), Event notification, Deauthorization | `https://HOST/altcoin/webhook/zoom` |
| App Zoom `okxbot`: Home, Direct landing, Documentation, Privacy, Terms, Support | `https://HOST/altcoin/docs`, `/altcoin/privacy`, `/altcoin/terms`, `/altcoin/support` |
| App Zoom `vnbot`, health check Botkeep, link Telegram stock | **không đổi** |
| Render: `PEER_URL` | `https://HOST/altcoin` (keepalive tự nối `/health`) |
| Render: `healthCheckPath` | giữ `/health` |
| Botkeep: `ALT_PEER_URL` | URL Render |
| Tên miền cũ `okx.bot-keep.xyz` | Tuỳ chọn `ALTCOIN_LEGACY_HOSTS`: chỉ GET/HEAD có đường dẫn **chưa** bắt đầu bằng `/altcoin` mới nhận 308 sang URL **tuyệt đối** `<gốc PUBLIC_URL>/altcoin<path>`, nên không lặp vòng. POST trả 410 kèm gợi ý. Zoom vẫn phải đăng ký lại. |

---

## 5. Đăng nhập và CSRF

**Khuyến nghị: một lần đăng nhập.** Basic `WEB_PASSWORD` được kiểm ở Hub trước khi proxy `/altcoin/*`. Đăng nhập cookie của altcoin vẫn còn trong code nhưng tắt (`DASHBOARD_*` bị xoá, trừ khi có `ALT_DASHBOARD_*`). Lý do:

- Altcoin hiện "mở toang" khi thiếu mật khẩu, kể cả `/api/export` và `/api/learn`.
- Altcoin so mật khẩu bằng `==` và không có throttle.

Cụ thể:

- **Throttle dùng chung.** Một `LoginThrottle` (sai 8 lần / 600 s thì khoá 300 s) dùng cho cả Hub và `create_app(..., throttle=…)`. Tham số mới mặc định tạo instance mới, nên test không đổi.
- **Danh sách công khai** (so chính xác trên đường dẫn chuẩn sau tiền tố):
  - GET/HEAD: `/health`, `/docs`, `/privacy`, `/terms`, `/support`, `/data-deletion`, `/zoom/install`, `/zoom/oauth/callback`.
  - POST: `/webhook/zoom`, `/zoom/bot`.
  - Mọi đường dẫn khác: `WEB_PASSWORD` trống → 503; đang bị khoá → 429 kèm `Retry-After`; sai hoặc thiếu → 401 `WWW-Authenticate: Basic`.
- **CSRF.**
  - Mọi method khác GET/HEAD tới `/altcoin/*` phải qua `same_origin_ok`. Thứ tự kiểm: Origin, `Sec-Fetch-Site`, Referer, so với Host, `X-Forwarded-Host` đầu tiên và host của `PUBLIC_URL`.
  - Chỉ miễn 2 webhook Zoom (đã có HMAC).
  - Che được cả trường hợp các app khác cùng `*.bot-keep.xyz`.
- **Vệ sinh proxy:**
  - Bỏ `Authorization`, `Proxy-Authorization` và header hop-by-hop.
  - Đặt lại `X-Forwarded-For/-Host/-Proto` (`-Proto` lấy scheme mà uvicorn của stock đã xác định), thêm `X-Forwarded-Prefix: /altcoin` và `X-Hub-Token`.
  - `setdefault` `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin`.
  - Body ≤ 64 KB (vượt thì 413). Stream phản hồi bằng `aiter_raw`.
  - httpx dùng `trust_env=False`, connect 2 s, read 120 s, ≤ 8 request đồng thời (vượt thì 503).
- **Chống vòng qua localhost.** Middleware `X-Hub-Token` trong child.
- **Đối chiếu.** Cùng một bộ ca auth và CSRF chạy với cả Hub lẫn `create_app`, kết quả phải giống nhau.
- **Chưa kiểm:** có bị hỏi mật khẩu lần hai trên Chrome và Safari không (tệ nhất là hỏi lại cùng mật khẩu); proxy Botkeep có gộp IP client không (nếu có, một lần bị khoá sẽ khoá cả hai mục; đặt `FORWARDED_ALLOW_IPS`).

---

## 6. Tách biến môi trường / cấu hình

**Env của child được dựng riêng:**

- Allowlist hệ thống: `PATH HOME LANG LC_* TZ TMPDIR PYTHONPATH PYTHONHOME PYTHONUSERBASE PYTHONNOUSERSITE VIRTUAL_ENV LD_LIBRARY_PATH HTTP(S)_PROXY http(s)_proxy NO_PROXY REQUESTS_CA_BUNDLE SSL_CERT_FILE SSL_CERT_DIR`.
- Luôn đặt `PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1`, `HUB_TOKEN`.
- Mỗi `ALT_<TÊN>` của cha được đổi thành `<TÊN>`.
- Biến stock **không bao giờ** được chuyển, trừ các fallback ghi trong bảng.
- Giai đoạn 0 liệt kê **tên** biến env của tiến trình trên Botkeep để bổ sung allowlist nếu thiếu.

**Khoá chỉ supervisor đọc, không chuyển:** `ALT_MODE`, `ALT_I_UNDERSTAND_THE_RISK`, `ALT_LIVE_ACK`, `ALT_PEER_MODE`, `ALT_DATA_DIR`, `ALT_PUBLIC_URL`, `ALT_INSTANCE_NAME`. Ba khoá cuối được niêm phong qua argv.

**Khoá cấp host** (tiền tố `ALTCOIN_`, không bao giờ chuyển): `ALTCOIN_ENABLED`, `ALTCOIN_AUTO_INSTALL`, `ALTCOIN_FRESH_START`, `ALTCOIN_LEGACY_HOSTS`.

Child chỉ đọc `altcoin/.env` và `altcoin/bot.env`. Có thể chép `bot.env` cũ vào `altcoin/bot.env` mà không đổi tên biến, theo thứ tự ưu tiên ở bảng dưới.

**Bí mật trên panel cũ không chép được.** Panel hiển thị dạng `__BOTKEEP_KEEP_SECRET_<TÊN>__`, nên phải nhập lại từ nguồn: BotFather, OKX, Zoom Marketplace, Supabase.

**20 biến hai repo cùng đọc:**

| Biến (stock giữ nguyên) | Tên cho altcoin trên panel | Child nhận | Lấy giá trị stock khi trống? |
|---|---|---|---|
| `TELEGRAM_TOKEN` | `ALT_TELEGRAM_TOKEN` | `TELEGRAM_TOKEN` | **Không bao giờ.** Trùng sha (đã `strip`) với stock thì chỉ gửi, không poll. |
| `DATABASE_URL` | `ALT_DATABASE_URL` | `DATABASE_URL` (trống thì dùng SQLite) | **Không bao giờ.** Trùng URL hoặc DB có bảng stock thì thoát 78. |
| `DATA_DIR` | `ALT_DATA_DIR` | `DATA_DIR` tuyệt đối, niêm phong | mặc định `<DATA_DIR stock>/altcoin` |
| `PORT` | — | cổng tạm nội bộ, niêm phong | — |
| `SERVER_PORT`, `APP_PORT` | — | bị xoá (`DASHBOARD_PORT` xoá; `DASHBOARD_HOST=127.0.0.1`) | — |
| `PUBLIC_URL` | `ALT_PUBLIC_URL` (ví dụ `https://HOST/altcoin`) | niêm phong | `PUBLIC_URL` stock + `/altcoin` |
| `SUPPORT_EMAIL` | `ALT_SUPPORT_EMAIL` | `SUPPORT_EMAIL` | có |
| `OPENROUTER_API_KEY` | `ALT_OPENROUTER_API_KEY` | `OPENROUTER_API_KEY` | có (ghi log: dùng chung hạn mức free) |
| `ZOOM_WEBHOOK_URL`, `ZOOM_WEBHOOK_TOKEN`, `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`, `ZOOM_BOT_JID`, `ZOOM_SECRET_TOKEN`, `ZOOM_BOT_SECRET_TOKEN`, `ZOOM_VERIFICATION_TOKEN`, `ZOOM_ACCOUNT_ID`, `ZOOM_TO_JID`, `ZOOM_ALLOWED_USERS` (11) | `ALT_ZOOM_*` | `ZOOM_*` | **không bao giờ** (app riêng) |

**Biến chỉ altcoin có, hoặc cùng mục đích nhưng khác tên:**

| Altcoin độc lập | Tên trên panel (gộp) | Mặc định / fallback |
|---|---|---|
| `MODE` | `ALT_MODE` (chỉ supervisor đọc) | `paper` |
| `I_UNDERSTAND_THE_RISK` | `ALT_I_UNDERSTAND_THE_RISK` (chỉ supervisor đọc; `hosted.py` tự đặt) | — |
| (mới) | `ALT_LIVE_ACK` = tên instance; `ALT_PEER_MODE=live` | — |
| `OKX_API_KEY`, `OKX_API_SECRET`, `OKX_PASSPHRASE`, `OKX_DEMO` | `ALT_OKX_*` (live chỉ nhận từ panel) | trống |
| `TELEGRAM_CHAT_ID` | `ALT_TELEGRAM_CHAT_ID` | trống |
| `TIMEZONE`, `LOG_LEVEL` | `ALT_TIMEZONE`, `ALT_LOG_LEVEL` | `Asia/Ho_Chi_Minh`, `INFO` |
| `INSTANCE_NAME` | `ALT_INSTANCE_NAME` (niêm phong) | `botkeep`; cut-over dùng `crypto` |
| `PEER_URL`, `SELF_URL`, `LEADER_TTL`, `KEEPALIVE_INTERVAL` | `ALT_*` | như cũ (90 s, 240 s) |
| `NINEROUTER_BASE_URL`, `NINEROUTER_API_KEY` | `ALT_NINEROUTER_*` | `ROUTER9_BASE_URL`, `ROUTER9_API_KEY` |
| `NINEROUTER_MODELS` | `ALT_NINEROUTER_MODELS` | `auto` |
| `CUSTOM_*` | `ALT_CUSTOM_*` | không có fallback |
| `DASHBOARD_USER`, `DASHBOARD_PASS` | `ALT_DASHBOARD_*` | trống, tức đăng nhập chung |
| Khoảng 100 tham số chiến lược / AI / rủi ro / học | `ALT_<TÊN>` hoặc để trần trong `altcoin/bot.env` | mặc định trong code |
| (host) | `ALTCOIN_ENABLED` / `ALTCOIN_AUTO_INSTALL` / `ALTCOIN_FRESH_START` / `ALTCOIN_LEGACY_HOSTS` | `0` / `0` / `0` / trống |

**Thứ tự ưu tiên khi có `altcoin/bot.env`:**

| Khoá | Giá trị thắng | Ghi chú |
|---|---|---|
| `INSTANCE_NAME`, `PUBLIC_URL`, `DATA_DIR`, `PORT`/`DASHBOARD_*PORT/HOST` | Niêm phong (argv) | `bot.env` khác thì WARNING |
| `MODE`, `I_UNDERSTAND_THE_RISK` | Supervisor (argv) | `bot.env` khác thì ERROR |
| `OKX_*` | live: panel `ALT_OKX_*`; paper: bị xoá | Key chỉ có trong `bot.env` thì vẫn chạy paper (ERROR) |
| `DASHBOARD_USER/PASS` | `ALT_DASHBOARD_*`, nếu không có thì xoá | Giữ "một lần đăng nhập" |
| `TELEGRAM_TOKEN`, `DATABASE_URL`, `PEER_URL` | Panel `ALT_*`, nếu không có thì `bot.env` | Mọi kiểm tra ở §1 bước 5 chạy trên giá trị hiệu lực |
| Tham số còn lại | `bot.env` (file-wins như altcoin gốc) | |

**Bảy lớp khoá chống vô tình chạy `MODE=live`:**

1. **Supervisor** chỉ truyền `--mode live` khi đúng **tất cả** các điều kiện sau trong env thật của tiến trình cha:
   - `ALT_MODE=live`;
   - `ALT_I_UNDERSTAND_THE_RISK=yes`;
   - `ALT_LIVE_ACK` bằng `ALT_INSTANCE_NAME`;
   - đủ ba `ALT_OKX_API_KEY/SECRET/PASSPHRASE`;
   - `ALT_PEER_URL` trống, **hoặc** `ALT_PEER_MODE=live` (peer cũng đang live với key riêng).

   Sai bất kỳ điều kiện nào thì chạy paper, kèm ERROR và dòng đỏ trên `/altcoin/`.
2. **`hosted.py` ghi đè `cfg.MODE`**, chạy lại hai kiểm tra của `main()` và thoát 78 nếu sai.
3. **Paper:** xoá `cfg.OKX_*` và `I_UNDERSTAND_THE_RISK`, nên không gọi được endpoint riêng tư nào.
4. **Peer:** từ chối live (78) nếu `heartbeats` có instance khác đang `paper=True` trong `LEADER_TTL`. Lý do: `PaperEngine.monitor()` sẽ "đóng" các lệnh thật trong DB.
5. **Web không đổi được chế độ:** `config.FIELDS` không có `ALT_*` hay `MODE` (có test kiểm). `MODE` trần trên panel bị bỏ và chỉ cảnh báo.
6. **Gói không mang cấu hình live:** bộ đóng gói từ chối `.env`, `bot.env`, `*.env`.
7. **Test ma trận** trong `suite_altcoin_host`, chạy tới ngay trước `Bot()` (stub). Các ca phải ra paper hoặc disabled:
   - `bot.env` có `MODE=live` + xác nhận + key;
   - thiếu xác nhận;
   - thiếu `ALT_LIVE_ACK`;
   - SQLite + peer (disabled);
   - peer mà không có `ALT_PEER_MODE`;
   - key chỉ nằm trong `bot.env`.

   Đủ mọi điều kiện, kể cả khi `bot.env` có `I_UNDERSTAND_THE_RISK=no`: chọn `LiveEngine` và qua cả hai kiểm tra của `main()`.

**Chỉ bật live sau mục "vùng găng" ở Giai đoạn 7**, vì SIGTERM có thể rơi vào giữa lúc đặt lệnh và lúc ghi DB.

---

## 7. Telegram và Zoom

### Telegram: hai bot (không cần sửa `bot/`)

- Stock giữ `TELEGRAM_TOKEN` và khoảng 59 lệnh.
- Altcoin dùng `ALT_TELEGRAM_TOKEN` + `ALT_TELEGRAM_CHAT_ID` (token bot OKX hiện có), giữ đủ các lệnh: `status pause resume trades close signals ai learn apply lessons btc phienban/version zoomtest zoompair zoomxoa zoomstatus`.
- **Chặn 409 trong cùng host:** token trùng stock (so sha sau `strip`) thì vá `Telegram.start` thành no-op. Altcoin **vẫn gửi** thông báo lệnh, báo cáo ngày, circuit breaker, đổi leader, nhưng không poll. Stock nghỉ 30 s khi gặp 409, còn altcoin poll lại ngay, nên nếu để hai bên cùng poll thì stock sẽ "điếc".
- **Giữa các host:** chỉ leader mới poll. SQLite + peer đã bị chặn (§1).
- **Tài liệu:** đừng thêm cả hai bot vào một group rồi gõ `/status` trần. Dùng chat riêng hoặc `/status@<bot>`.
- **Bác bỏ lúc này:** một bot định tuyến `/alt …`. Phải sửa cả hai lớp lệnh, và mất lệnh khi failover sang Render.

### Zoom: hai app

- App `vnbot` giữ URL gốc, trạng thái lưu ở `data/zoom_state.json`.
- App `okxbot` đăng ký lại dưới `/altcoin/…`, trạng thái lưu trong kv `zoom_*` của DB altcoin. Mỗi app kiểm HMAC bằng bí mật riêng.
- Kiểu "mượn app OKX chỉ để gửi" của stock (`.env.example:178–181`) vẫn chạy.
- **Thứ tự (sau khi child mới đã chạy, §15 Giai đoạn 5):**
  1. đặt `ALT_ZOOM_*`;
  2. mở `/altcoin/zoom`;
  3. dán URL vào Marketplace;
  4. bấm Validate;
  5. gửi thử qua `/altcoin/caidat/zoomtest`.

  Zoom altcoin gián đoạn vài phút là chấp nhận được. App đã publish có thể phải duyệt lại (chưa kiểm).

---

## 8. Lưu trữ

**Luôn tách store, không đổi SQL.**

- SQLite: stock ở `data/bot.db`, altcoin ở `data/altcoin/bot.db` (tuyệt đối, niêm phong).
- Postgres: altcoin dùng `ALT_DATABASE_URL` riêng, không bao giờ fallback sang URL của stock.
- Chỉ so chuỗi URL là không đủ: endpoint direct `db.<ref>.supabase.co` và pooler `aws-0-*.pooler.supabase.com:5432/6543` (user `postgres.<ref>`) cùng trỏ một DB. Vì vậy `hosted.py` kiểm thêm theo bảng (§1 bước 5).
- `os._exit(3)` sau 8 lỗi DB nay chỉ giết child.
- Bảng `market`/`fills` của stock và `trades`/`locks`/`learn_log` của altcoin dùng để nhận diện DB của bên nào.

| Bảng | Stock (`data/bot.db` / `DATABASE_URL`) | Altcoin (`data/altcoin/bot.db` / `ALT_DATABASE_URL`) | Nếu lỡ dùng chung 1 DB | Sau gộp |
|---|---|---|---|---|
| `kv` | (k, v, ts), PK k | (k, v), PK k; khoảng 29 khoá | ít xung đột | tách |
| `signals` | id + 44 cột, UNIQUE(day, symbol, source) | ts, symbol, side, score, ai_*, sid, outcome… | `ALTER ADD` ghép cột chéo; `log_signal` hỏng | tách |
| `equity` | (day, book, nav, …, ts) | (ts, equity, mode); INSERT theo vị trí | INSERT lỗi; `prune()` altcoin xoá NAV stock cũ hơn 90 ngày (stock giữ 730) | tách |
| `scans` | id, day, kind, …, ts | (ts, scanned, passed, reasons, near, top) | INSERT lỗi; `prune()` xoá scan stock cũ hơn 30 ngày | tách |
| `events` | (id, ts, level, src, msg) | (ts, level, src, msg) | INSERT lỗi → `os._exit(3)` | tách |
| `market`, `fills` | chỉ stock | — | dấu hiệu "DB stock" | giữ |
| `trades`, `reviews`, `locks`, `heartbeats`, `learn_log` | — | chỉ altcoin | dấu hiệu "DB altcoin" | giữ |
| Index `ix_events_ts` | có | có | trùng tên; `CREATE INDEX IF NOT EXISTS` lặng lẽ bỏ qua | tách |

### Chuyển dữ liệu altcoin

Luôn **dừng server altcoin cũ trước**. Dừng êm thì nó nhả lock.

- **Đang dùng Supabase:** đặt `ALT_DATABASE_URL` = đúng URL Session pooler đó. Leader lock, `paper_balance`, `learned`, trades và cặp Zoom đều đi theo. Lưu ý: không có cơ chế ưu tiên leader. Nếu Render đã tiếp quản thì nó giữ lock; muốn Botkeep làm leader thì tạm dừng Render lâu hơn `LEADER_TTL`.
- **Đang dùng SQLite trên server Botkeep cũ:**
  1. dừng server cũ;
  2. tải `data/bot.db` (cùng `-journal` nếu có);
  3. deploy bản gộp (`ALTCOIN_ENABLED` vẫn 0);
  4. tải file lên `data/altcoin/bot.db`;
  5. đặt `ALT_INSTANCE_NAME=crypto`, `ALTCOIN_ENABLED=1`, rồi khởi động lại.

  Nếu File Manager giới hạn 4 MB thì chạy `VACUUM` trước, hoặc đẩy lên Postgres.
- **Chặn DB rỗng ngay trong code** (§1 bước 5). Muốn bắt đầu mới hoàn toàn thì đặt `ALTCOIN_FRESH_START=1` cho lần chạy đầu.
- **Giai đoạn 0, chỉ đọc, chạy cục bộ:** liệt kê bảng của từng URL. `check_db.py` không làm được việc này, vì nó tạo rồi xoá bảng ping.

  ```bash
  python -c "import psycopg,sys;c=psycopg.connect(sys.argv[1]);print([r[0] for r in c.execute(\"select table_name from information_schema.tables where table_schema='public' order by 1\")])" "$URL"
  ```

  DB stock đã có `trades`/`locks`/`learn_log` nghĩa là hai bot **đang** dùng chung DB, và phải tách trước mọi việc khác.
- **Gộp về một Supabase** (schema riêng): không làm, ít nhất tới khi `_columns()` của stock (`store.py:256`) lọc theo schema.

---

## 9. Tách log và eventlog

- Hai tiến trình có root logger, `basicConfig`, `Formatter.converter`, `RedactFilter` và handler DB riêng.
  - `events` của altcoin nằm trong DB altcoin và xem ở `/altcoin/`.
  - `events` của stock (`/dulieu`) không có dòng nào của altcoin.
- **stdout và stderr của child** (gồm traceback và lỗi thread) đi qua cùng một pipe. Thread `altcoin-log` giải mã utf-8 với `errors="replace"`, cho qua `vnbot.eventlog.redact()`, rồi in `[altcoin] …` ra `sys.stdout`. Không đi qua `logging`, nên không lọt vào `events` của stock. Giữ 200 dòng cuối; trang 503 hiện các dòng này sau khi đăng nhập.
- **Sự kiện supervisor** (spawn, mã thoát, backoff, watchdog kill, khoá bị bỏ) ghi qua logger `vnbot.altcoin_host`, hiện ở `/dulieu`.
- **Che bí mật ở phía cha:**
  - `SECRET_KEYS` thêm: `ALT_TELEGRAM_TOKEN`, `ALT_DATABASE_URL`, `ALT_OKX_API_KEY`, `ALT_OKX_API_SECRET`, `ALT_OKX_PASSPHRASE`, `ALT_ZOOM_CLIENT_SECRET`, `ALT_ZOOM_SECRET_TOKEN`, `ALT_ZOOM_BOT_SECRET_TOKEN`, `ALT_ZOOM_WEBHOOK_TOKEN`, `ALT_ZOOM_VERIFICATION_TOKEN`, `ALT_DASHBOARD_PASS`, `ALT_OPENROUTER_API_KEY`, `ALT_NINEROUTER_API_KEY`, `ALT_CUSTOM_API_KEY`.
  - Tách mật khẩu khỏi `ALT_DATABASE_URL`.
  - Đọc `dotenv_values()` của `altcoin/.env` và `altcoin/bot.env`, rồi thêm các giá trị có tên chứa `TOKEN|SECRET|KEY|PASS` (và mật khẩu trong `DATABASE_URL`) vào danh sách che, thông qua hàm mới `eventlog.add_secret_values()`.

---

## 10. Phụ thuộc và ngân sách RAM / đĩa trên Botkeep

- **Python ≥ 3.12** cho stock và bản gộp. Code altcoin trên Render vẫn chạy 3.11.9.
- **`requirements.txt` gốc** thêm:
  - `ccxt==4.5.90`, ghim đúng bản đã resolve và đo. ccxt ghim cứng khoảng 17 gói (`urllib3==2.8.0`, `idna==3.18`, `typing_extensions==4.16.0`, `charset_normalizer==3.4.7`, `uvloop==0.22.1`…). Hệ quả là **hạ** `idna` (3.20 → 3.18) và `charset_normalizer` (3.5.2 → 3.4.7) mà stock đang dùng, nên phải chạy đủ `run_all.py`.
  - `httpx>=0.27` (stock đã import mà chưa khai báo).
  - Mỗi lần nâng ccxt là một thay đổi ảnh hưởng stock: chạy `pip install --dry-run -r requirements.txt`, ghi lại phiên bản `idna`/`urllib3`/`typing_extensions`/`charset_normalizer`, rồi `run_all.py`.
- **`altcoin/requirements.txt`** cũng ghim `ccxt==4.5.90`, để Render và Botkeep chạy cùng ccxt.
- **Event loop:** ccxt kéo theo `uvloop`, nên stock chạy `uvicorn.run(..., loop="asyncio")`. Tiến trình stock không import ccxt.
- **`oom_score_adj=800`** cho child, để khi hết RAM kernel giết altcoin trước stock.

| Tài nguyên | Hiện nay | Sau gộp | Căn cứ |
|---|---|---|---|
| RAM nhàn rỗi | stock 157 MB | stock 157 + altcoin 154–156 + proxy ~3 ≈ **315 MB / 2 GB** | đo |
| RAM đỉnh | stock khi `/phantich`: chưa đo | đo ở Giai đoạn 3 khi child đang chạy; chấp nhận nếu tổng < 1,2 GB | — |
| Đĩa site-packages | 322 MB | + ccxt và phụ thuộc ≈ 102–123 MB → **≈ 445 MB / 2 GB** + `data/` | đo; README ghi gói free có 2 GB đĩa; đo lại `df` ở Giai đoạn 0 |
| Khởi động | stock sẵn sàng ~1,6 s | child bắt đầu **sau** stock; thêm 2–5 s | đo |

Thay ccxt bằng một client OKX tự viết (khoảng 17 endpoint): **không khuyến nghị**. Không giúp gì cho giới hạn upload, và đường đặt lệnh live chưa có test.

---

## 11. Gói Botkeep: `scripts/build_botkeep_bundle.py` (mới) và test

**Danh sách cho phép, ngoài ra không có gì:**

- `main_botkeep.py`, `requirements.txt`, `.env.example`, `README.md`, `LICENSE`
- `tradingagents/**/*.py`
- `tradingagents/assets/tauric-logo.svg` (`report_html.py` đọc file này lúc import)
- `vnbot/**/*.py`
- `vnbot/static/favicon.png`, `vnbot/static/logo.png`
- `altcoin/bot/**/*.py`
- `altcoin/run.py` (bắt buộc cho fingerprint)
- `altcoin/hosted.py`, `altcoin/requirements.txt`, `altcoin/.env.example`

**Kiểm tra cứng (sai thì thoát 1, kèm bảng file vi phạm):**

0. Từ chối chạy trên Python < 3.12. Lý do: biên dịch bằng 3.11 thì hỏng, còn biên dịch bằng 3.13 lại che mất lỗi mà Botkeep 3.11 sẽ gặp.
1. **Cấm:** `.env`, `*.env`, `*.db*`, `data/`, `__pycache__/`, `*.pyc`, `tests/`, `docs/`, `altcoin/test_*`, `altcoin/tools/`, `altcoin/dist/`, `dist/`, `.git*`. Nguồn là `git ls-files`; không có `.git` thì duyệt thư mục với cùng luật (27 file `.pyc` bị ignore đang nằm trong cây làm việc).
2. Số file ≤ 250 **và** số file + số thư mục ≤ 250.
3. Tổng giải nén ≤ 4.000.000 B.
4. Zip ≤ 4.000.000 B. Zip phẳng, mặc định **không** ghi mục thư mục, tên sắp xếp, timestamp cố định, deflate 9, nên tái lập được. Cờ `--dir-entries` để dự phòng (185 mục, vẫn < 250).
5. **Đủ file:** mọi `.py` mà git theo dõi dưới 3 package đều có; đủ 3 file ảnh/svg bắt buộc.
6. `compile()` từng `.py` trong bộ nhớ.
7. **Fingerprint** trên bản dàn dựng, với `PYTHONDONTWRITEBYTECODE=1`:
   - `python -I -c "import sys; sys.path.insert(0, STAGE); import vnbot.version as v; print(v.build())"`
   - và lệnh tương tự với `STAGE/altcoin` cho `bot.version`.

   `-I` bỏ cwd khỏi `sys.path`, nên phải chèn đường dẫn. Cả hai phải bằng `RELEASE_BUILD`. Cây làm việc bẩn trên đường dẫn được phép thì lỗi, trừ khi có `--allow-dirty`.
8. Cảnh báo khi vượt 85 % bất kỳ giới hạn nào.

**Kết quả:** `dist/botkeep/`, `dist/stockvn-botkeep-v1.15.0-alt3.2.0.zip`, và `dist/botkeep.json` (số file, số thư mục, số byte, khoảng trống, hai build) ghi **ngoài** bản dàn dựng.

**Cờ:** `--check`, `--out`, `--max-files`, `--max-bytes`, `--dir-entries`, `--allow-dirty`.

| Tập | File | Thư mục | Giải nén (B) | Zip (B) | Còn trống (250 / 4.000.000 B) |
|---|---|---|---|---|---|
| Gói stock hiện nay (đo) | 137 | 18 | 2.130.211 | 775.457 | 113 file (95 nếu tính thư mục) / 1.869.789 B |
| Phần altcoin thêm (đo): `bot/` 21 + `run.py` + `requirements.txt` + `.env.example` | +24 | +2 | +362.251 | — | — |
| Nguyên mẫu gộp (đo) | 161 | 20 | 2.492.462 | 899.730 (910.832 nếu ghi 20 mục thư mục) | 89 (69) / 1.507.538 B |
| **Sau gộp** (+ `hosted.py`, `hub.py`, `altcoin_host.py`, `webguard.py`, phần sửa; ước tính) | **≈165** | 20 | **≈2.535.000** | **≈915.000** | **≈85 (≈65) / ≈1,46 MB** |
| Deploy cả repo từ GitHub (ước tính: 183 + 41 + ≈13 file mới) | ≈237 | ≈29 | ≈3.320.000 | ≈1.250.000 | 13 file; tính cả thư mục ≈266 > 250 → **không khả thi** theo cách hiểu chặt |

**Đòn bẩy dự phòng:** bỏ `README.md` khỏi gói (93 KB); bỏ `vnbot/static/logo.png` (86 KB, không HTML nào dùng, nhưng bỏ thì phải sửa `ui.py` và re-stamp).

**`tests/suites/suite_bundle.py`:**

1. Assert `MAX_FILES == 250` và `MAX_BYTES == 4_000_000`.
2. Build vào thư mục tạm. Assert mọi giới hạn ở mục 2–5, không có mục thư mục, không có tên bị cấm, đủ file bắt buộc. In khoảng trống còn lại.
3. **Mồi nhử:** chép thêm `altcoin/bot.env`, `.env`, `vnbot/__pycache__/x.pyc`, `data/bot.db`. Không file nào trong số đó được vào gói.
4. `--max-files 10` và `--max-bytes 1000` phải thoát 1. Danh sách giả 251 file, hoặc tổng 4.000.001 B, phải lỗi.
5. Hai fingerprint đúng bản phát hành (quên `--stamp` thì đỏ).
6. Cảnh báo khi số file git theo dõi > 240.

---

## 12. Test

- **`pytest.ini`:** `testpaths = tests`, `python_files = test_suites.py`. Như vậy pytest không thu thập các script altcoin chạy assert ở cấp module.
- **`suite_altcoin_scripts.py`:**
  - Chạy `test_engine`, `test_learner`, `test_risk`, `test_strategy`, `test_web`.
  - Chép `altcoin/bot` và file test vào thư mục tạm, để không có `.env`/`bot.env` và không chạm DB thật.
  - Mỗi file chạy trong một subprocess (timeout 300 s): `python -c "import sys,runpy; sys.path[:0]=[SUITES,TMP]; import _common; import dotenv; dotenv.dotenv_values=lambda *a,**k:{}; runpy.run_path(TEST, run_name='__main__')"`, env sạch, chặn mạng, cwd tạm.
  - `test_smoke.py` cần OKX thật, nên chạy tay: `cd altcoin && python test_smoke.py`.
- **`suite_altcoin_host.py`:**
  - ánh xạ env, allowlist, `PYTHONIOENCODING`;
  - bảng ưu tiên với `bot.env`;
  - ma trận live (§6) chạy tới `Bot()` stub;
  - Telegram trùng token: `send()` vẫn gửi, `start()` không poll (thêm ca có khoảng trắng thừa);
  - chặn DB trùng: theo URL, theo bảng `market`/`fills`;
  - chặn DB rỗng, và `ALTCOIN_FRESH_START`;
  - trùng tên instance khác host → 75;
  - lọc `__BOTKEEP_KEEP_SECRET_` kèm cảnh báo;
  - supervisor với child giả: mã 78/75/-9, backoff, quá hạn sẵn sàng thì spawn lại với cổng mới, `stop()` qua SIGTERM thật;
  - `/health` trả lời < 100 ms trong lúc `stop()` đang chạy;
  - spawn `hosted.py --check` thật từ cwd `/`, có một `.env` gốc mồi nhử: thoát 0 và không đọc file mồi;
  - child ở `TZ=Asia/Ho_Chi_Minh` khi cha ở `TZ=UTC`, và fallback tzdata;
  - che `ALT_ZOOM_BOT_SECRET_TOKEN` và giá trị trong `bot.env`;
  - không khoá `ALT_` nào nằm trong `config.FIELDS`.
- **`suite_altcoin_proxy.py`:** Hub + child ASGI giả trên cổng localhost thật.
  - danh sách công khai so chính xác;
  - 401, 429 (throttle chung), 503 khi thiếu `WEB_PASSWORD`;
  - CSRF 403 với Origin lạ, `Origin: null`, Referer chéo site; webhook Zoom được miễn;
  - **400** với `/altcoin/health/../api/export/all.json`, `POST /altcoin/webhook/zoom/../../api/learn/reset`, `%2e%2e`, `%2F`, `//`;
  - 413 khi body > 64 KB;
  - không chuyển tiếp `Authorization`; có `X-Hub-Token`;
  - 308 `/altcoin` → `/altcoin/`;
  - viết lại `Location` cho cả `http`/`https://127.0.0.1:<port>` và host công khai; ca `/altcoin/caidat/` với `X-Forwarded-Proto: https`;
  - legacy host: GET → 308 tuyệt đối, không lặp vòng; POST → 410;
  - stream 5 MB;
  - 503 khi child chết;
  - route stock chạy khi `LateApp` đang khởi động;
  - cùng bộ ca auth chạy với Hub và `create_app`, kết quả giống nhau.
- **`suite_altcoin_web.py`:** `build_app` thật với bot giả, sau `hosted.adapt("/altcoin", …)`.
  - không còn `href`/`action`/`formaction` tuyệt đối thiếu tiền tố trên cả 11 trang;
  - `Location` của `/zoom`, `/zoom/pair` có tiền tố;
  - `redirect_uri` chứa `/altcoin/zoom/oauth/callback`;
  - có thanh chuyển mục;
  - middleware token chặn request thiếu header;
  - tự kiểm báo lỗi rõ khi một seam biến mất;
  - end-to-end tuỳ chọn: spawn child thật (skip 77 nếu thiếu ccxt), `/altcoin/health` báo build `380dfca`.
- **Suite stock:**
  - thêm assert: khi `ui.SECTIONS` được đặt thì có đúng một `<h1` và nav "Điều hướng chính" không đổi;
  - `suite_security_web` và `suite_web` phải xanh **không sửa** sau refactor `webguard`.
- **Mong đợi:** `python tests/run_all.py -j 4` → **27/27** (22 + 5). `pytest -q` chỉ thu thập `tests/test_suites.py`.
- **Skill dùng khi làm:**
  - `test-driven-development` cho `hub.py`, `altcoin_host.py`, `hosted.py`, `webguard.py`;
  - `code-review` cho mỗi PR;
  - `security-review` cho Giai đoạn 3 và 4.

---

## 13. Tài liệu

- **`README.md` gốc:**
  - Mục mới ở đầu: "Hai phần: Cổ phiếu (`/`) và Altcoin (`/altcoin/`), một server Botkeep, một gói zip".
  - §2 trỏ tới `python scripts/build_botkeep_bundle.py` với số thật (≈165 file, ≈2,5 MB, zip ≈0,92 MB).
  - Python **3.12+** (dòng 27, 39, 562).
  - Bỏ link `trading_vn.git`.
  - "hơn 370 kiểm tra" → "hơn 1.000".
  - `deploy/botkeep/.env.example` → `.env.example`, sửa cả trong docstring `main_botkeep.py` và `vnbot/__init__.py`.
- **`docs/ALTCOIN_TRONG_STOCKVN.md`:**
  - quy tắc `ALT_*` / `ALTCOIN_*` và các bảng ở §6, gồm bảng thứ tự ưu tiên;
  - chép `bot.env`;
  - nhập lại bí mật từ nguồn, không chép từ panel bị che;
  - **đừng** dán panel altcoin cũ (không tiền tố) vào panel stock, vì sẽ đè `TELEGRAM_TOKEN`/`DATABASE_URL` của stock;
  - live gate, và quy tắc "chỉ bật live sau Giai đoạn 7";
  - chuyển DB; lệnh liệt kê bảng;
  - 10 URL Zoom;
  - Render failover: không có ưu tiên leader; live phải đi kèm `ALT_PEER_MODE`;
  - `ALTCOIN_ENABLED`, `ALTCOIN_AUTO_INSTALL`, `ALTCOIN_FRESH_START`;
  - rollback;
  - cảnh báo hồ sơ rủi ro cao của `botkeep.env`;
  - hộp "Cập nhật bot" ở `/altcoin/phienban` mô tả chế độ độc lập (tải zip altcoin, `data/`), không đúng cho chế độ hosted;
  - các hành vi của `run.py` không có trong chế độ hosted: tự cài thư viện (trừ khi `ALTCOIN_AUTO_INSTALL=1`), dọn pip cache và `__pycache__`, log dung lượng đĩa. Trên Render/VPS chúng vẫn chạy;
  - mỗi bản phát hành chỉ của stock cũng làm altcoin restart.
- **`.env.example`:** thêm mục `# ===== ALTCOIN =====` gồm `ALTCOIN_ENABLED=0`, `ALTCOIN_AUTO_INSTALL`, `ALTCOIN_FRESH_START`, `ALTCOIN_LEGACY_HOSTS`, `ALT_MODE=paper`, `ALT_I_UNDERSTAND_THE_RISK`, `ALT_LIVE_ACK`, `ALT_PEER_MODE`, `ALT_TELEGRAM_TOKEN`, `ALT_TELEGRAM_CHAT_ID`, `ALT_DATABASE_URL`, `ALT_DATA_DIR`, `ALT_INSTANCE_NAME`, `ALT_PEER_URL`, `ALT_OKX_*`, `ALT_ZOOM_*`, `ALT_NINEROUTER_*`, `ALT_PUBLIC_URL`.
- **Phía altcoin:**
  - `altcoin/README.md` thêm 5 dòng đầu;
  - `altcoin/check_db.py` nạp `.env` cạnh nó;
  - `.env.example` và `botkeep.env` giữ nguyên cho chế độ độc lập.
  - Tất cả nằm ngoài fingerprint.
- **Giấy phép:** chờ anh/chị quyết (§17).

---

## 14. Render failover cho altcoin: giữ lại

```yaml
services:
  - type: web
    name: okx-ai-bot
    runtime: python
    plan: free
    rootDir: altcoin
    buildCommand: pip install -r requirements.txt       # bộ gọn, ccxt==4.5.90
    startCommand: python run.py                         # altcoin độc lập ở gốc URL Render
    healthCheckPath: /health
    buildFilter: { paths: ["altcoin/**"] }              # commit chỉ đụng stock không redeploy Render
    envVars: (giữ nguyên: PYTHON_VERSION=3.11.9, INSTANCE_NAME=render, MODE=paper, DATABASE_URL, PEER_URL, TELEGRAM_*, …)
```

- **Render:** nối lại sang `traique/stockvn`, đặt `PEER_URL=https://HOST/altcoin`.
- **Botkeep:**
  - `ALT_DATABASE_URL` = **cùng Supabase** với Render (SQLite + peer bị chặn);
  - `ALT_INSTANCE_NAME=crypto`;
  - `ALT_PEER_URL` = URL Render.
- **Cùng phiên bản:** hai bên chạy `bot/` giống từng byte (build `380dfca`) và cùng ccxt.
- **Live:** Render đang paper, nên muốn Botkeep chạy live thì phải chọn một trong hai:
  - tắt failover (để trống `ALT_PEER_URL` và dừng Render);
  - cho Render chạy live với key riêng, rồi đặt `ALT_PEER_MODE=live`.
- **Stock vẫn không có failover.** Render không bao giờ chạy `main_botkeep.py`.
- **Chưa kiểm:** `rootDir` và `buildFilter` trong spec Blueprint.

---

## 15. Các giai đoạn (mỗi giai đoạn một PR, xong vẫn deploy được)

| Giai đoạn | Nội dung | Kiểm tra chấp nhận |
|---|---|---|
| **0. Khảo sát và sao lưu** (không code) | **Botkeep:** phiên bản Python (**bắt buộc ≥ 3.12**); có cài lại requirements mỗi lần deploy không; lệnh khởi động; upload có giữ `data/` và `altcoin/bot.env` không; health probe (TCP hay `/health`, GET hay HEAD); IP client sau proxy; giới hạn File Manager; `df`; **tên** các biến env của tiến trình; có `/usr/share/zoneinfo` không. **Kiểm kê:** 2 token Telegram; 2 app Zoom và URL; 2 `DATABASE_URL` và danh sách bảng (truy vấn chỉ đọc ở §8); panel và `bot.env` của altcoin cũ; altcoin đang paper hay live; instance nào đang giữ leader. **Sao lưu:** `data/` của stock, `data/bot.db` của altcoin cũ, `pg_dump` cả hai DB. | Checklist điền đủ. Phát hiện DB dùng chung trước mọi việc. Python < 3.12 thì dừng lại để nâng cấp trước. Stock 22/22. Altcoin 5/5 test offline ở `8332222`. |
| **1. Bộ đóng gói** (không sửa `.py` trong `vnbot/`, `tradingagents/`, `main_botkeep.py`) | `scripts/build_botkeep_bundle.py`, `suite_bundle.py`, `pytest.ini`, README §2 + Python 3.12. | Builder báo 137 file / 18 thư mục / ≈2,13 MB, zip ≈0,78 MB, thoát 0. Build stock vẫn `7eaa3df`. Chèn file thứ 251 → suite đỏ. Tải zip lên Botkeep: giải nén ra đủ thư mục lồng (`vnbot/static`, `tradingagents/agents/analysts`); hỏng thì dùng `--dir-entries`. `/health` 200, `/phienban` "đúng bản phát hành", Telegram `/phienban` trả lời. |
| **2. Nhập altcoin kèm lịch sử** (ngủ yên) | Subtree add + merge (§3); dời `render.yaml`; `.gitignore` (+`!altcoin/dist/`); `altcoin/check_db.py`; `suite_altcoin_scripts`; builder gồm phần altcoin runtime. | Số commit như §3. `altcoin/tools/backtest_sessions.py` có mặt. `run_all` 24/24. Gói 161 file / 20 thư mục / ≈2,49 MB / zip ≈0,90 MB. Build `7eaa3df` và `380dfca`. Deploy lên Botkeep hành xử như cũ. |
| **3. Supervisor + `hosted.py`** (chưa có web) | `vnbot/altcoin_host.py`, `altcoin/hosted.py` (§1), `main_botkeep.py` (kiểm 3.12, handler tín hiệu, supervisor, `loop="asyncio"`, docstring), `SECRET_KEYS` + `add_secret_values`, `requirements.txt` (`ccxt==4.5.90`, `httpx`), `altcoin/requirements.txt` (ghim), hook `health_extra`, stock **v1.15.0** + NOTES + `python -m vnbot.version --stamp`. | `suite_altcoin_host` xanh. `run_all` xanh dưới `TZ=UTC` và `TZ=Asia/Ho_Chi_Minh`. `pip install --dry-run -r requirements.txt` resolve được, đã ghi phiên bản các gói dùng chung. Deploy với `ALTCOIN_ENABLED` chưa đặt: giống v1.14, RSS không đổi, log "Altcoin: tắt". **Cục bộ** với `=1` + `ALTCOIN_FRESH_START=1`, paper, SQLite tạm, token thử: log `[altcoin] … Dashboard sẵn sàng 127.0.0.1:N`; `kill -9 <child>` → restart sau 20 s trong khi `/health` stock vẫn 200; `kill -TERM <stock>` → không còn child mồ côi; `oom_score_adj` của child = 800; `data/bot.db` của stock không có `trades`/`locks`. Đo RSS đỉnh khi child chạy và `/phantich` đang chạy. |
| **4. Hai mục trên web** | Bước 1: `vnbot/webguard.py` (refactor) + `web.py` (`throttle=`). **Cổng chặn:** `suite_security_web` và `suite_web` xanh không sửa. Bước 2: `vnbot/hub.py`, thanh chuyển mục ở `ui.py`, thích nghi web trong `hosted.py`, `ALTCOIN_LEGACY_HOSTS`, re-stamp. | Suite proxy và web xanh, kể cả các vector `..`/`%2e`. Suite web/config/version của stock không đổi. Tổng 27/27. **Thử tay:** một lần hỏi mật khẩu; qua lại hai mục được; POST với `Origin: https://evil.example` → 403; `/altcoin/zoom` liệt kê `https://HOST/altcoin/...`; `/altcoin` chạy khi stock đang khởi động; giết child thì stock vẫn phục vụ. |
| **5. Cut-over production** | Theo thứ tự: (1) nhập lại bí mật thành `ALT_*` hoặc ghi vào `altcoin/bot.env`; (2) **dừng server altcoin cũ**; (3) chuyển DB (§8); (4) `ALT_INSTANCE_NAME=crypto`, `ALTCOIN_ENABLED=1`, khởi động lại; (5) đăng ký lại 10 URL Zoom. | `/health`: `altcoin.state=running`, `mode=paper`, `dropped_secrets` rỗng. `/altcoin/phienban` báo 3.2.0, `380dfca`, "đúng bản phát hành". Equity, số trade, tham số đã học giống bản cũ. `heartbeats` không có `crypto` từ hai host. Bot altcoin trả lời `status`, bot stock trả lời `/trangthai`. Không có `409` / `terminated by other getUpdates` trong 1 giờ log. Zoom Validate OK. p95 `/health` không đổi. RAM tăng < 200 MB. |
| **6. Render từ stockvn** | Nối Render sang stockvn, đặt `PEER_URL=https://HOST/altcoin`, archive repo cũ. | Log build của Render cài bộ gọn với ccxt 4.5.90. `/altcoin/` thấy heartbeat của `render` và `crypto`, đúng một leader. Dừng child → Render tiếp quản trong ≤ 90 s. Commit chỉ đụng stock không làm Render redeploy. |
| **7. Gia cố** (mục nào sửa `bot/` thì `python -m bot.version --stamp` trong `altcoin/` và deploy Render + Botkeep cùng lúc) | **Bắt buộc trước khi bật live:** vùng găng quanh `LiveEngine.open/partial/close`: hoãn `KeyboardInterrupt` do SIGTERM tới khi ra khỏi vùng. **Tuỳ chọn:** đưa seam vào `pages.py`/`ui.py` (`WEB_PREFIX`) và sửa hộp "Cập nhật bot"; Telegram nghỉ khi `ok:false` và lưu offset; `config.py` bỏ qua `__BOTKEEP_KEEP_SECRET_`; cookie `path=/altcoin/` + `secure`; thẻ Altcoin trên `/phienban` của stock. | Suite vẫn xanh. Hai build khớp trên cả hai host. Test vùng găng: gửi SIGTERM giữa `open` không bỏ dở bước ghi DB. |

**Rollback ở mọi giai đoạn:** đặt `ALTCOIN_ENABLED=0` rồi khởi động lại. Từ Giai đoạn 5, chỉ bật lại server altcoin cũ **sau khi** child đã dừng (tránh hai leader cùng tên). Supabase không phân kỳ dữ liệu; SQLite phân kỳ kể từ lúc chép.

---

## 16. Rủi ro và cách giảm

| Rủi ro | Cách giảm |
|---|---|
| Hai leader cùng tên `crypto` lúc cut-over (cùng Supabase), cùng giao dịch và bão 409 | Dừng server cũ trước khi bật. Chặn trùng tên qua `heartbeats` (thoát 75). |
| Vòng qua đăng nhập bằng `..` / `%2e` (httpx chuẩn hoá dấu chấm) | Hub trả 400 và so danh sách công khai chính xác; có test vector. |
| Cùng DB stock qua endpoint khác (pooler/direct) → `prune()` xoá dữ liệu stock | Kiểm bảng `market`/`fills` trước `Store()`. Supervisor kiểm `trades`/`locks` trong DB stock. |
| Peer paper tiếp quản lệnh live, "đóng" lệnh thật trong DB | Live cần `ALT_PEER_MODE=live`. `hosted.py` từ chối nếu có peer `paper=True`. |
| Live không bao giờ khởi động (`I_UNDERSTAND_THE_RISK`) | `hosted.py` đặt biến này sau gate và chạy lại kiểm tra của `main()`; ma trận test chạy tới `Bot()`. |
| SIGTERM rơi vào giữa đặt lệnh và ghi DB; restart nay xảy ra theo mỗi lần deploy stock | Vùng găng ở Giai đoạn 7 là điều kiện để bật live. Tài liệu ghi rõ. |
| OOM lúc stock phân tích nặng giết cả hai | `oom_score_adj=800` cho child; đo RSS đỉnh ở Giai đoạn 3; `rss_mb` trong `/health`; công tắc khẩn `ALTCOIN_ENABLED=0`. |
| Botkeep chạy Python 3.11 → stock lỗi SyntaxError | Giai đoạn 0 kiểm; builder từ chối < 3.12; `main_botkeep` báo lỗi rõ. |
| ccxt ghim cứng làm đổi thư viện stock dùng chung | Ghim `ccxt==4.5.90`; `pip --dry-run` + `run_all` mỗi lần nâng; `loop="asyncio"`. |
| Seam monkeypatch gãy khi altcoin đổi | `suite_altcoin_web`/`host` ghim từng điểm; tự kiểm chạy giảm chức năng; Telegram mất seam thì xoá token; Giai đoạn 7 đưa seam vào code. |
| Spawn sai đường dẫn / nạp nhầm `.env` của stock | Đường dẫn tuyệt đối, `cwd=altcoin/`; test spawn từ `/` với `.env` mồi nhử. |
| Child mồ côi, tranh cổng, child chạy không có UI | PDEATHSIG từ thread sống lâu; so `--parent-pid`; cổng mới mỗi lần spawn; quá hạn sẵn sàng thì spawn lại. |
| `stop()` không chạy khi SIGTERM | Handler riêng cài trước `uvicorn.run`; hook lifespan; supervisor là daemon. |
| `/health` bị treo vì supervisor | Snapshot bất biến; test < 100 ms. |
| Bí mật bị che (`KEEP_SECRET`) mất âm thầm | WARNING + `dropped_secrets` trong `/health` và trên `/altcoin/`; tài liệu "nhập lại từ nguồn". |
| Lộ bí mật qua log của child | Che hai lớp; cha biết cả giá trị trong `bot.env`; stderr đi chung pipe. |
| Giờ phiên altcoin lệch 7 giờ (thiếu zoneinfo hệ thống) | Kiểm `tm_gmtoff`, fallback tzdata, nếu vẫn lệch thì thoát 78. |
| Botkeep không giải nén đúng zip không có mục thư mục | Thử ở Giai đoạn 1; cờ `--dir-entries`. |
| Botkeep không cài lại requirements | `ALTCOIN_AUTO_INSTALL=1` hoặc lệnh pip tay; trạng thái "thiếu thư viện" hiện trên UI. |
| Vượt 250 file / 4 MB về sau | `suite_bundle` đỏ khi vượt, cảnh báo ở 85 %; hiện dư ≈85 file / ≈1,46 MB. |
| Deploy cả repo qua GitHub | Không khả thi (≈266 mục); chỉ deploy zip do builder tạo. |
| Telegram 409 / stock "điếc" | Hai token; token trùng thì chỉ gửi; SQLite + peer bị chặn. |
| Gộp IP client sau proxy → một lần khoá chặn mọi người (cả hai mục) | Kiểm ở Giai đoạn 0; `FORWARDED_ALLOW_IPS`. |
| Hạn mức OpenRouter free bị dùng chung | Log khi dùng fallback; đặt `ALT_OPENROUTER_API_KEY`. |

**Chưa kiểm chứng:**

- **Botkeep:** health probe, proxy/IP, ngữ nghĩa ghi đè khi upload, có đếm thư mục không, MB hay MiB, cách giải nén zip, Python, có cài lại requirements không, zoneinfo.
- **Render:** `rootDir`, `buildFilter`.
- **Hệ thống:** RAM đỉnh; hai bot hiện có đang dùng chung DB, token hay app Zoom không.
- **Trình duyệt:** một lần hỏi Basic trên Chrome và Safari.

---

## 17. Quyết định cần anh/chị chốt (in đậm = khuyến nghị)

1. **Mô hình tiến trình:** **altcoin là tiến trình con có supervisor, stock giữ cổng.** Cách còn lại là chạy chung tiến trình: tiết kiệm ≈80 MB nhưng phải sửa khoảng 15 file altcoin và tăng rủi ro cho stock.
2. **Tiền tố URL:** **`/altcoin/`**.
3. **Đăng nhập:** **một mật khẩu `WEB_PASSWORD`**, tắt cookie altcoin. Hoặc thêm `ALT_DASHBOARD_PASS` (hai lớp).
4. **Telegram:** **hai bot** (`ALT_TELEGRAM_TOKEN` = token OKX hiện có). Hoặc một bot với `/alt …` (phải sửa code, mất lệnh khi failover).
5. **Zoom:** **hai app**, đăng ký lại 10 URL dưới `/altcoin/`. Hoặc tắt Zoom altcoin.
6. **Database:** **altcoin giữ DB riêng** (Supabase hiện tại, nếu không có thì `data/altcoin/bot.db`).
7. **Render failover:** **giữ, ở chế độ paper**, deploy từ stockvn với `rootDir: altcoin`.
8. **Nhánh `ccr-46f71b1d-mqcst3`:** **đưa vào** `altcoin/tools/`.
9. **`altcoin/dist/okx-ai-bot-v3.2.0.zip`:** **giữ tracked + `!altcoin/dist/`**. Hoặc chuyển sang GitHub Release.
10. **Lịch sử git:** **`git subtree` không squash**. Hoặc `filter-repo`.
11. **Cách deploy lên Botkeep:** **zip do builder tạo**. Deploy cả repo không khả thi.
12. **Nghĩa của "4 MB":** **4.000.000 B cho cả zip lẫn giải nén; file + thư mục ≤ 250.**
13. **`ALTCOIN_ENABLED`:** **mặc định 0 vĩnh viễn**, chỉ bật trên panel sau khi đã chép DB.
14. **Fallback AI:** **cho dùng `ROUTER9_*`/`OPENROUTER_API_KEY` của stock khi thiếu `ALT_*`** (có log). Hoặc bắt khai riêng.
15. **Tên miền cũ:** **chuyển hướng qua `ALTCOIN_LEGACY_HOSTS`** nếu trỏ được về server mới; nếu không thì bỏ.
16. **Phiên bản:** **stock v1.15.0 ("Gộp bot Altcoin: mục /altcoin/"); altcoin giữ 3.2.0 / `380dfca`** tới Giai đoạn 7.
17. **Giấy phép:** **ghi rõ Apache-2.0 của repo áp dụng cho `altcoin/`** (hiện altcoin chưa có giấy phép).
18. **Python trên Botkeep:** **nâng lên 3.12+** nếu Giai đoạn 0 thấy thấp hơn. Đây là điều kiện bắt buộc của stock.
19. **ccxt:** **ghim `4.5.90` ở cả hai `requirements.txt`**, chấp nhận hạ `idna`/`charset_normalizer` của stock.
20. **`ALTCOIN_AUTO_INSTALL`:** **mặc định tắt**; bật nếu Botkeep không cài lại requirements khi cập nhật.
21. **Live:** **chỉ bật sau mục vùng găng của Giai đoạn 7**, và khi đó tắt failover hoặc cho Render chạy live (`ALT_PEER_MODE=live`).