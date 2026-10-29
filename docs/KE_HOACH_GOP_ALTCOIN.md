# Kế hoạch gộp altcoin_bot vào Stockvn

Tài liệu này là kế hoạch, chưa sửa code. Nguồn: repo `traique/stockvn` (v1.14.0) và `traique/altcoin_bot` (nhánh mặc định `ccr-78e45817-g8v8rx`, build `380dfca`), khảo sát ngày 2026-10-29. **Bản 2** (cùng ngày): bốn quyết định dưới đây đã được áp vào mọi mục, không chỉ §7.

## Quyết định đã chốt (2026-10-29)

1. **Một mật khẩu dùng chung.** `WEB_PASSWORD` (Basic, kiểm ở Hub) cho cả Cổ phiếu và Altcoin. Đăng nhập cookie của altcoin **luôn tắt**. `ALT_DASHBOARD_*` không còn là lựa chọn: đặt trên panel thì bị bỏ qua và có WARNING.
2. **Một bot Telegram và một app Zoom, dùng chung cho stock và altcoin.**
   - Chỉ tiến trình stock nhận. Telegram chỉ cho một `getUpdates` trên mỗi token (409), và Zoom gửi sự kiện chatbot tới một endpoint cho mỗi app.
   - Lệnh altcoin đi qua họ lệnh mới `/alt <lệnh>` của stock tới child.
   - Thông báo altcoin đi từ child, qua một pipe riêng, về stock, rồi ra đúng các kênh và công tắc của stock.
   - Child **không bao giờ** cầm token Telegram hay khoá Zoom nào.
3. **Không giữ Render.** Không failover, không peer: chỉ một instance altcoin, chạy trong server Botkeep.
   - `render.yaml` bị xoá khỏi cây gộp, nhưng vẫn còn trong lịch sử git.
   - Code leader/keepalive trong `altcoin/bot/` giữ nguyên byte nhưng ngủ, vì `PEER_URL`/`SELF_URL` bị ép rỗng.
   - Kế hoạch không còn phụ thuộc vào Render, và không còn hướng dẫn deploy Render.
4. **Một repo, một server Botkeep.** Toàn bộ repo `altcoin_bot` chuyển vào `stockvn` (thư mục `altcoin/`, giữ lịch sử git). Chỉ chạy **một** server Botkeep với một gói zip và lệnh `python main_botkeep.py`. Phần altcoin là tiến trình con **trên chính server đó**, không phải server thứ hai. Server Botkeep altcoin cũ tắt hẳn ở Giai đoạn 6, sau khi chép dữ liệu sang.

Các sự kiện đã kiểm trong code (chỉ đọc):

- **`altcoin/bot/config.py`**
  - Nạp `.env` rồi `bot.env`, tìm ở `_ROOT` hoặc ở cwd. Giá trị trong file ghi đè `os.environ`. Thuộc tính `cfg.*` cố định lúc import (`:65-254`).
  - Đặt `TZ` rồi gọi `tzset()`.
  - `MODE` chỉ đọc một lần lúc import. Các chỗ dùng nó đọc `cfg.MODE` lúc gọi hàm (`learner.py:112`, `main.py:334`), nên ép `cfg.MODE` sau khi import vẫn có hiệu lực.
  - `INSTANCE_NAME` rơi về `RENDER_SERVICE_NAME` rồi `nodename` (`:223`). `SELF_URL` rơi về `RENDER_EXTERNAL_URL` (`:226`).
- **`dashboard.py:636`** đọc `DASHBOARD_HOST` bằng `os.getenv` lúc gọi.
- **`dashboard.py:629-630`**: `start_dashboard` tra `build_app` trong globals của module lúc gọi, nên bọc được để thêm route.
- **`strategy.py:387` và `storage.py:239`** dùng `time.localtime().tm_hour`. Vì vậy giờ phiên của altcoin phụ thuộc múi giờ của tiến trình.
- **`main()` của altcoin (`main.py:558-566`)**
  - Thoát `sys.exit(1)` khi `MODE=live` mà thiếu `OKX_API_KEY`.
  - Cũng thoát `sys.exit(1)` khi `os.getenv("I_UNDERSTAND_THE_RISK") != "yes"`.
  - Cài `SIGTERM` để ném `KeyboardInterrupt`.
- **Lệnh và thông báo của altcoin**
  - `main.py:23` nhập tên `Telegram` vào namespace `bot.main`, nên chỉ cần gán `bot.main.Telegram` trước `Bot()` là thay được kênh.
  - `handle_command(text, source)` (`main.py:146-199`) trả về chuỗi. Nó chỉ xét từ đầu tiên, và không bắt lỗi.
  - `Notifier.send` (`notify.py:35-46`) bỏ thông báo khi chưa là leader. Nó gọi `ch.send` đồng bộ, không bắt lỗi, và bỏ qua kênh có `enabled` sai.
- **`telegram.py`**: `enabled = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)`. Chỉ leader mới poll; nhận `ok:false` thì poll lại ngay, không nghỉ; xác thực theo chat id. Sau gộp lớp này không bao giờ được tạo (§7).
- **`_keepalive` (`main.py:430-434`)** không làm gì khi cả `SELF_URL` lẫn `PEER_URL` rỗng.
- **`storage.try_lock`**: một tiến trình mang **cùng tên owner** sẽ gia hạn được khoá, tức là thành leader thứ hai.
- **Stock `vnbot/telegram_bot.py`**
  - `_poll` gặp 409 thì nghỉ 30 s (`:466-469`).
  - Telegram và Zoom cùng đi vào **một** `_handle` (`:486-541`); Zoom vào qua `zoom_command` (`:406-423`). Lệnh lạ thì trả help (`:535`).
  - `broadcast` (`:278-286`) gửi Zoom trước, rồi tới các chat báo cáo, theo công tắc `zoom_notify`/`telegram`. `send` cắt tin ở 3.900 ký tự.
  - Không có `parse_mode`, nên mọi tin là văn bản thường.
- **Stock không có endpoint nào nhận thông báo từ bên ngoài.**
  - Auth là dependency gắn từng route (`guard`, `same_origin`, `web.py:496-532`), không phải middleware.
  - Uvicorn tin `X-Forwarded-For` đến từ `127.0.0.1`.
- **Python của stock phải ≥ 3.12.** `vnbot/config_web.py:80` và `vnbot/taisan_web.py:553` dùng f-string lồng kiểu PEP 701, nên báo SyntaxError trên 3.11. Code altcoin vẫn biên dịch được trên 3.11.
- **Bản dàn dựng gộp:**
  - 161 file, 20 thư mục, 2.492.462 B (gồm `altcoin/.env.example`; gói cuối bỏ file này, §11).
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
| Công sức (cao = ít việc) | 5 | **6** | 3 |
| Vận hành (1 lần deploy, log, khởi động lại) | 8 | 7 | **9** |
| **Tổng** | 44 | **49** | 45 |

Ba quyết định 2026-10-29 áp như nhau cho cả ba phương án. Chỉ điểm Công sức của isolation-first đổi (7 → 6), vì cầu nối kênh phải vượt ranh giới tiến trình (pipe, endpoint, hai token). Nó vẫn đứng đầu.

- **lean-inprocess** (chạy chung tiến trình)
  - Phải sửa khoảng 15 file altcoin.
  - `TZ=Asia/Ho_Chi_Minh` áp cho cả tiến trình, làm đổi khoảng 18 lời gọi giờ "naive" của stock.
  - Altcoin có thể chặn GIL hoặc event loop của stock.
- **isolation-first** (tiến trình con)
  - `bot/` giữ nguyên từng byte. Stock không bị đổi TZ, env hay logging.
  - Cái giá: phải viết proxy, supervisor và cầu nối kênh, cộng một lớp seam monkeypatch có test ghim.
- **unified-shell**
  - Trải nghiệm thống nhất nhất.
  - Nhiều việc nhất và chạm nhiều code stock nhất.

**Chọn: isolation-first làm xương sống.** Ghép thêm các ý sau:

| Ý ghép | Lấy từ |
|---|---|
| Child giữ `TIMEZONE` riêng và kiểm múi giờ thật | unified-shell |
| Bộ đóng gói nghiêm ngặt: không ghi mục thư mục, kiểm file + thư mục ≤ 250, zip tái lập được, test mồi nhử | unified-shell |
| `/health` có `altcoin.state`; chi tiết và `rss_mb` ở `/altcoin/hub.json` (Basic) | unified-shell |
| Điều kiện live `ALT_LIVE_ACK=<tên instance>` | unified-shell |
| Chuyển hướng tên miền cũ (`ALTCOIN_LEGACY_HOSTS`, tuỳ chọn) | unified-shell |
| Chỉ khởi động altcoin sau khi stock sẵn sàng | lean, unified |
| ≤ 8 request đồng thời vào `/altcoin` | lean |
| Không bao giờ để altcoin chạy trên DB rỗng | lean (thêm chốt chặn trong code) |
| `pytest.ini` với `python_files = test_suites.py` | lean |
| Kiểm DB stock xem đã lẫn bảng altcoin chưa | lean |
| Bổ sung khi rà soát (xem các mục 1, 4–8) | Đường dẫn chuẩn hoá ở Hub; viết lại `Location` với mọi scheme; token Hub↔child; `oom_score_adj`; kiểm DB theo bảng; **child không cầm token kênh nào; cầu nối `/alt` + pipe thông báo; chặn một instance (heartbeat + `flock`); khoá lệnh ghi trong child; canary trên Botkeep trước cut-over** |

---

## 1. Kiến trúc và mô hình tiến trình / luồng

```
Botkeep: python main_botkeep.py                    (lệnh khởi động GIỮ NGUYÊN; Python ≥ 3.12)
PID A – stock, giữ cổng công khai duy nhất (SERVER_PORT > PORT > APP_PORT > 8080)
 ├─ main thread: cài handler SIGTERM/SIGINT → uvicorn.run(Hub, 0.0.0.0, loop="asyncio", access_log=False)
 │   Hub (vnbot/hub.py; lúc import chỉ dùng stdlib → cổng vẫn mở trong <1 s)
 │    ├─ /altcoin*: kiểm đường dẫn THÔ trước tiên (400 nếu bất thường, §4); mọi so khớp bên dưới
 │    │             dùng đường dẫn ĐÃ GIẢI MÃ (scope["path"])
 │    ├─ GET/HEAD /altcoin        → 308 Location: /altcoin/ (tương đối, giữ query)
 │    ├─ /altcoin/_hub, /altcoin/_hub/*, đường Zoom/OAuth cũ của altcoin, POST /altcoin/caidat/{kenh/*,zoomtest}
 │    │                           → 404, không bao giờ tới child (§4)
 │    ├─ GET /altcoin/{zoom,docs,privacy,terms,support,data-deletion} → 308 sang trang cùng tên của stock
 │    ├─ GET /altcoin/hub.json    → Basic → Hub tự trả state() đầy đủ (không token), không tới child
 │    ├─ /altcoin/*               → Basic WEB_PASSWORD
 │    │                             + LoginThrottle dùng chung, same-origin cho POST, body ≤ 64 KB, ≤ 8 đồng thời
 │    │                             → xoá mọi X-Hub-* của client → httpx stream → http://127.0.0.1:<cổng tạm>/<đường dẫn chuẩn>
 │    │                             → child chưa chạy: trang 503 "Altcoin đang khởi động / tắt" (Retry-After 5)
 │    └─ mọi đường dẫn khác + lifespan → LateApp (vnbot/boot.py, giữ nguyên) → create_app() của stock
 │                                       (gồm POST /webhook/zoom của app Zoom DÙNG CHUNG)
 ├─ thread "boot" và các thread stock (giữ nguyên). "telegram-bot" là nơi DUY NHẤT gọi getUpdates.
 │    └─ _handle (Telegram và Zoom): "/alt <lệnh>" → vnbot/altcoin_bridge.py
 │         → POST http://127.0.0.1:<cổng tạm>/_hub/command (X-Hub-Cmd-Token) → trả lời bằng send()
 ├─ thread "altcoin-supervisor" (daemon; vnbot/altcoin_host.py): kiểm tra trước → chờ stock (≤ 60 s) → spawn → watchdog
 ├─ thread "altcoin-log": đọc stdout của child (utf-8, errors="replace") → "[altcoin] …" đã che bí mật + giữ 200 dòng
 ├─ thread "altcoin-hub": đọc pipe riêng của child → khung JSON {"t":"state"|"notify", …}; luôn đọc hết pipe
 │    (khung notify vào hàng đợi bằng put_nowait: đầy thì bỏ và đếm, không bao giờ chặn)
 └─ thread "altcoin-notify": lọc, viết lại gợi ý lệnh, chống trùng, giới hạn nhịp → TelegramBot.alt_broadcast("🪙 Altcoin › …")
PID B – python -u <ROOT>/altcoin/hosted.py --port <tạm> --parent-pid <A> …
        (cwd=<ROOT>/altcoin, env dựng riêng, pass_fds=(<đầu ghi pipe>,))
 ├─ main() của bot.main NGUYÊN BẢN; mọi signal.signal, sys.exit, os._exit(3), tzset, ghi os.environ,
 │  basicConfig và handler gắn root logger chỉ có tác dụng trong PID B.
 ├─ KHÔNG token Telegram, KHÔNG khoá Zoom, PEER_URL/SELF_URL rỗng (dù bot.env ghi gì)
 ├─ kênh "Telegram" của Notifier = HubChannel → put_nowait vào hàng đợi; thread "hub-notify" là nơi DUY NHẤT ghi pipe
 └─ Zoom của altcoin trơ (cfg rỗng)
```

**Vì sao stock giữ cổng công khai:**

- `LateApp` đã bảo đảm mở cổng trong giây đầu.
- Health check của Botkeep đang trỏ vào stock.
- Khoảng 60 URL stock, các link Telegram/Zoom đã gửi đi và các test đều giả định stock nằm ở `/`.
- Bot Telegram và app Zoom dùng chung đều đã trỏ vào stock, nên stock là nơi nhận tự nhiên.

Hub bọc **bên ngoài** `LateApp`. Nhờ vậy `/altcoin` vẫn chạy khi stock đang khởi động, và cả khi `_start()` của stock lỗi. Khi `_start()` lỗi thì không có `broadcast`: thông báo altcoin được đệm rồi bỏ, kèm bộ đếm (§7.3).

### Supervisor (`vnbot/altcoin_host.py`, `ROOT = Path(__file__).resolve().parents[1]`)

**Kiểm tra trước khi spawn.** Nếu sai thì chuyển trạng thái, không lặp khởi động lại.

| Điều kiện | Trạng thái |
|---|---|
| `ALTCOIN_ENABLED != 1` (mặc định 0, **vĩnh viễn**) | `off` |
| `find_spec("ccxt")` hoặc `find_spec("httpx")` trả None | Có `ALTCOIN_AUTO_INSTALL=1`: chạy logic `ensure_deps` của `run.py` ngay trong thread supervisor (`pip --no-cache-dir`, cài từ `altcoin/requirements.txt`, lần lượt thử `--user`, thường, `--break-system-packages`; sau đó xoá `~/.cache/pip`) rồi kiểm lại. Không có: `disabled: thiếu thư viện`, kèm lệnh gợi ý `pip install --no-cache-dir ccxt==4.5.90`. |
| `ALT_DATA_DIR` trùng `DATA_DIR` của stock | `disabled` |
| DB stock đã có `trades`/`locks`/`learn_log` (truy vấn chỉ đọc `information_schema`) | `disabled` + WARNING "DB đang dùng chung" |
| Panel có khoá bị bỏ: `ALT_TELEGRAM_*`, `ALT_ZOOM_*`, `ALT_PEER_*`, `ALT_SELF_URL`, `ALT_KEEPALIVE_INTERVAL`, `ALT_DASHBOARD_*`, `ALT_SUPPORT_EMAIL` | Vẫn spawn. WARNING một lần, tên khoá vào `ignored_keys`. Không chuyển cho child. |

**Cổng.** Bind `127.0.0.1:0`, đọc số cổng, đóng lại, rồi truyền qua `--port`. Mỗi lần spawn một cổng mới.

**Spawn.**

- Mở pipe: `r, w = os.pipe()`.
- Lệnh: `Popen([sys.executable, "-u", str(ROOT/"altcoin"/"hosted.py"), "--port", …, "--parent-pid", str(os.getpid()), "--prefix", "/altcoin", "--public-url", …, "--data-dir", …, "--mode", paper|live, "--instance", …, "--deny-db", …, ["--allow-empty-db"]], cwd=str(ROOT/"altcoin"), env=<§6>, pass_fds=(w,), stdin=DEVNULL, stdout=PIPE, stderr=STDOUT, close_fds=True)`.
- Ngay sau `Popen`, cha **đóng `w`**. Như vậy khi child chết, thread `altcoin-hub` đọc `r` thấy EOF.
- Dùng đường dẫn tuyệt đối. Nếu dùng đường dẫn tương đối kèm `cwd`, Python sẽ tìm `altcoin/altcoin/hosted.py` và thoát mã 2. cwd phải là `altcoin/` để `.env` gốc của stock không bao giờ bị nạp.
- Chỉ spawn từ thread supervisor sống lâu, vì `PR_SET_PDEATHSIG` bắn theo luồng tạo child.
- Hai token, mới mỗi lần spawn (`secrets.token_urlsafe(32)`):
  - `HUB_TOKEN`: Hub → child, gắn vào mọi request proxy;
  - `HUB_CMD_TOKEN`: cầu nối lệnh → child, chỉ dùng cho `/_hub/*`.

  Cả hai cùng `HUB_FD=<w>` đi qua env, không qua argv. Không token nào nằm trong `state()`, `/health` hay `/altcoin/hub.json`.
- Vòng supervisor bọc `try/except`: lỗi thì ghi log rồi chạy lại vòng.

**Sẵn sàng.** Gọi `GET /health` của child mỗi 2 s, tối đa 120 s, rồi chuyển `running`. Quá hạn (ví dụ tranh cổng làm dashboard không bind được trong khi bot vẫn chạy) thì SIGTERM, chờ 15 s, SIGKILL, rồi spawn lại với cổng mới. Lần này tính là một lần restart.

**Watchdog.** `GET /health` mỗi 30 s, timeout 10 s. Trượt 4 lần liên tiếp thì SIGTERM, chờ 15 s, SIGKILL, rồi khởi động lại.

**Mã thoát của child:**

| Mã | Ý nghĩa | Supervisor xử lý |
|---|---|---|
| 78 | Cấu hình sai | `disabled: <lý do>`, không restart |
| 75 | Tạm thời: một instance khác còn sống trong `heartbeats` (server altcoin cũ hoặc Render chưa tắt), hoặc `hosted.lock` đang bị child khác trên cùng host giữ | Trạng thái `waiting: <tên>@<host>` (lấy từ khung `state` cuối cùng child gửi trước khi thoát); thử lại sau `LEADER_TTL + 10` s |
| -9 | Bị kill | Ghi "bị kill (OOM?)" |
| Mã khác | Lỗi | Backoff 20 s (giống `run.py:67`), nhân đôi tới 600 s. Đặt lại sau 15 phút chạy khoẻ. |

**Dừng.**

- `main_botkeep.py` cài handler `SIGTERM`/`SIGINT` **trước** `uvicorn.run()`. uvicorn (≥ 0.29, đã thấy ở 0.54) khôi phục handler này rồi `raise_signal` sau khi tắt êm. Handler gọi `stop()`, sau đó trả về `SIG_DFL` và tự gửi lại tín hiệu.
- Hub bọc `receive` để bắt `lifespan.shutdown` và gọi `stop()` qua `asyncio.to_thread`.
- Vẫn giữ `finally: stop()` cho trường hợp `uvicorn.run()` trả về bình thường.
- `stop()` gửi SIGTERM, chờ 15 s cho altcoin nhả leader lock, rồi SIGKILL.

**`state()`.** Là một dict bất biến, được thay nguyên khối (gán nguyên tử). Đọc mà không cần khoá, chỉ thêm bước đọc `/proc/<pid>/statm`. Các trường:

`{state, reason, pid, port, started, restarts, last_exit, version, build, leader, mode, seam_ok, bridge, dropped_secrets, ignored_keys, rss_mb}`

Trong đó `bridge = {notified, muted, dropped, deduped, send_failed, last_notify_ts, last_cmd_ts}`.

- `/health` **công khai** chỉ thêm `altcoin: {"state": …}`. Mode, leader, reason (`<tên>@<host>`), bridge và thời điểm lệnh cuối không lộ ra ngoài.
- Toàn bộ dict (không token) ở `GET /altcoin/hub.json`, do Hub trả sau Basic, chạy được cả khi child chết hoặc `_start()` của stock lỗi.
- `seam_ok=false` thì supervisor ghi ERROR; nhãn Altcoin trên thanh chuyển mục hiện "(lỗi kênh)".

Child gửi trạng thái riêng qua khung `{"t":"state",…}` trên pipe (schema ở §7.4), thay cho dòng `@@hosted` trên stdout của bản 1. stdout chỉ còn log, nên không dòng log nào giả được trạng thái.

### `altcoin/hosted.py` (mới, nằm ngoài fingerprint altcoin = `bot/**` + `run.py`)

1. **Tự chết theo cha, chết trước khi OOM.**
   - `prctl(PR_SET_PDEATHSIG, SIGTERM)`.
   - So `os.getppid()` với `--parent-pid` ngay sau `prctl`, rồi mỗi 5 s. Lệch thì `os.kill(os.getpid(), SIGTERM)` để `main()` thoát êm và nhả lock; sau 20 s mà chưa thoát thì `os._exit(0)`.
   - Ghi `800` vào `/proc/self/oom_score_adj`, để kernel giết altcoin trước stock; in giá trị đọc lại ra log (`oom_score_adj=800`).
   - Đọc `HUB_FD`, `HUB_TOKEN`, `HUB_CMD_TOKEN` rồi **xoá cả ba khỏi `os.environ`**, và gọi `os.set_inheritable(HUB_FD, False)`. Fd đưa qua `pass_fds` là kế thừa được trong child (đã thử trên 3.13), nên nếu không làm vậy, tiến trình cháu nào (`os.system`, `close_fds=False`) cũng nhận đầu ghi và token.
2. **Nạp cấu hình.** `import bot.config`, đọc `altcoin/.env` và `altcoin/bot.env`; quy tắc file-wins chỉ áp trong child. Sau đó **kiểm múi giờ**: so `time.localtime().tm_gmtoff` với `ZoneInfo(TIMEZONE).utcoffset(now)`.
   - Lệch thì đặt `TZ=":<đường dẫn zoneinfo của gói tzdata>"` rồi `tzset()`.
   - Vẫn lệch thì thoát 78.
3. **Niêm phong và thứ tự ưu tiên** (bảng ở §6). Mọi bước đều làm **sau** `import bot.config`, vì gán env thôi là không đủ.
   - `cfg.DASHBOARD_PORT = port`, `os.environ["DASHBOARD_HOST"] = "127.0.0.1"`.
   - `cfg.DATA_DIR`, `cfg.PUBLIC_URL`, `cfg.INSTANCE_NAME` lấy từ argv.
   - **Kênh, Render và đăng nhập riêng luôn rỗng.** Các thuộc tính sau bị gán `""`, và tên env tương ứng bị xoá khỏi `os.environ`, dù giá trị đến từ đâu:
     - `cfg.TELEGRAM_TOKEN`, `cfg.TELEGRAM_CHAT_ID`;
     - 10 thuộc tính `cfg.ZOOM_*` (`config.py:230-239`). Env có 11 tên: `ZOOM_BOT_SECRET_TOKEN` chỉ là bí danh gộp vào `cfg.ZOOM_SECRET_TOKEN`, nên không gán `cfg.ZOOM_BOT_SECRET_TOKEN` mà chỉ xoá tên env. `suite_altcoin_host` ghim cả hai danh sách;
     - `cfg.PEER_URL`, `cfg.SELF_URL`;
     - `cfg.DASHBOARD_USER`, `cfg.DASHBOARD_PASS`.

     Env của child không bao giờ chứa các khoá này; chỉ `bot.env` cũ mới mang chúng vào được. Có giá trị bị xoá thì ghi WARNING liệt kê tên khoá và đưa vào `ignored_keys`.
   - Các khoá nhạy cảm còn lại (`DATABASE_URL`, `OKX_*`…) lấy giá trị panel đè lên `bot.env`, mỗi khoá bị đè ghi một WARNING.
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
   | `DATABASE_URL` đã chuẩn hoá trùng `--deny-db` | 78 |
   | DB Postgres có bảng `market` hoặc `fills` trong `public` (cùng DB stock qua endpoint khác) | 78 |
   | DB rỗng: SQLite chưa có file, hoặc kv thiếu cả `start_equity` lẫn `paper_balance`, và không có `--allow-empty-db` | 78 "DB rỗng – chép DB cũ hoặc đặt ALTCOIN_FRESH_START=1" |
   | **Một instance trên cùng host:** `fcntl.flock(LOCK_EX \| LOCK_NB)` trên `<DATA_DIR>/hosted.lock`, giữ suốt đời child (kernel tự nhả khi child chết, kể cả SIGKILL) | Bị giữ → 75 "child khác trên cùng host còn chạy". Chặn được hai child chồng nhau lúc restart dù cùng tên và cùng host. |
   | **Một instance duy nhất:** `heartbeats` có instance khác còn sống, tức `ts` trong `LEADER_TTL` và tên khác, hoặc cùng tên nhưng `host=` khác `os.uname().nodename` | 75 "instance khác đang chạy: `<tên>@<host>` – dừng server altcoin cũ / Render". Áp cho cả paper lẫn live. Cùng tên cùng host thì đã qua `flock`, nên được chạy. |

   Trước khi thoát 78/75, child gửi một khung `state` cuối có `reason`.

   Các kiểm tra của bản 1 đã bỏ: so sha token Telegram (`--deny-sha`), vì child không còn token; SQLite + `PEER_URL`, vì không còn peer; live + peer `paper=True`, vì đã nằm gọn trong chặn một instance.
6. **Thích nghi web** (chi tiết ở §4). Gồm cả thay hai thẻ kênh ở `/caidat` (§7.5).
7. **Kênh chung** (chi tiết ở §7).
   - Trước `main()`: `bot.main.Telegram = HubChannel`.
   - Bọc `bot.dashboard.build_app` để thêm `POST /_hub/command`.
   - Thêm middleware hai token:
     - `/_hub/*` chỉ nhận `X-Hub-Cmd-Token`;
     - mọi đường dẫn khác cần `X-Hub-Token`, trừ `GET /health`;
     - so bằng `hmac.compare_digest`; sai thì 403.
   - Đối tượng `Zoom` của altcoin được giữ (handler lệnh và `/caidat` gọi phương thức của nó), nhưng trơ: cfg rỗng nên `chatbot_on`/`webhook_on` là False và không có worker.
   - Mỗi seam có bước tự kiểm: thiếu thuộc tính thì ghi log lỗi và chạy giảm chức năng. Nếu seam `Telegram` gãy, lớp Telegram gốc được tạo nhưng `enabled=False` (cfg rỗng). Kết quả là mất thông báo chứ **không** sinh 409.
   - Kiểm cả **hiệu lực**, không chỉ sự tồn tại: khi `bot.start_equity` đã có (sau `Bot.__init__`; `self.tg` được gán ở `main.py:52`, sau `start_dashboard`), thread `hub-notify` kiểm `isinstance(bot.tg, HubChannel) and bot.tg in bot.notify.channels`. Sai thì `seam_ok=false` trong khung `state`, ERROR ở cả hai phía, banner trên `/altcoin/`.
8. **`--check`.** In cấu hình đã niêm phong ra JSON (gồm `ignored_keys`, `hub_fd` có hay không). Không gọi mạng, không import ccxt.
9. `from bot.main import main; main()`. `main()` vẫn dùng lại được, vì route `/_hub/command` lấy `bot` qua closure của `build_app(bot)`.

### Đánh đổi

- **Tốn:**
  - child tốn ≈155 MB RSS (đo), trong đó ≈80 MB là phần trội so với chạy chung tiến trình (ước tính);
  - ≈1 ms cho chặng localhost;
  - ≈450 dòng code host, cộng ≈250 dòng cầu nối (stock `altcoin_bridge.py` + `HubChannel`/endpoint trong `hosted.py`);
  - child mất 2–5 s mới sẵn sàng (`import ccxt` mất 2,3 s lúc nguội);
  - seam có thể gãy khi altcoin đổi tên hàm;
  - mỗi lần deploy hoặc restart stock thì altcoin cũng restart. Không còn failover, nên altcoin ngừng vài giây tới vài phút mỗi lần.
- **Được:**
  - cô lập lỗi hai chiều (kể cả OOM, nhờ `oom_score_adj`);
  - `bot/` giống từng byte build `380dfca`;
  - stock giữ nguyên TZ, env, logging;
  - child không bao giờ thấy `WEB_PASSWORD`, `TELEGRAM_TOKEN`, `ZOOM_*`, `ROUTER9_*` hay bí mật khác của stock;
  - **một nơi nhận lệnh và một chính sách thông báo**: che bí mật, cắt tin, công tắc, quyền;
  - `git subtree pull` vào `bot/**` và `run.py` vẫn sạch cho tới khi repo cũ được archive. Các file ngoài `bot/` phải giải xung đột tay: đã sửa (`README.md`, `check_db.py`, `requirements.txt`, `.env.example`, `botkeep.env`) hoặc đã xoá (`render.yaml`).

### File stock bị sửa / thêm

- **Sửa:**
  - `main_botkeep.py`: kiểm `sys.version_info >= (3,12)` (sai thì ghi thông báo rõ rồi thoát 1); handler tín hiệu; supervisor; `loop="asyncio"`; nối cầu nối (`bot.alt`, sink thông báo); docstring.
  - `vnbot/telegram_bot.py`:
    - nhánh `/alt` và `/altcoin` trước `else` ở `:535`, chỉ khi `self.alt` đã được gắn;
    - gợi ý cho lệnh altcoin gõ trần;
    - dòng HELP có điều kiện;
    - `send` trả `bool` (mọi phần đã gửi); `broadcast(text, chat_ids=None, on_fail=None)` gọi `on_fail` cho mỗi chat Telegram lỗi và chuyển nó cho `zoom.send(on_fail=…)`;
    - `alt_broadcast(text)` mới: xét công tắc `altcoin` (tắt thì đếm `muted`), rồi gọi `broadcast` với `ALTCOIN_REPORT_CHAT_IDS` và bộ đếm `send_failed`;
    - `zoom_command` đưa người gửi thật vào `message["from"] = {"id": "zoom", "jid": <user_jid>, "chat": <to_jid>}` (cho xác nhận hai bước, §7.2). `ZOOM_USER` và mọi phép so `user_id == ZOOM_USER` giữ nguyên.
  - `vnbot/zoom.py`: `handle` truyền `sender_jid`/`reply_jid` cho handler (tham số tuỳ chọn, handler cũ vẫn chạy); sửa docstring `:9-12`, `:19-20` (bỏ "mượn app OKX" / "app thứ hai").
  - `vnbot/channels.py`: `NAMES` thêm `"altcoin"`, cập nhật docstring.
  - `vnbot/web.py`: dòng "Altcoin · thông báo" trong thẻ "Kênh thông báo"; thêm `id="kenh"` (sửa lỗi nhỏ: redirect `/ai#kenh` trỏ vào chỗ không có).
  - `vnbot/settings.py`: `altcoin_report_chat_ids = _ids("ALTCOIN_REPORT_CHAT_IDS")`, `altcoin_admin_ids = _ids("ALTCOIN_ADMIN_IDS")` (trống = `TELEGRAM_ALLOWED_IDS`).
  - `vnbot/pages.py`: dòng `alt <lệnh>` trong bảng `COMMANDS` của `/docs`, chỉ khi altcoin bật.
  - `vnbot/ui.py`, `vnbot/boot.py`, `vnbot/eventlog.py`, `vnbot/version.py`, `vnbot/__init__.py` (docstring), `requirements.txt`, `README.md`.
- **Mới:**
  - `vnbot/hub.py`, `vnbot/altcoin_host.py`, `vnbot/altcoin_bridge.py`.
  - `vnbot/webguard.py`. Đây là **refactor code bảo mật**, không phải dời nguyên: các closure `password_in`/`guard`/`same_origin` (`web.py:474–532`) được tách thành hàm thuần `check_basic(header, expected, throttle, client)` và `same_origin_ok(headers, allowed_hosts)`. `create_app` uỷ quyền cho chúng và giữ `app.state.login_throttle`.

---

## 2. Cây thư mục đích

```
/  (traique/stockvn)
├── main_botkeep.py                 Hub + supervisor + cầu nối kênh + kiểm Python 3.12 + handler tín hiệu
├── vnbot/                          stock + hub.py, altcoin_host.py, altcoin_bridge.py, webguard.py (mới)
├── tradingagents/                  giữ nguyên
├── requirements.txt                stock + "ccxt==4.5.90" + "httpx>=0.27"
├── requirements-dev.txt            giữ nguyên
├── pytest.ini                      MỚI: testpaths = tests ; python_files = test_suites.py
├── .env.example                    stock + mục "ALTCOIN (ALT_* / ALTCOIN_*)"
├── .gitignore                      stock + bot.env, .bt_cache/, bt_result*.json, !altcoin/dist/
├── check_db.py                     stock, giữ nguyên
├── README.md                       stock + mục "Hai phần, một bot" + sửa dòng cũ + Python 3.12
├── LICENSE, BAO_CAO_RA_SOAT_2026-10-23.md
├── scripts/build_botkeep_bundle.py MỚI
├── docs/ALTCOIN_TRONG_STOCKVN.md   MỚI
├── tests/  run_all.py, test_suites.py, fixtures/, suites/ (22 stock + 6 mới:
│           suite_bundle, suite_altcoin_scripts, suite_altcoin_host, suite_altcoin_proxy,
│           suite_altcoin_web, suite_altcoin_bridge)
└── altcoin/                        git subtree từ altcoin_bot (8 commit) + nhánh ccr-46f71b1d-mqcst3
    ├── bot/                        21 .py, KHÔNG sửa (build 380dfca)
    ├── run.py                      giữ (chế độ độc lập/VPS; nằm trong fingerprint)
    ├── hosted.py                   MỚI (ngoài fingerprint)
    ├── test_engine.py test_learner.py test_risk.py test_smoke.py test_strategy.py test_web.py
    ├── tools/ kronos_analyze.py kronos_eval.py requirements-kronos.txt README.md backtest_sessions.py
    ├── README.md (+5 dòng đầu, bỏ §3b Render)  .env.example, botkeep.env (khối Render ghi "không dùng")
    ├── check_db.py (sửa)  requirements.txt (ghim ccxt)  .gitignore
    └── dist/okx-ai-bot-v3.2.0.zip  tracked; không bao giờ vào gói Botkeep
    (render.yaml: đã xoá khỏi cây – vẫn còn trong lịch sử git)
```

| File altcoin | Vị trí sau gộp | Ghi chú |
|---|---|---|
| `bot/*.py` (21) | `altcoin/bot/` | Không sửa. `version.py` hash theo `parents[1]` = `altcoin/`, nên build vẫn `380dfca`. |
| `run.py` | `altcoin/run.py` | Phải ship cùng `bot/` để fingerprint không đổi. Hosted mode không chạy nó. |
| `requirements.txt` | `altcoin/requirements.txt` | Bộ gọn cho chế độ độc lập. `ALTCOIN_AUTO_INSTALL` dùng lại `ensure_deps` (`run.py:32-38`), mà hàm này cài từ file này. Đổi `ccxt>=4.4` thành `ccxt==4.5.90`. |
| `test_*.py` (6) | `altcoin/test_*.py` | Giữ chỗ để `from bot…` vẫn chạy. Chạy qua `suite_altcoin_scripts`. |
| `tools/*` (4) + `backtest_sessions.py` | `altcoin/tools/` | Chỉ dùng offline, không đóng gói. |
| `render.yaml` | **xoá** (`git rm`, commit riêng) | Không dùng Render (quyết định 3). Lấy lại khi cần: `git show <commit-subtree-add>^2:render.yaml`. |
| `botkeep.env` | `altcoin/botkeep.env` | Mẫu cho chế độ độc lập. Chỉ sửa khối "Chạy song song Render + Supabase" (`:58-60`): ghi "không dùng – đã bỏ Render", bỏ `PEER_URL`. Docs ghi hai cảnh báo: hồ sơ rủi ro cao (`RISK_PER_TRADE=0.10`); và trong hosted mode, các khoá `TELEGRAM_*`/`ZOOM_*` của file này bị bỏ qua. |
| `check_db.py` | `altcoin/check_db.py` | Sửa để nạp `.env`/`bot.env` **cạnh nó** và ưu tiên `ALT_DATABASE_URL`. Nếu không, `load_dotenv()` sẽ đi ngược lên `.env` gốc của stock và kiểm nhầm DB stock. |
| `README.md` | `altcoin/README.md` | Thêm 5 dòng đầu trỏ tới docs. **Bỏ §3b** "Chạy song song trên Render free + Botkeep" (`:66-89`), thay bằng 2 dòng: Render đã bỏ, `render.yaml` lấy lại bằng `git show <commit-subtree-add>^2:render.yaml`; phần tạo Supabase (Bước 1) chuyển sang docs. Bỏ dòng `render.yaml` trong danh sách file (`:205`). |
| `.env.example`, `.gitignore` | `altcoin/` | Khối "CHẠY SONG SONG RENDER + BOTKEEP" của `.env.example` (`:109-120`) đổi tiêu đề thành "Postgres dùng chung (tuỳ chọn)"; bỏ `PEER_URL`/`SELF_URL` và chú thích Render. `.env.example` **không vào gói Botkeep** (không ai đọc nó; nó quảng cáo các khoá bị bỏ qua ở hosted mode). `.gitignore` lồng nhau là hợp lệ. |
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
git rm altcoin/render.yaml && git commit -m "altcoin: bỏ render.yaml – không dùng Render (vẫn còn trong lịch sử git)"
# mọi sửa nội dung để ở các commit SAU
```

- **Kiểm tra:**
  - `git log --oneline <commit-subtree-add>^2 | wc -l` = 8;
  - `git log --oneline <commit-subtree-add>^2 <commit-subtree-merge>^2 | wc -l` = 10 (riêng `<commit-subtree-merge>^2` là 7: nhánh tách từ `9c1a566`);
  - `git log <commit-subtree-add>^2 -- bot/main.py` thấy 2 commit (v3.1.0, v3.2.0);
  - `git show <commit-subtree-add>^2:render.yaml` vẫn in được blueprint cũ.
- **Cách khác:** `git filter-repo --to-subdirectory-filter altcoin` cho lịch sử liền mạch, nhưng đổi mọi SHA và mất `subtree pull`. Không khuyến nghị.
- **Cuối Giai đoạn 6** (sau 1–2 tuần ổn định): đẩy một commit README "đã chuyển sang traique/stockvn" lên repo cũ, rồi **archive** repo đó, không xoá.

---

## 4. Bản đồ URL: hai mục tách biệt

**Nguyên tắc.** Stock ở `/` và giữ nguyên mọi URL. Altcoin ở **`/altcoin/`**, bắt buộc có `/` cuối vì dashboard altcoin dùng link tương đối. Không dùng `/bot` được vì stock đã có `/bot*`. Mọi đường Zoom và trang công khai chỉ còn ở phía stock, vì chỉ có một app Zoom.

| Đường dẫn | Chủ | Đăng nhập | Ghi chú |
|---|---|---|---|
| `/`, `/taisan*`, `/ai`, `/thamdinh*`, `/chonbong`, `/tinhieu`, `/dulieu*`, `/bot*`, `/caidat*`, `/phienban`, `/jobs/*`, `/reports/*`, POST `/critic /analyze /quick /watchlist /channels/*` | Stock | Basic + same-origin | Giữ nguyên. `/channels/altcoin` là công tắc mới (§7.3). |
| `/health` | Stock | công khai | Mã trạng thái chỉ phụ thuộc stock. JSON chỉ thêm `altcoin{state}`. |
| `/altcoin/hub.json` | Hub | **Basic** | Toàn bộ `state()` (§1): `reason`, `version`, `build`, `leader`, `mode`, `seam_ok`, `restarts`, `bridge`, `dropped_secrets`, `ignored_keys` (chỉ tên khoá), `rss_mb{stock, altcoin}`. Không token. Không tới child. |
| `/static/{favicon,logo}.png` | Stock | công khai | giữ nguyên |
| `/docs /privacy /terms /support /data-deletion /zoom /zoom/install /zoom/oauth/callback /zoom/pair /zoom/unpair`, POST `/webhook/zoom`, `/zoom/bot` | Stock | như hiện nay | **App Zoom dùng chung.** URL không đổi; nhận cả lệnh `alt …`. |
| `/altcoin` | Hub | công khai | 308 → `/altcoin/` |
| `/altcoin/`, `/altcoin/ai`, `/altcoin/caidat`, `/altcoin/phienban` | Altcoin | Basic | Ở `/caidat`, hai thẻ kênh được thay bằng thẻ "Kênh do Stock quản lý" (§7.5). |
| `/altcoin/api/state`, `/api/events`, `/api/scans`, `/api/export/{name}`, POST `/api/learn/{a}` | Altcoin | Basic (+ same-origin cho POST) | |
| `/altcoin/login`, `/altcoin/logout` | Altcoin | Basic | Vô hại: cookie luôn tắt (quyết định 1). |
| `/altcoin/health` | Altcoin, hoặc Hub khi child chưa chạy | **Basic** | JSON gốc của altcoin, hoặc 503 `{"ok":false,"state":…}`. Không còn công khai: lý do cũ là keepalive của Render, và khối `altcoin` đã có trong `/health` của stock. |
| GET `/altcoin/zoom`, `/altcoin/{docs,privacy,terms,support,data-deletion}` | Hub | công khai | 308 sang `/zoom`, `/docs`… của stock. Link chân trang của altcoin (`bot/ui.py:286`) vẫn dẫn tới đúng chỗ. |
| `/altcoin/_hub`, `/altcoin/_hub/*`, `/altcoin/zoom/{install,oauth/callback,pair,unpair}`, POST `/altcoin/{webhook/zoom, zoom/bot, caidat/kenh/*, caidat/zoomtest}` | Hub | — | **404**, trả trước cả bước auth và không bao giờ tới child. |

Các route trùng tên: bản ở gốc thuộc stock, bản altcoin chuyển sang `/altcoin/<cùng tên>`. Riêng các route Zoom và trang công khai chỉ còn bản ở gốc.

### Chuẩn hoá đường dẫn ở Hub (bắt buộc)

- httpx tự chuẩn hoá dấu chấm, nên `/altcoin/health/../api/export/all.json` sẽ thành `/api/export/all.json`.
- Vì vậy bước **đầu tiên** cho mọi `/altcoin*` là kiểm đường dẫn thô (`raw_path`, trước khi giải mã). Hub trả **400** khi có:
  - đoạn `.` hoặc `..`;
  - `//`;
  - `\`, hoặc `%5c`;
  - `%2e`, `%2f`, hoặc bất kỳ `%XX` nào giải mã ra ký tự không cần mã hoá (chữ, số, `-._~`), ví dụ `/altcoin/%5Fhub/command`. Không phân biệt hoa thường.
- Sau bước đó, danh sách chặn (404), chuyển hướng (308), `hub.json` và công khai đều so **chính xác** theo method + đường dẫn **đã giải mã** (`scope["path"]`). Riêng `_hub` chặn khi đường dẫn bằng `/altcoin/_hub` hoặc bắt đầu bằng `/altcoin/_hub/`.
- Hub chuyển tiếp đường dẫn thô đã qua kiểm (bỏ tiền tố `/altcoin`), nên child giải mã ra đúng đường dẫn Hub đã so.

### Thích nghi web trong `hosted.py` (không sửa `bot/`; `suite_altcoin_web` ghim)

1. `cfg.PUBLIC_URL` lấy `ALT_PUBLIC_URL`; nếu trống thì lấy `PUBLIC_URL` của stock + `/altcoin`. Giá trị này giờ chỉ dùng cho link tuyệt đối (nếu có). OAuth, URL Marketplace và link ghép đôi `notify.py:450` của altcoin không còn dùng.
2. Bọc `ui.page`, theo đúng thứ tự:
   1. gọi `ui.page` gốc;
   2. viết lại tiền tố trên HTML đó: `re.sub(r'\b(href|action|formaction)="/(?!/)', r'\1="/altcoin/', html)`. Các link `https:`, `mailto:`, `zoommtg:` không bị đụng;
   3. đổi dấu `="@stock/` thành `="/`. Mọi link sang stock do `hosted.py` chèn **bên trong body** (thẻ "Kênh do Stock quản lý" ở §7.5, banner seam) viết là `href="@stock/ai#kenh"`, `href="@stock/zoom"`, nên bước 2 không đụng tới;
   4. chèn thanh chuyển mục (`href="/"`, `href="/altcoin/"`) ngay sau `<div class="top-in">`, **sau** bước viết lại.
3. Thay hai thẻ kênh ở `/caidat` (§7.5).
4. Thêm route `/_hub/command` và middleware hai token (§1 bước 7, §7.2).

So với bản 1 đã bỏ: bọc `pages.base_url`, `pages.page` và `pages.RedirectResponse`. Lý do là mọi trang của `bot/pages.py` (Zoom, OAuth, Marketplace) giờ bị Hub chặn hoặc chuyển sang stock. Bớt được ba seam.

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
  - Nhãn Altcoin hiện trạng thái: "(đang khởi động)", "(tắt)", "(lỗi)", "(chờ instance khác)".
- **Phía altcoin:** do `hosted.py` chèn.
- **Hai mục vẫn tách bạch:** brand riêng ("TradingAgents VN" / "OKX Altcoin Bot"), nav, pill phiên bản, footer, bảng màu, khoá theme (`ta-theme` / `okx-theme`).

### URL và tài khoản bên ngoài (HOST = tên miền server stock)

| Ở đâu | Việc cần làm |
|---|---|
| App Zoom dùng chung, health check Botkeep, link Telegram của stock | App chung là `vnbot` (mặc định): **không đổi**, không phải đăng ký lại URL Zoom nào. App chung là `okxbot` (ngoại lệ ở §17, mục 4 (Còn cần chốt)): trỏ lại URL của nó về stock lúc cut-over (§7.6). |
| App Zoom `okxbot` | **Nếu app chung là `vnbot`:** ngừng dùng; sau 1–2 tuần ổn định gỡ Bot endpoint/Event Subscription hoặc deactivate app. **Nếu app chung là `okxbot`:** không bao giờ deactivate. Không bao giờ đăng ký URL `/altcoin/...` nào. |
| Bot Telegram OKX (token cũ của altcoin) | Ngừng dùng. Gửi một tin chuyển đổi bằng token này (§7.6). Tuỳ chọn revoke ở BotFather **cuối** Giai đoạn 6, sau khi server cũ đã tắt hẳn và hết thời gian có thể rollback. |
| Render (service `okx-ai-bot`) | Tạm dừng **trước** cut-over (Giai đoạn 6 bước 1). Render không phải đường rollback. Khi Giai đoạn 6 đạt kiểm tra chấp nhận: chép giá trị env còn cần từ Render (nếu có), rồi **xoá service và ngắt kết nối repo** ngay. Không nối Render sang stockvn. |
| Tên miền cũ `okx.bot-keep.xyz` (server Botkeep cũ, không liên quan Render) | Tuỳ chọn `ALTCOIN_LEGACY_HOSTS`. Chỉ GET/HEAD có đường dẫn **chưa** bắt đầu bằng `/altcoin` mới nhận 308 sang URL **tuyệt đối** `<gốc PUBLIC_URL>/altcoin<path>`, nên không lặp vòng. POST trả 410 kèm gợi ý. Chỉ làm nếu trỏ được tên miền này về server mới; nếu không thì bỏ cả code. |

---

## 5. Đăng nhập và CSRF

**Đã chốt (quyết định 1): một lần đăng nhập.** Basic `WEB_PASSWORD` được kiểm ở Hub trước khi proxy `/altcoin/*`. Đăng nhập cookie của altcoin vẫn còn trong code nhưng **luôn tắt**: `DASHBOARD_*` luôn bị xoá, và `ALT_DASHBOARD_*` bị bỏ qua. Lý do:

- Altcoin hiện "mở toang" khi thiếu mật khẩu, kể cả `/api/export` và `/api/learn`.
- Altcoin so mật khẩu bằng `==` và không có throttle.

Cụ thể:

- **Throttle dùng chung.** Một `LoginThrottle` (sai 8 lần / 600 s thì khoá 300 s) dùng cho cả Hub và `create_app(..., throttle=…)`. Tham số mới mặc định tạo instance mới, nên test không đổi.
- **Danh sách công khai dưới `/altcoin`:** chỉ còn GET/HEAD `/altcoin` (308) và các 308 sang trang công khai của stock (§4). Không còn webhook nào dưới `/altcoin`. `/altcoin/hub.json` cần Basic.
  - Mọi đường dẫn khác: `WEB_PASSWORD` trống → 503; đang bị khoá → 429 kèm `Retry-After`; sai hoặc thiếu → 401 `WWW-Authenticate: Basic`.
- **CSRF.**
  - Mọi method khác GET/HEAD tới `/altcoin/*` phải qua `same_origin_ok`, **không có ngoại lệ** (bản 1 miễn cho hai webhook Zoom, nay đã bỏ). Thứ tự kiểm: Origin, `Sec-Fetch-Site`, Referer, so với Host, `X-Forwarded-Host` đầu tiên và host của `PUBLIC_URL`.
  - Che được cả trường hợp các app khác cùng `*.bot-keep.xyz`.
- **Vệ sinh proxy:**
  - Bỏ `Authorization`, `Proxy-Authorization`, header hop-by-hop, và **mọi header `X-Hub-*` client gửi lên**.
  - Đặt lại `X-Forwarded-For/-Host/-Proto` (`-Proto` lấy scheme mà uvicorn của stock đã xác định), thêm `X-Forwarded-Prefix: /altcoin` và `X-Hub-Token`. Hub **không bao giờ** gửi `X-Hub-Cmd-Token`.
  - `setdefault` `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: same-origin`.
  - Body ≤ 64 KB (vượt thì 413). Stream phản hồi bằng `aiter_raw`.
  - httpx dùng `trust_env=False`, connect 2 s, read 120 s, ≤ 8 request đồng thời (vượt thì 503).
- **Chống vòng qua localhost.** Middleware hai token trong child (§1 bước 7). Cổng của child bind `127.0.0.1`, nên proxy Botkeep không chuyển tới được.
- **Cầu nối kênh không thêm route web nào ở stock.** Lệnh `/alt` dùng quyền của Telegram/Zoom (§7.2). Thông báo đi qua pipe (§7.4), nên không có địa chỉ nào để gọi từ ngoài.
- **Đối chiếu.** Cùng một bộ ca auth và CSRF chạy với cả Hub lẫn `create_app`, kết quả phải giống nhau.
- **Chưa kiểm:**
  - có bị hỏi mật khẩu lần hai trên Chrome và Safari không (tệ nhất là hỏi lại cùng mật khẩu);
  - proxy Botkeep có gộp IP client không. Nếu có, một lần bị khoá sẽ khoá cả hai mục; khi đó đặt `FORWARDED_ALLOW_IPS`.

---

## 6. Tách biến môi trường / cấu hình

**Env của child được dựng riêng:**

- Allowlist hệ thống: `PATH HOME LANG LC_* TZ TMPDIR PYTHONPATH PYTHONHOME PYTHONUSERBASE PYTHONNOUSERSITE VIRTUAL_ENV LD_LIBRARY_PATH HTTP(S)_PROXY http(s)_proxy NO_PROXY REQUESTS_CA_BUNDLE SSL_CERT_FILE SSL_CERT_DIR`. Không có `RENDER_*`.
- Luôn đặt `PYTHONIOENCODING=utf-8`, `PYTHONUTF8=1`, `HUB_TOKEN`, `HUB_CMD_TOKEN`, `HUB_FD`.
- Mỗi `ALT_<TÊN>` của cha được đổi thành `<TÊN>`, **trừ các khoá bị bỏ**: `ALT_TELEGRAM_*`, `ALT_ZOOM_*`, `ALT_PEER_*`, `ALT_SELF_URL`, `ALT_KEEPALIVE_INTERVAL`, `ALT_DASHBOARD_*`, `ALT_SUPPORT_EMAIL`. Các khoá này chỉ sinh WARNING và vào `ignored_keys`.
- Biến stock **không bao giờ** được chuyển, trừ các fallback ghi trong bảng. Đặc biệt `TELEGRAM_TOKEN`, `ZOOM_*` và `WEB_PASSWORD`: `/caidat` của stock đẩy chúng vào `os.environ` (`vnbot/config.py:293-299`), nên env của child phải dựng từ allowlist, không được sao `os.environ`.
- Giai đoạn 0 liệt kê **tên** biến env của tiến trình trên Botkeep để bổ sung allowlist nếu thiếu.

**Khoá chỉ supervisor đọc, không chuyển:** `ALT_MODE`, `ALT_I_UNDERSTAND_THE_RISK`, `ALT_LIVE_ACK`, `ALT_DATA_DIR`, `ALT_PUBLIC_URL`, `ALT_INSTANCE_NAME`. Ba khoá cuối được niêm phong qua argv.

**Khoá cấp host** (tiền tố `ALTCOIN_`, không bao giờ chuyển):

- Supervisor đọc: `ALTCOIN_ENABLED`, `ALTCOIN_AUTO_INSTALL`, `ALTCOIN_FRESH_START`.
- Hub đọc: `ALTCOIN_LEGACY_HOSTS`.
- Cầu nối đọc (qua `settings`): `ALTCOIN_REPORT_CHAT_IDS`, `ALTCOIN_ADMIN_IDS`.

Child chỉ đọc `altcoin/.env` và `altcoin/bot.env`. Có thể chép `bot.env` cũ vào `altcoin/bot.env` mà không đổi tên biến, theo thứ tự ưu tiên ở bảng dưới. Các khoá kênh, Render và đăng nhập riêng trong file đó bị bỏ qua (WARNING).

**Bí mật trên panel cũ không chép được.** Panel hiển thị dạng `__BOTKEEP_KEEP_SECRET_<TÊN>__`, nên phải nhập lại từ nguồn: OKX, Supabase, 9Router/OpenRouter. Không cần nhập lại token Telegram hay khoá Zoom của altcoin nữa.

**20 biến hai repo cùng đọc:**

| Biến (stock giữ nguyên) | Tên cho altcoin trên panel | Child nhận | Lấy giá trị stock khi trống? |
|---|---|---|---|
| `TELEGRAM_TOKEN` | — (`ALT_TELEGRAM_TOKEN` bị bỏ qua) | **luôn rỗng** | **Không bao giờ.** Bot dùng chung do stock giữ (§7). |
| `DATABASE_URL` | `ALT_DATABASE_URL` | `DATABASE_URL` (trống thì dùng SQLite) | **Không bao giờ.** Trùng URL hoặc DB có bảng stock thì thoát 78. |
| `DATA_DIR` | `ALT_DATA_DIR` | `DATA_DIR` tuyệt đối, niêm phong | mặc định `<DATA_DIR stock>/altcoin` |
| `PORT` | — | cổng tạm nội bộ, niêm phong | — |
| `SERVER_PORT`, `APP_PORT` | — | bị xoá (`DASHBOARD_PORT` xoá; `DASHBOARD_HOST=127.0.0.1`) | — |
| `PUBLIC_URL` | `ALT_PUBLIC_URL` (ví dụ `https://HOST/altcoin`) | niêm phong | `PUBLIC_URL` stock + `/altcoin` |
| `SUPPORT_EMAIL` | — (`ALT_SUPPORT_EMAIL` bị bỏ qua) | không | Không cần: `/altcoin/privacy` và `/support` chuyển sang trang stock. |
| `OPENROUTER_API_KEY` | `ALT_OPENROUTER_API_KEY` | `OPENROUTER_API_KEY` | có (ghi log: dùng chung hạn mức free) |
| `ZOOM_WEBHOOK_URL`, `ZOOM_WEBHOOK_TOKEN`, `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`, `ZOOM_BOT_JID`, `ZOOM_SECRET_TOKEN`, `ZOOM_BOT_SECRET_TOKEN`, `ZOOM_VERIFICATION_TOKEN`, `ZOOM_ACCOUNT_ID`, `ZOOM_TO_JID`, `ZOOM_ALLOWED_USERS` (11) | — (`ALT_ZOOM_*` bị bỏ qua) | **luôn rỗng** | **Không bao giờ.** App Zoom dùng chung do stock giữ. |

**Biến chỉ altcoin có, hoặc cùng mục đích nhưng khác tên:**

| Altcoin độc lập | Tên trên panel (gộp) | Mặc định / fallback |
|---|---|---|
| `MODE` | `ALT_MODE` (chỉ supervisor đọc) | `paper` |
| `I_UNDERSTAND_THE_RISK` | `ALT_I_UNDERSTAND_THE_RISK` (chỉ supervisor đọc; `hosted.py` tự đặt) | — |
| (mới) | `ALT_LIVE_ACK` = tên instance | — |
| `OKX_API_KEY`, `OKX_API_SECRET`, `OKX_PASSPHRASE`, `OKX_DEMO` | `ALT_OKX_*` (live chỉ nhận từ panel) | trống |
| `TELEGRAM_CHAT_ID` | — (bỏ qua). Thay bằng `ALTCOIN_REPORT_CHAT_IDS` do **stock** đọc, nhận `,`/`;` và id âm của nhóm. | trống = `TELEGRAM_REPORT_CHAT_IDS` của stock |
| `TIMEZONE`, `LOG_LEVEL` | `ALT_TIMEZONE`, `ALT_LOG_LEVEL` | `Asia/Ho_Chi_Minh`, `INFO` |
| `INSTANCE_NAME` | `ALT_INSTANCE_NAME` (niêm phong) | `botkeep`; cut-over dùng `crypto` |
| `LEADER_TTL` | `ALT_LEADER_TTL` | 90 s (vẫn dùng cho lock và chặn một instance) |
| `PEER_URL`, `SELF_URL`, `KEEPALIVE_INTERVAL` | — (bỏ qua; ép rỗng) | keepalive không chạy |
| `NINEROUTER_BASE_URL`, `NINEROUTER_API_KEY` | `ALT_NINEROUTER_*` | `ROUTER9_BASE_URL`, `ROUTER9_API_KEY` |
| `NINEROUTER_MODELS` | `ALT_NINEROUTER_MODELS` | `auto` |
| `CUSTOM_*` | `ALT_CUSTOM_*` | không có fallback |
| `DASHBOARD_USER`, `DASHBOARD_PASS` | — (bỏ qua; luôn xoá) | đăng nhập chung `WEB_PASSWORD` |
| Khoảng 100 tham số chiến lược / AI / rủi ro / học | `ALT_<TÊN>` hoặc để trần trong `altcoin/bot.env` | mặc định trong code |
| (host) | `ALTCOIN_ENABLED` / `ALTCOIN_AUTO_INSTALL` / `ALTCOIN_FRESH_START` / `ALTCOIN_LEGACY_HOSTS` / `ALTCOIN_REPORT_CHAT_IDS` / `ALTCOIN_ADMIN_IDS` | `0` / `0` / `0` / trống / trống / trống = `TELEGRAM_ALLOWED_IDS` |

**Thứ tự ưu tiên khi có `altcoin/bot.env`:**

| Khoá | Giá trị thắng | Ghi chú |
|---|---|---|
| `INSTANCE_NAME`, `PUBLIC_URL`, `DATA_DIR`, `PORT`/`DASHBOARD_*PORT/HOST` | Niêm phong (argv) | `bot.env` khác thì WARNING |
| `MODE`, `I_UNDERSTAND_THE_RISK` | Supervisor (argv) | `bot.env` khác thì ERROR |
| `OKX_*` | live: panel `ALT_OKX_*`; paper: bị xoá | Key chỉ có trong `bot.env` thì vẫn chạy paper (ERROR) |
| `TELEGRAM_*`, `ZOOM_*`, `PEER_URL`, `SELF_URL`, `DASHBOARD_USER/PASS` | **Luôn rỗng** | `bot.env` có thì WARNING + `ignored_keys` |
| `DATABASE_URL` | Panel `ALT_DATABASE_URL`, nếu không có thì `bot.env` | Mọi kiểm tra ở §1 bước 5 chạy trên giá trị hiệu lực |
| Tham số còn lại | `bot.env` (file-wins như altcoin gốc) | |

**Bảy lớp khoá chống vô tình chạy `MODE=live`:**

1. **Supervisor** chỉ truyền `--mode live` khi đúng **tất cả** các điều kiện sau trong env thật của tiến trình cha:
   - `ALT_MODE=live`;
   - `ALT_I_UNDERSTAND_THE_RISK=yes`;
   - `ALT_LIVE_ACK` bằng `ALT_INSTANCE_NAME`;
   - đủ ba `ALT_OKX_API_KEY/SECRET/PASSPHRASE`.

   Sai bất kỳ điều kiện nào thì chạy paper, kèm ERROR và dòng đỏ trên `/altcoin/`. Điều kiện về peer của bản 1 đã bỏ, vì không còn peer.
2. **`hosted.py` ghi đè `cfg.MODE`**, chạy lại hai kiểm tra của `main()` và thoát 78 nếu sai.
3. **Paper:** xoá `cfg.OKX_*` và `I_UNDERSTAND_THE_RISK`, nên không gọi được endpoint riêng tư nào.
4. **Một instance duy nhất:** `hosted.py` thoát 75 nếu `heartbeats` có instance khác còn sống trong `LEADER_TTL`. Không còn peer, nên không còn chuyện một instance paper "đóng" lệnh thật trong DB. Chặn này bắt nốt server altcoin cũ hoặc Render chưa tắt.
5. **Web và chat không đổi được chế độ:**
   - `config.FIELDS` không có `ALT_*` hay `MODE` (có test kiểm);
   - `MODE` trần trên panel bị bỏ và chỉ cảnh báo;
   - cầu nối `/alt` không có lệnh nào đổi chế độ (allowlist, §7.2).
6. **Gói không mang cấu hình live:** bộ đóng gói từ chối `.env`, `bot.env`, `*.env`.
7. **Test ma trận** trong `suite_altcoin_host`, chạy tới ngay trước `Bot()` (stub). Các ca phải ra paper, disabled hoặc 75:
   - `bot.env` có `MODE=live` + xác nhận + key;
   - thiếu xác nhận;
   - thiếu `ALT_LIVE_ACK`;
   - key chỉ nằm trong `bot.env`;
   - heartbeat của instance khác còn sống (75, cả khi mọi điều kiện live đều đủ).

   Khi đủ mọi điều kiện, kể cả lúc `bot.env` có `I_UNDERSTAND_THE_RISK=no`: chọn `LiveEngine` và qua cả hai kiểm tra của `main()`.

**Chỉ bật live sau mục "vùng găng" ở Giai đoạn 7**, vì SIGTERM có thể rơi vào giữa lúc đặt lệnh và lúc ghi DB.

---

## 7. Telegram và Zoom: một bot, một app, cầu nối `/alt`

### 7.1 Nguyên tắc

- **Chỉ tiến trình stock nhận.**
  - Thread `telegram-bot` là nơi duy nhất gọi `getUpdates` (`allowed_updates=["message"]`; altcoin không dùng nút inline hay `callback_query`, nên không mất gì).
  - `POST /webhook/zoom` của stock là endpoint duy nhất của app Zoom.
- **Child không có token kênh nào** (§1 bước 3). Nhờ vậy cơ chế "chỉ gửi" (`--deny-sha`) và mọi phân tích 409 giữa hai poller của bản 1 bị bỏ.
- **Một chính sách.** Mọi tin altcoin ra ngoài đều đi qua `TelegramBot.send`/`broadcast` của stock: cùng `redact()`, cùng cắt tin 3.900 ký tự (Telegram) / 4.096 (Zoom), cùng nghỉ 30 phút khi Zoom lỗi liên tiếp, cùng công tắc.
- **"Giữ đủ mọi thứ"** nghĩa là mọi tính năng kênh của altcoin vẫn dùng được qua bot chung (§7.5), không phải nhân đôi đường ống.

### 7.2 Lệnh: `/alt <lệnh>`

**Cú pháp.** `/alt <lệnh>`, kèm bí danh `/altcoin <lệnh>`. `/alt` đứng một mình hoặc `/alt help` hiện bảng lệnh altcoin do stock dựng (không gọi child).

- Telegram: `/alt status`, hoặc `/alt@<TênBot> status` (stock đã cắt `@…`).
- Zoom, chat 1:1: gõ `alt status`. Stock tự thêm `/` (`vnbot/zoom.py:509`).
- Zoom, trong kênh: gõ `/<slash command của app chung> alt status`, tức `/vnbot alt status`, hoặc `/okxbot alt status` nếu app chung là `okxbot`.

**Kiểm trùng tên (đã làm).**

- Không có trùng:
  - 59 tên lệnh của stock (`_handle` `:494-536`, `TAISAN_COMMANDS` `:143-145`) không có `/alt`, `/altcoin`, và không tên nào bắt đầu bằng `/a`.
  - Các trang web `/ai`, `/altcoin/` không phải lệnh Telegram.
- Trùng thật giữa hai bên chỉ có `phienban`, `version`, `zoompair`, `zoomxoa`, `zoomstatus` và `help`. Vì phía altcoin nằm sau tiền tố `/alt`, chúng không đụng nhau.
- Không thêm bí danh ngắn (`/a`, `/okx`):
  - một ký tự rất dễ gõ nhầm, mà `/a close` quá gần một lệnh nguy hiểm;
  - bí danh ngắn chặn tên lệnh stock sau này.

**Lệnh altcoin gõ trần** (`/status /pause /resume /trades /close /signals /ai /learn /apply /lessons /btc /zoomtest`) **không bao giờ được chuyển** sang altcoin.

- Khi cầu nối đang được gắn, stock trả gợi ý: "Lệnh Altcoin giờ gõ `/alt <lệnh>`, ví dụ `/alt status`." Như vậy `/close` trần không thể đóng vị thế.
- Khi không gắn (harness test stock), hành vi như cũ: trả help.

**Bảng lệnh (allowlist ở cả stock lẫn child):**

| Gõ | Xử lý | Timeout | Ghi chú |
|---|---|---|---|
| `/alt`, `/alt help`, `/alt <lạ>` | Stock trả bảng lệnh altcoin | — | Không gọi child. Lệnh lạ không bao giờ được chuyển. |
| `/alt status`, `trades`, `signals`, `ai`, `learn`, `lessons`, `btc` | Gọi child, chạy trên lane của chat | 15 s | Chỉ đọc |
| `/alt phienban`, `/alt version` | Gọi child | 15 s | Khác `/phienban` (phiên bản stock) |
| `/alt pause` | Gọi child | 15 s | An toàn, không cần xác nhận. Quyền ghi (xem **Quyền**). |
| `/alt resume` | **Hai bước** | 15 s | Xoá cả circuit breaker (`halted=False`) |
| `/alt apply` | **Hai bước**, thread `alt-cmd` | 30 s | Áp **mọi** đề xuất tự học đang chờ |
| `/alt close` | **Hai bước**, thread `alt-cmd` | 60 s | Đóng mọi lệnh. Khi live là lệnh thật trên OKX. |
| `/alt zoomtest` | Stock tự gửi tin "🪙 Altcoin › 🧪 Tin thử kênh chung" theo đúng công tắc và chat của thông báo altcoin (§7.3, không tính giới hạn nhịp), chạy trên lane của chat. Phần Zoom gọi `zoom.deliver(("notify", text, None))` **đồng bộ**, không qua hàng đợi. | — | Trả lời từng kênh: Zoom nhận / không nhận, mỗi chat Telegram OK / lỗi, kênh bị công tắc chặn |
| `/alt zoompair`, `zoomxoa`, `zoomstatus` | Stock trả: "Zoom dùng chung với Cổ phiếu: dùng /zoompair, /zoomxoa, /zoomstatus." | — | Không gọi child |

**Hai bước cho lệnh nguy hiểm** (`close`, `apply`, `resume`):

1. `/alt close` → "⚠️ Lệnh này đóng mọi vị thế altcoin đang mở (lệnh thật khi chạy live). Gõ `/alt close xacnhan` trong 120 giây để thực hiện."
2. `/alt close xacnhan` chỉ có hiệu lực khi **cùng khoá** (kênh, người gửi thật, cuộc trò chuyện) đã có yêu cầu chờ chưa quá 120 s. Mỗi yêu cầu chờ chỉ dùng một lần.
   - Telegram: (`from.id`, `chat.id`).
   - Zoom: (`user_jid`, `to_jid`), lấy từ `message["from"]` mà `zoom_command` điền (§1, file stock). **Không** dùng hằng `zoom`, vì mọi người Zoom được phép đều mang `from.id = "zoom"`.
3. Stock chỉ chuyển **tên lệnh** sang child; `xacnhan` và mọi tham số khác bị bỏ. Altcoin vốn chỉ đọc từ đầu tiên.

**Quyền.**

- **Lệnh đọc:** như mọi lệnh stock. Người gửi phải nằm trong `TELEGRAM_ALLOWED_IDS`; `_handle` đã chặn người lạ trước mọi nhánh (`:497-499`). Lệnh từ Zoom phải do người đã ghép đôi, hoặc người trong `ZOOM_ALLOWED_USERS`, gửi (kiểm ở `zoom._allowed`, trước `_handle`), và công tắc `zoom_commands` phải bật.
- **Lệnh ghi** (`pause`, `resume`, `close`, `apply`): thêm một lớp.
  - Telegram: user id phải nằm trong `ALTCOIN_ADMIN_IDS` (trống = `TELEGRAM_ALLOWED_IDS`).
  - Zoom: chỉ JID **đã ghép đôi** (`pairing().jid`), không phải mọi mục của `ZOOM_ALLOWED_USERS`.
  - Không đủ quyền: "Lệnh ghi Altcoin chỉ dành cho ALTCOIN_ADMIN_IDS / tài khoản Zoom đã ghép đôi."
- Trong nhóm: người được phép gõ được, câu trả lời vào nhóm. Người lạ nhận câu từ chối của stock (tối đa 1 lần / 300 s mỗi chat).
- **Khác với altcoin cũ, theo cả hai chiều.** Docs phải ghi rõ:
  - **hẹp lại:** bot OKX xác thực theo **chat id**, nên ai trong nhóm `TELEGRAM_CHAT_ID` cũng ra lệnh được; nay chỉ user id được phép;
  - **rộng ra:** mọi người dùng stock (và mọi `ZOOM_ALLOWED_USERS`) đọc được altcoin, và nếu để trống `ALTCOIN_ADMIN_IDS` thì mọi người dùng stock trên Telegram đóng được vị thế altcoin, kể cả người chưa từng có quyền với bot OKX. Giai đoạn 6 đặt `ALTCOIN_ADMIN_IDS` khi `TELEGRAM_ALLOWED_IDS` có người không nên có quyền đó.
- Công tắc `telegram_cmd`/`zoom_cmd` trong kv của altcoin **không** còn được xét. Nếu DB chép sang đang để chúng tắt, sẽ thành trạng thái ẩn, không sửa được trên web.

**Gọi child.**

- `vnbot/altcoin_bridge.py` gửi `POST http://127.0.0.1:<port>/_hub/command`, body `{"cmd": "status", "source": "Telegram"|"Zoom"}`, header `X-Hub-Cmd-Token`.
- httpx với `trust_env=False`, connect 2 s, read theo bảng trên. Không đi qua Hub, nên không tính vào giới hạn 8 request.
- `port` và token lấy từ supervisor; chúng không nằm trong `state()`.
- `source` giữ đúng ngữ nghĩa altcoin, ví dụ `reason_close = "Đóng thủ công qua Telegram"`.

**Đồng thời.**

- Lệnh chỉ đọc chạy ngay trên lane của chat (3 lane, hàng chờ 50 dùng chung với stock).
- `close`/`apply` chạy trên thread `alt-cmd`, mỗi lần chỉ một lệnh. Lệnh thứ hai nhận "đang có lệnh Altcoin chạy". Trước đó trả "⏳ Đang gửi lệnh tới Altcoin…".
- **Khoá thật nằm trong child**, không ở stock. httpx không huỷ được route đang chạy, nên sau timeout 60 s stock nhả `alt-cmd` trong khi `close_all` cũ vẫn chạy. Nếu chỉ có khoá ở stock, một `/alt close xacnhan` thứ hai sẽ chạy `close_all` song song: cả hai đọc cùng `open_trades()`, cùng một trade bị `engine.close` hai lần (paper: cộng `paper_balance` hai lần; live: hai lệnh reduceOnly), `_finalize` ghi và báo hai lần. Vì vậy endpoint giữ một `threading.Lock` **không chặn** quanh `pause/resume/close/apply` (bước 4b bên dưới).
- **Không tự gửi lại** lệnh ghi khi lỗi hoặc timeout, vì các lệnh này không idempotent.

**Endpoint trong child** (`hosted.py`, route `def` thường, chạy trong threadpool của FastAPI):

1. Thiếu hoặc sai `X-Hub-Cmd-Token` → 403. Middleware đã chặn trước khi tới đây; `X-Hub-Token` một mình không đủ.
2. Chưa có `bot.start_equity` (route đã gọi được ngay trong `Bot.__init__`) → 503 "đang khởi động".
3. `cmd` ngoài allowlist `{status, trades, signals, ai, learn, lessons, btc, phienban, version, pause, resume, close, apply}` → 400.
4. `pause/resume/close/apply` khi `not bot.is_leader` → 409 `{"ok": false, "leader": false}` "chưa giữ quyền giao dịch".
   - 4b. Với bốn lệnh đó: `write_lock.acquire(blocking=False)`; không lấy được → 409 `{"ok": false, "busy": true}`. Khoá được nhả trong `finally`, chỉ khi `handle_command` thật sự xong, dù stock đã bỏ request từ lâu.
5. Gọi `bot.handle_command(cmd, source=source)`. Bắt mọi lỗi, trả `{"ok": true, "reply": str}` hoặc `{"ok": false, "error": <đã che>}`. DB đã được khoá toàn cục (`bot/storage.py:48-50`); Zoom vốn đã gọi `handle_command` ngoài thread chính.

**Câu trả lời khi child không sẵn sàng:**

| Tình huống | Trả lời |
|---|---|
| `off` / `disabled` (gồm `ALTCOIN_ENABLED=0`, thiếu thư viện) | "🪙 Altcoin đang tắt (`<lý do>`)." |
| `waiting` (mã 75) | "🪙 Altcoin đang chờ: instance khác còn chạy (`<tên>@<host>`). Dừng instance đó thì Altcoin tự chạy." |
| `starting`, `backoff`, kết nối bị từ chối, child trả 503 | "🪙 Altcoin đang khởi động, thử lại sau ít giây." |
| Child trả 409 `leader:false` | "🪙 Altcoin vừa khởi động, chưa nhận quyền giao dịch. Thử lại sau khoảng 1 phút." |
| Child trả 409 `busy:true` | "⏳ Đang có lệnh Altcoin chạy (lệnh trước chưa xong). Đừng gửi lại." |
| Timeout, lệnh đọc | "⏱ Altcoin chưa trả lời sau N s, thử lại sau." |
| Timeout, lệnh ghi | "⏱ Altcoin chưa trả lời sau N s. Lệnh **vẫn đang chạy** trong Altcoin (với `close`, kết quả từng lệnh đóng sẽ tới qua thông báo). Đừng gửi lại: lệnh ghi mới sẽ bị từ chối cho tới khi lệnh này xong." |
| Child báo lỗi | "⚠️ Altcoin lỗi: `<đã che>`" |

**Trả lời.**

- Dòng đầu là `🪙 Altcoin › <lệnh>`, sau đó nội dung qua `eventlog.redact()`, rồi qua **cùng bước viết lại gợi ý lệnh** như thông báo (§7.3 bước 5.3; ví dụ `learn_text` có "gõ /apply để áp dụng tất cả", `main.py:426`, thành `/alt apply`), rồi tới `self.send(chat_id, …)`.
- `send` đã cắt tin 3.900 ký tự cho Telegram, và chuyển `zoom:N` sang `zoom.reply` (cắt 4.096).
- `lessons` (≈3,3k) và `phienban` (≈1,9k) nằm gọn trong một tin.

**Trợ giúp.**

- `HELP` của stock thêm dòng "/alt <lệnh> – bot Altcoin (gõ /alt để xem lệnh)".
- `/docs` (`COMMANDS`, `vnbot/pages.py:177-203`) thêm dòng `alt <lệnh>`.
- Cả hai **chỉ khi `ALTCOIN_ENABLED=1`**, nên output và test stock mặc định không đổi.
- Không gọi `setMyCommands`.

### 7.3 Thông báo: child → pipe → stock → kênh của stock

**Đường đi:**

1. Altcoin `Notifier.send(text)` → kiểm leader (giữ nguyên) → các kênh `[HubChannel, Zoom (trơ, enabled=False)]`.
2. `HubChannel.send(text)` chỉ `put_nowait` vào hàng đợi (200; đầy thì bỏ và đếm) rồi trả về. Nó không bao giờ chặn và không bao giờ ném lỗi, nên engine không bị kẹt khi `close` gửi thông báo trong lúc lệnh HTTP vẫn đang mở.
   - `HubChannel` có `enabled = True`, `start()` không làm gì, và **không có** `switch_key`: công tắc `telegram_notify` trong kv của altcoin không còn tác dụng.
3. Thread `hub-notify` trong child là **nơi duy nhất ghi `HUB_FD`**; engine và main thread không bao giờ ghi.
   - Khung `notify`: `{"t":"notify","n":<số thứ tự>,"ts":…,"text":…}` + `\n`, dựng bằng `json.dumps(..., ensure_ascii=False)`. `text` được cắt theo **byte UTF-8** sao cho cả dòng ≤ 60 KiB (với `ensure_ascii=True`, 12.000 ký tự tiếng Việt/emoji có thể vượt 64 KiB và bị cha bỏ).
   - Khung `state` (schema ở §7.4) được **gộp**: chỉ giữ bản mới nhất, và ghi **trước** các khung `notify` đang chờ. Nhờ vậy một loạt thông báo không làm trạng thái cũ đi.
   - Dùng `os.write`, lặp tới khi ghi hết. `EPIPE`/`EBADF` thì bỏ và ghi log một lần.
4. Thread `altcoin-hub` của stock đọc từng dòng (≤ 64 KiB; dài hơn thì bỏ tới hết dòng và đếm). Dòng không phải JSON, hoặc `t` lạ, thì bỏ và đếm. Khung `state` thay `state()` ngay. Khung `notify` vào hàng đợi của `altcoin-notify` (200) bằng **`put_nowait`**: đầy thì bỏ và đếm `dropped`. Thread này không bao giờ chặn, nên luôn rút cạn pipe; một lần gửi Telegram bị treo (`_call` có thể ngủ tới 60,5 s mỗi lần 429) không làm đầy pipe 64 KiB và không làm child kẹt ở `os.write`.
5. Thread `altcoin-notify` xử lý theo thứ tự:
   1. che bằng `eventlog.redact()` (gồm bí mật altcoin, §9);
   2. **lọc** "👑 … tiếp quản" (vô nghĩa khi chỉ một instance; chỉ ghi log). "🚀 … khởi động" giữ, nhưng tối đa 1 tin / 30 phút;
   3. **viết lại gợi ý lệnh:** `/resume`, `/apply`, `/pause`, `/close`, `/status` đứng riêng thành `/alt …` (`main.py:257`, `learner.py:449`);
   4. **chống trùng:** cùng nội dung trong 120 s thì gửi một lần;
   5. **giới hạn nhịp:** token bucket **15 tin/phút, burst 5**, và mỗi lần gửi cách lần trước **≥ 1 s** (Telegram: khoảng 1 tin/s mỗi chat, 20 tin/phút mỗi nhóm; hạn mức tính theo bot nên còn chừa chỗ cho stock).
      - **Tin ưu tiên** (bắt đầu bằng `🛑` hoặc `⚠️`, ví dụ "🛑 DỪNG GIAO DỊCH … /resume", `main.py:257`) **không** bị bucket chặn và không bị gộp; vẫn qua chống trùng và giãn 1 s.
      - Phần thừa được đếm, và **toàn văn** (đã che) được ghi qua logger `vnbot.altcoin_bridge` (vào `events` của stock); mỗi 60 s có thừa thì gửi một tin "🪙 Altcoin › Bỏ qua N thông báo vì quá dày – xem /dulieu";
   6. thêm tiền tố `🪙 Altcoin › `;
   7. gửi tới **sink**.
6. **Sink** = `TelegramBot.alt_broadcast(text)`:
   - công tắc `altcoin` tắt thì bỏ và đếm `muted`;
   - nếu không, gọi `broadcast(text, chat_ids=settings.altcoin_report_chat_ids or None, on_fail=…)`.

   `broadcast` vẫn xét `zoom_notify` và `telegram`, gửi Zoom tới `notify_target()` (người đã ghép đôi hoặc `ZOOM_TO_JID`), và gửi mỗi chat Telegram **một lần** (tập đã sắp xếp, cắt 3.900). Mỗi chat Telegram gửi lỗi (`send` trả `False` sau khi `_call` hết lượt thử 429) và mỗi tin Zoom không kênh nào nhận (`on_fail` của `zoom.send`) tăng `send_failed` và ghi toàn văn đã che vào log cầu nối.

**Trước khi có sink.** Sink được gắn ở cuối `_start()`. Trước đó, hoặc khi `_start()` lỗi, cầu nối đệm tối đa 50 tin (bỏ tin cũ nhất). Mỗi tin cũng in `[altcoin-notify] …` (đã che) ra stdout. Supervisor vốn chờ stock sẵn sàng (≤ 60 s), nên bình thường không cần đệm.

**Công tắc** (thẻ "Kênh thông báo" trên `/ai`, có `id="kenh"`):

| Công tắc | Tác dụng lên thông báo altcoin |
|---|---|
| `altcoin` (**mới**, mặc định bật; chỉ hiện khi `ALTCOIN_ENABLED=1`, nếu không thì hiện `_UNSET_BADGE`) | Tắt thì không gửi gì của altcoin. Stock không đổi. |
| `telegram` | Như stock: tắt thì cả stock lẫn altcoin không gửi Telegram. Lệnh vẫn được trả lời. |
| `zoom_notify` | Như stock, áp cho cả hai |
| `zoom_commands` | Lệnh Zoom, gồm `alt …` |

Nghĩa là `telegram`/`zoom_notify` là công tắc **kênh** (chung hai mục), còn `altcoin` là công tắc **mục**.

**Mất tin.** Khi child hoặc stock restart, tin còn trong hàng đợi bộ nhớ có thể mất. Điều này chấp nhận được, nhưng dấu vết có giới hạn:

- `events` của altcoin chỉ giữ **200 ký tự đầu** của mỗi thông báo (`notify.py:36`), nên tin dài (tổng kết ngày, review AI) chỉ còn phần đầu.
- Tin bị cầu nối bỏ vì hàng đợi đầy, giới hạn nhịp hoặc gửi lỗi thì có **toàn văn** (đã che) trong `events` của stock (`/dulieu`, logger `vnbot.altcoin_bridge`).
- Tin bị công tắc `altcoin` chặn, hoặc bị chống trùng, chỉ được đếm.
- Các bộ đếm `notified/muted/dropped/deduped/send_failed` nằm trong `/altcoin/hub.json` (`bridge`).

### 7.4 Điểm nội bộ của cầu nối: chọn gì, vì sao

**Thông báo (child → stock) đi qua pipe kế thừa.**

| Cách | Có lộ ra Internet qua proxy Botkeep/Hub? | Xác thực | Chọn |
|---|---|---|---|
| Route mới trên cổng công khai, "chỉ nhận loopback" + token | **Có.** Proxy chuyển mọi đường dẫn tới cổng này. Nếu proxy chạy cùng máy thì request công khai cũng tới từ `127.0.0.1`; uvicorn lại tin `X-Forwarded-For` từ `127.0.0.1`, nên `client.host` giả được, và `Host` do client đặt. Stock bind `0.0.0.0`. Auth của stock là dependency từng route, nên một route quên `Depends` là lộ. | Chỉ còn token | Không |
| Listener riêng `127.0.0.1:<cổng tạm>` trong stock (stdlib `ThreadingHTTPServer`) | Không qua proxy, vì proxy chỉ tới `SERVER_PORT`. Nhưng mọi tiến trình cùng máy kết nối được. | Token mỗi lần spawn | Dự phòng |
| Unix socket `0600` trong `DATA_DIR` | Không | Quyền file + token | Không: child dùng `requests`, vốn không nói UDS, nên phải tự viết client; đường dẫn ≤ 107 byte |
| **Pipe kế thừa** (`os.pipe()` + `pass_fds`) | **Không có địa chỉ nào để gọi** | Chỉ child cầm đầu ghi, nên không cần token. Cha đóng bản sao đầu ghi ngay sau spawn. Ở cha fd không kế thừa (PEP 446), nhưng `pass_fds` làm nó **kế thừa được** trong child; `hosted.py` gọi `os.set_inheritable(HUB_FD, False)` và xoá `HUB_FD` khỏi env ngay khi đọc (§1 bước 1), nên tiến trình cháu không nhận. | **Chọn** |

Pipe là một chiều, đủ cho thông báo và khung trạng thái (thay `@@hosted`). Nó tự huỷ cùng child, và không cần code HTTP ở phía stock. Nếu Giai đoạn 3 (cục bộ) hoặc canary trên Botkeep (cuối Giai đoạn 5) thấy `pass_fds` có vấn đề, chuyển sang listener `127.0.0.1` với `HUB_CMD_TOKEN`; giao thức khung giữ nguyên.

**Giao thức khung** (mỗi khung một dòng JSON UTF-8, ≤ 60 KiB):

| Khung | Trường |
|---|---|
| `notify` | `{"t":"notify", "n":<số thứ tự>, "ts":<epoch>, "text":<str>}` |
| `state` | `{"t":"state", "state":"starting"\|"running"\|"waiting"\|"disabled", "reason":<str\|null>, "leader":<bool>, "mode":"paper"\|"live", "version":<str>, "build":<str>, "instance":<str>, "seam_ok":<bool\|null>, "ignored_keys":[<tên>], "dropped_secrets":[<tên>]}` |

Child gửi `state` lúc khởi động, mỗi khi một trường đổi (thread `hub-notify` đọc `bot.is_leader`, `cfg.MODE`… mỗi 5 s), và một lần cuối trước khi thoát 78/75 (có `reason`). Supervisor ghép các trường này vào `state()`; `pid`, `port`, `restarts`, `last_exit`, `bridge`, `rss_mb` do cha tự đo.

**Lệnh (stock → child) đi qua HTTP** tới cổng `127.0.0.1` sẵn có của child, vì cần hỏi–đáp và child đã chạy uvicorn. Có ba lớp chặn:

1. Hub trả 404 cho `/altcoin/_hub/*` trước mọi bước khác.
2. Hub xoá mọi `X-Hub-*` client gửi, và không bao giờ gắn `X-Hub-Cmd-Token`.
3. Child chỉ nhận `/_hub/*` khi có `X-Hub-Cmd-Token` đúng; `X-Hub-Token` của proxy không đủ.

Nhờ vậy, ngay cả người có `WEB_PASSWORD` cũng không chạy được `close` qua web. Dashboard altcoin vốn không có nút này.

### 7.5 Tính năng kênh của altcoin sau gộp

| Tính năng altcoin cũ | Sau gộp |
|---|---|
| Lệnh Telegram qua bot OKX | `/alt <lệnh>` trên bot chung (§7.2) |
| Lệnh Zoom qua app `okxbot` (`/okxbot status`) | `alt status` (1:1) hoặc `/vnbot alt status` (`/okxbot alt status` nếu app chung là `okxbot`) |
| Thông báo Telegram tới `TELEGRAM_CHAT_ID` | Tới `ALTCOIN_REPORT_CHAT_IDS`, trống thì các chat báo cáo của stock (§7.3) |
| Thông báo Zoom | Tới người được ghép đôi với app chung (một người nhận, hoặc `ZOOM_TO_JID`, hoặc Incoming Webhook của stock) |
| Kênh Zoom **riêng** của altcoin: `ZOOM_WEBHOOK_URL`/`TOKEN` (Incoming Webhook vào một kênh, `notify.py:64`, `:106-107`) hoặc `ZOOM_TO_JID` riêng | **Bỏ** (mặc định): tin altcoin đi theo Zoom của stock. Giai đoạn 0 kiểm kê; nếu đang dùng và phải giữ, thêm `ALTCOIN_ZOOM_WEBHOOK_URL`/`_TOKEN` do **stock** đọc và stock gửi (child vẫn không cầm gì) – quyết định ở §17, mục 20. |
| `zoompair`, `zoomxoa`, `zoomstatus` | `/zoompair`, `/zoomxoa`, `/zoomstatus` của stock, vì chỉ có một cặp ghép đôi |
| `zoomtest` (lệnh và nút trên `/caidat`) | `/alt zoomtest`: stock gửi tin thử, Zoom gửi đồng bộ nên báo được nhận hay không. Nút không còn: POST `/altcoin/caidat/zoomtest` → 404. |
| Công tắc `telegram_notify/telegram_cmd/zoom_notify/zoom_cmd` (`/altcoin/caidat`) | Thay bằng công tắc `altcoin`, `telegram`, `zoom_notify`, `zoom_commands` của stock trên `/ai`. POST `/altcoin/caidat/kenh/*` → 404. |
| Thẻ "Thông báo & tin nhắn" và thẻ "Zoom" trên `/altcoin/caidat` | **Giữ trang, thay thẻ.** `hosted.py` bọc `ui.card`: khi tiêu đề là "Thông báo & tin nhắn", hoặc "Zoom" kèm sub "Kiểm tra và gửi thử thông báo", thì thay bằng **một** thẻ "Kênh do Stock quản lý". Thẻ này có link tới `/ai#kenh` (công tắc) và `/zoom` (ghép đôi), viết dạng `@stock/…` để bước viết tiền tố không đụng (§4), và ghi rõ lệnh là `/alt …`. Seam mất thì chèn banner cùng nội dung ở đầu trang. |

**Mất mát chấp nhận** (ghi trong docs):

- `telegram_cmd` không có tương đương: stock không có công tắc tắt trả lời lệnh Telegram (`vnbot/channels.py:7-8`). Muốn chặn lệnh altcoin thì bỏ `ALTCOIN_ENABLED` hoặc thu hẹp `ALTCOIN_ADMIN_IDS` (chỉ chặn lệnh ghi).
- Không còn tổ hợp theo từng mục × từng kênh (ví dụ altcoin chỉ Telegram trong khi stock vẫn Zoom). Công tắc `telegram`/`zoom_notify` áp cho cả hai mục; `altcoin` chỉ bật/tắt cả mục.
- Kênh Zoom riêng của altcoin (dòng trên), trừ khi chọn giữ.
| `/altcoin/zoom` (trang cấu hình) | 308 → `/zoom` của stock |
| OAuth `/zoom/install`, `/zoom/oauth/callback`, webhook, trang Marketplace công khai | 404 hoặc 308 sang trang stock (§4). Không đăng ký lại URL nào. |
| Cảnh báo người gửi Zoom chưa ghép đôi, xử lý gỡ app | Do stock làm cho app chung (`alert_owner = broadcast`) |
| kv `zoom_pairing`, `zoom_notify_target`, `zoom_notify_ok`, `zoom_account_id`, `zoom_robot_jid`, `zoom_requests`, `zoom_pending`, `zoom_install`, `zoom_oauth_states`, `channels` | Nằm yên trong DB altcoin, **không chuyển**. Chúng thuộc app `okxbot`. Kể cả khi `okxbot` thành app chung, ghép đôi phải làm lại trên stock (`zoom_state.json`), xem §7.6. |

### 7.6 Chuyển người dùng sang bot chung (Giai đoạn 6)

- Người từng dùng bot OKX phải `/start` bot stock một lần, vì bot không nhắn trước được cho người chưa bắt đầu. User id của họ phải nằm trong `TELEGRAM_ALLOWED_IDS` thì mới ra lệnh được.
- Nếu `TELEGRAM_CHAT_ID` cũ là một nhóm, có hai cách:
  - thêm bot stock vào nhóm đó, rồi đưa id nhóm vào `ALTCOIN_REPORT_CHAT_IDS`;
  - hoặc chấp nhận nhận tin altcoin ở các chat báo cáo của stock.
- Zoom: người nhận thông báo là người đã ghép đôi với app chung. Nếu trước đây người nhận tin `okxbot` khác người nhận tin stock, nay chỉ còn một người nhận (giới hạn của `notify_target()`). Muốn cả nhóm cùng nhận thì dùng Incoming Webhook của stock. Kênh Zoom riêng của altcoin: xem §7.5.
- **Nếu app chung là `okxbot`** (stock đang "mượn" nó qua `ZOOM_TO_JID`, ngoại lệ ở §17, mục 4), làm ngay sau khi dừng server altcoin cũ (Giai đoạn 6 bước 3b):
  1. trên Marketplace, trỏ Bot endpoint và Event Subscription (`/webhook/zoom`), Deauthorization, OAuth redirect (`/zoom/oauth/callback`) và các trang công khai (`/docs`, `/privacy`, `/terms`, `/support`, `/data-deletion`) của `okxbot` về URL gốc của stock, theo danh sách trên trang `/zoom` của stock; bấm Validate;
  2. đặt `ZOOM_SECRET_TOKEN` của `okxbot` cho stock (cùng `ZOOM_CLIENT_*`, `ZOOM_BOT_JID`, `ZOOM_ACCOUNT_ID` nếu chưa khớp);
  3. ghép đôi **trên stock** (`/zoompair <JID>` hoặc `ZOOM_ALLOWED_USERS`), kiểm `/zoomstatus`, **rồi** mới xoá `ZOOM_TO_JID`. Xoá trước thì thông báo Zoom lặng lẽ dừng, vì ghép đôi cũ nằm trong kv altcoin;
  4. lệnh trong kênh là `/okxbot alt …`; **không bao giờ** deactivate `okxbot`;
  5. rollback về server cũ thì phải trỏ lại các URL trên về server cũ, và stock mất lệnh Zoom trong thời gian đó.
- **Tin chuyển đổi**, gửi hai đường:
  - bằng **token bot OKX cũ** (bot stock không nhắn được cho người chưa `/start` nó): `curl` `sendMessage` tới `TELEGRAM_CHAT_ID` cũ, token để trong biến môi trường chứ không gõ thẳng vào lệnh. Làm trước khi revoke token;
  - bằng bot stock, vào các chat báo cáo (và `ALTCOIN_REPORT_CHAT_IDS`).

  Nội dung: "Lệnh Altcoin giờ gõ `/alt …` trên bot <tên bot stock> (nhớ `/start` bot này); Zoom: `alt …`".
- Phụ lục docs: bảng "lệnh cũ → lệnh mới".

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

Luôn **tạm dừng Render trước, rồi dừng server altcoin cũ**. Dừng êm thì nó nhả lock.

- **Đang dùng Supabase:**
  - Đặt `ALT_DATABASE_URL` = đúng URL Session pooler đó. Leader lock, `paper_balance`, `learned` và trades đều đi theo. Cặp Zoom cũ của `okxbot` cũng nằm trong kv, nhưng không còn dùng.
  - Nếu Render còn chạy, nó giữ lock và heartbeat. Khi đó chặn một instance giữ child ở `waiting` (75) mãi, nên tạm dừng Render và chờ quá `LEADER_TTL` trước khi bật.
  - Dòng heartbeat cũ của `render` vẫn hiện trong `/alt status` dưới dạng "render=…(mất liên lạc)". Đây chỉ là chuyện hiển thị; tuỳ chọn xoá ở Giai đoạn 7, sau `pg_dump`.
- **Đang dùng SQLite trên server Botkeep cũ:**
  1. dừng server cũ;
  2. tải `data/bot.db` (cùng `-journal` nếu có);
  3. deploy bản gộp (`ALTCOIN_ENABLED` vẫn 0);
  4. tải file lên `data/altcoin/bot.db`;
  5. đặt `ALT_INSTANCE_NAME=crypto`, `ALTCOIN_ENABLED=1`, rồi khởi động lại.

  Nếu File Manager giới hạn 4 MB thì chạy `VACUUM` trước, hoặc đẩy lên Postgres. Nếu file chép xong trong vòng 90 s sau khi dừng server cũ, heartbeat cũ (khác host) còn "sống", nên child chờ một lượt 75 rồi tự chạy.
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
  - `events` của altcoin nằm trong DB altcoin và xem ở `/altcoin/`. Mọi thông báo altcoin, kể cả tin bị lọc hay bị giới hạn nhịp, đều có ở đây, nhưng chỉ **200 ký tự đầu** (`Notifier.send` ghi log trước tiên, `notify.py:36`).
  - `events` của stock (`/dulieu`) không có dòng nào của altcoin, trừ sự kiện supervisor và cầu nối (gồm toàn văn đã che của tin cầu nối bỏ, §7.3).
- **stdout và stderr của child** (gồm traceback và lỗi thread) đi qua cùng một pipe.
  - Thread `altcoin-log` giải mã utf-8 với `errors="replace"`, cho qua `vnbot.eventlog.redact()`, rồi in `[altcoin] …` ra `sys.stdout`.
  - Không đi qua `logging`, nên không lọt vào `events` của stock.
  - Giữ 200 dòng cuối; trang 503 hiện các dòng này sau khi đăng nhập.
  - Khung trạng thái và thông báo đi riêng qua pipe `HUB_FD`, không lẫn với log.
- **Sự kiện supervisor và cầu nối** ghi qua logger `vnbot.altcoin_host` / `vnbot.altcoin_bridge`, hiện ở `/dulieu`:
  - spawn, mã thoát, backoff, watchdog kill, khoá bị bỏ (`dropped_secrets`, `ignored_keys`);
  - lệnh `/alt` (người gửi, tên lệnh, kết quả, thời gian; không ghi nội dung trả lời);
  - tin bị bỏ (toàn văn đã che) hoặc bị gộp; tin gửi lỗi (`send_failed`); `seam_ok=false`.
- **Che bí mật ở phía cha:**
  - `SECRET_KEYS` thêm: `ALT_DATABASE_URL`, `ALT_OKX_API_KEY`, `ALT_OKX_API_SECRET`, `ALT_OKX_PASSPHRASE`, `ALT_OPENROUTER_API_KEY`, `ALT_NINEROUTER_API_KEY`, `ALT_CUSTOM_API_KEY`.
  - Thêm cả các khoá bị bỏ qua nhưng có thể vẫn nằm trên panel: `ALT_TELEGRAM_TOKEN`, `ALT_ZOOM_CLIENT_SECRET`, `ALT_ZOOM_SECRET_TOKEN`, `ALT_ZOOM_BOT_SECRET_TOKEN`, `ALT_ZOOM_WEBHOOK_TOKEN`, `ALT_ZOOM_VERIFICATION_TOKEN`, `ALT_DASHBOARD_PASS`. Chúng không chuyển cho child nhưng vẫn được che.
  - Giá trị `HUB_TOKEN` và `HUB_CMD_TOKEN` của mỗi lần spawn.
  - Tách mật khẩu khỏi `ALT_DATABASE_URL`.
  - Đọc `dotenv_values()` của `altcoin/.env` và `altcoin/bot.env`, rồi thêm vào danh sách che các giá trị có tên chứa `TOKEN|SECRET|KEY|PASS` (và mật khẩu trong `DATABASE_URL`), thông qua hàm mới `eventlog.add_secret_values()`. Việc này áp cả cho token bot OKX cũ hay khoá `okxbot` còn sót trong file.
  - **Cầu nối:** mọi câu trả lời `/alt` và mọi thông báo altcoin đều qua `eventlog.redact()` trước `send`/`broadcast`. Sau đó `telegram_bot.redact()` của stock (token Telegram, `ROUTER9_API_KEY`) vẫn chạy như cũ.

---

## 10. Phụ thuộc và ngân sách RAM / đĩa trên Botkeep

- **Python ≥ 3.12** cho stock và bản gộp. Child dùng cùng trình thông dịch (`sys.executable`).
- **`requirements.txt` gốc** thêm:
  - `ccxt==4.5.90`, ghim đúng bản đã resolve và đo. ccxt ghim cứng khoảng 17 gói (`urllib3==2.8.0`, `idna==3.18`, `typing_extensions==4.16.0`, `charset_normalizer==3.4.7`, `uvloop==0.22.1`…). Hệ quả là **hạ** `idna` (3.20 → 3.18) và `charset_normalizer` (3.5.2 → 3.4.7) mà stock đang dùng, nên phải chạy đủ `run_all.py`.
  - `httpx>=0.27`. Stock đã import mà chưa khai báo; Hub và cầu nối lệnh dùng nó.
  - Mỗi lần nâng ccxt là một thay đổi ảnh hưởng stock: chạy `pip install --dry-run -r requirements.txt`, ghi lại phiên bản `idna`/`urllib3`/`typing_extensions`/`charset_normalizer`, rồi `run_all.py`.
- **`altcoin/requirements.txt`** cũng ghim `ccxt==4.5.90`, vì `ALTCOIN_AUTO_INSTALL` (`ensure_deps`) cài từ file này. Nhờ vậy cả hai đường cài cho cùng một ccxt.
- **Event loop:** ccxt kéo theo `uvloop`, nên stock chạy `uvicorn.run(..., loop="asyncio")`. Tiến trình stock không import ccxt.
- **`oom_score_adj=800`** cho child, để khi hết RAM kernel giết altcoin trước stock.

| Tài nguyên | Hiện nay | Sau gộp | Căn cứ |
|---|---|---|---|
| RAM nhàn rỗi | stock 157 MB | stock 157 + altcoin 154–156 + proxy và cầu nối ~3 ≈ **315 MB / 2 GB** | đo (cầu nối: 2 thread + hàng đợi ≤ 200 tin, không đáng kể) |
| RAM đỉnh | stock khi `/phantich`: chưa đo | đo cục bộ ở Giai đoạn 3, rồi **trên Botkeep** ở canary cuối Giai đoạn 5, khi child đang chạy; chấp nhận nếu tổng < 1,2 GB | — |
| Đĩa site-packages | 322 MB | + ccxt và phụ thuộc ≈ 102–123 MB → **≈ 445 MB / 2 GB** + `data/` | đo; README ghi gói free có 2 GB đĩa; đo lại `df` ở Giai đoạn 0 |
| Khởi động | stock sẵn sàng ~1,6 s | child bắt đầu **sau** stock; thêm 2–5 s | đo |

Thay ccxt bằng một client OKX tự viết (khoảng 17 endpoint): **không khuyến nghị**. Không giúp gì cho giới hạn upload, và đường đặt lệnh live chưa có test.

---

## 11. Gói Botkeep: `scripts/build_botkeep_bundle.py` (mới) và test

**Danh sách cho phép, ngoài ra không có gì:**

- `main_botkeep.py`, `requirements.txt`, `.env.example`, `README.md`, `LICENSE`
- `tradingagents/**/*.py`
- `tradingagents/assets/tauric-logo.svg` (`report_html.py` đọc file này lúc import)
- `vnbot/**/*.py` (gồm `altcoin_bridge.py`)
- `vnbot/static/favicon.png`, `vnbot/static/logo.png`
- `altcoin/bot/**/*.py`
- `altcoin/run.py` (bắt buộc cho fingerprint)
- `altcoin/hosted.py`, `altcoin/requirements.txt` (không gồm `altcoin/.env.example`: không ai đọc nó, và nó quảng cáo các khoá bị bỏ qua ở hosted mode)

**Kiểm tra cứng (sai thì thoát 1, kèm bảng file vi phạm):**

0. Từ chối chạy trên Python < 3.12. Lý do: biên dịch bằng 3.11 thì hỏng, còn biên dịch bằng 3.13 lại che mất lỗi mà Botkeep 3.11 sẽ gặp.
1. **Cấm:** `.env`, `*.env`, `*.db*`, `data/`, `__pycache__/`, `*.pyc`, `tests/`, `docs/`, `altcoin/test_*`, `altcoin/tools/`, `altcoin/dist/`, `dist/`, `.git*`, `render.yaml`. Nguồn là `git ls-files`; không có `.git` thì duyệt thư mục với cùng luật (27 file `.pyc` bị ignore đang nằm trong cây làm việc).
2. Số file ≤ 250 **và** số file + số thư mục ≤ 250.
3. Tổng giải nén ≤ 4.000.000 B.
4. Zip ≤ 4.000.000 B. Zip phẳng, mặc định **không** ghi mục thư mục, tên sắp xếp, timestamp cố định, deflate 9, nên tái lập được. Cờ `--dir-entries` để dự phòng (≈165 file + 20 thư mục = ≈185 mục, vẫn < 250).
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
| **Sau gộp** (+ `hosted.py`, `hub.py`, `altcoin_host.py`, `altcoin_bridge.py`, `webguard.py`, phần sửa; − `altcoin/.env.example` 8.094 B; ước tính) | **≈165** | 20 | **≈2.547.000** | **≈917.000** | **≈85 (≈65) / ≈1,45 MB** |
| Deploy cả repo từ GitHub (ước tính: 184 + 41 − `render.yaml` + ≈15 file mới) | ≈239 | ≈29 | ≈3.340.000 | ≈1.260.000 | 11 file; tính cả thư mục ≈268 > 250 → **không khả thi** theo cách hiểu chặt |

`render.yaml` chưa bao giờ nằm trong gói (không có trong danh sách cho phép), nên việc xoá nó không đổi số liệu gói.

**Đòn bẩy dự phòng:** bỏ `README.md` khỏi gói (93 KB); bỏ `vnbot/static/logo.png` (86 KB, không HTML nào dùng, nhưng bỏ thì phải sửa `ui.py` và re-stamp).

**`tests/suites/suite_bundle.py`:**

1. Assert `MAX_FILES == 250` và `MAX_BYTES == 4_000_000`.
2. Build vào thư mục tạm. Assert mọi giới hạn ở mục 2–5, không có mục thư mục, không có tên bị cấm, đủ file bắt buộc. In khoảng trống còn lại.
3. **Mồi nhử:** chép thêm `altcoin/bot.env`, `.env`, `vnbot/__pycache__/x.pyc`, `data/bot.db`, `render.yaml`. Không file nào trong số đó, và cả `altcoin/.env.example`, được vào gói.
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
  - ánh xạ env, allowlist, `PYTHONIOENCODING`; khoá bị bỏ (`ALT_TELEGRAM_*`, `ALT_ZOOM_*`, `ALT_PEER_*`, `ALT_DASHBOARD_*`…) không tới child và có trong `ignored_keys`;
  - env child không có `TELEGRAM_TOKEN`, `ZOOM_*`, `WEB_PASSWORD`, `RENDER_*` kể cả khi `os.environ` của cha có (giả lập `/caidat` đã `apply`);
  - bảng ưu tiên với `bot.env`. Ca mồi nhử: `bot.env` có `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `ZOOM_*` (cả `ZOOM_BOT_SECRET_TOKEN`), `PEER_URL`, `SELF_URL`, `DASHBOARD_*`. Sau niêm phong, mọi `cfg` đó rỗng, `os.environ` không còn chúng, và WARNING liệt kê đủ. Ghim hai danh sách: đúng 10 thuộc tính `cfg.ZOOM_*` và 11 tên env `ZOOM_*`;
  - `bot.main.Telegram is HubChannel`. Chạy tới `Bot()` stub với `requests` bị chặn: không request nào tới `api.telegram.org` hay `api.zoom.us`. Kiểm hiệu lực: `bot.tg` là `HubChannel` và nằm trong `bot.notify.channels`; nếu thay `bot.tg` bằng lớp khác thì `seam_ok=false`;
  - pipe: khung `state`/`notify` đúng schema §7.4 tới cha; dòng > 64 KiB bị bỏ; thông báo 12.000 ký tự tiếng Việt + emoji ra một khung ≤ 60 KiB, không bị bỏ; 300 khung `notify` liên tiếp khi cha không đọc → khung `state` mới nhất vẫn đi trước; cha đóng đầu ghi nên thấy EOF khi child chết; không có `HUB_FD` thì HubChannel chỉ ghi log; `HubChannel.send` không ném lỗi khi pipe đã đóng;
  - trong child: `HUB_FD`, `HUB_TOKEN`, `HUB_CMD_TOKEN` không còn trong `os.environ`, và `os.get_inheritable(HUB_FD)` là `False`; một subprocess cháu không thấy fd đó;
  - ma trận live (§6) chạy tới `Bot()` stub;
  - chặn DB trùng: theo URL, theo bảng `market`/`fills`;
  - chặn DB rỗng, và `ALTCOIN_FRESH_START`;
  - **chặn một instance:** heartbeat khác tên còn sống → 75; cùng tên khác host → 75; cùng tên cùng host (restart sau SIGKILL) → chạy; heartbeat quá `LEADER_TTL` → chạy; `hosted.lock` đang bị một tiến trình khác giữ → 75 dù cùng tên cùng host; SIGKILL tiến trình giữ khoá → lần sau chạy được;
  - lọc `__BOTKEEP_KEEP_SECRET_` kèm cảnh báo;
  - supervisor với child giả: mã 78/75/-9, backoff, quá hạn sẵn sàng thì spawn lại với cổng mới, `stop()` qua SIGTERM thật;
  - `/health` trả lời < 100 ms trong lúc `stop()` đang chạy; `state()`, `/health` và `/altcoin/hub.json` không chứa token; `/health` chỉ có `altcoin.state`;
  - spawn `hosted.py --check` thật từ cwd `/`, có một `.env` gốc mồi nhử: thoát 0 và không đọc file mồi;
  - child ở `TZ=Asia/Ho_Chi_Minh` khi cha ở `TZ=UTC`, và fallback tzdata;
  - che giá trị trong `bot.env` (kể cả token bot OKX cũ bị bỏ qua) và các `ALT_ZOOM_*` còn trên panel;
  - không khoá `ALT_` nào nằm trong `config.FIELDS`.
- **`suite_altcoin_proxy.py`:** Hub + child ASGI giả trên cổng localhost thật.
  - danh sách công khai, 308 và 404 so chính xác;
  - `/altcoin/_hub/command` và `/altcoin/_hub` (không `/` cuối) → 404, kể cả có Basic đúng; `/altcoin/_hub/../_hub/command`, `/altcoin/%5Fhub/command`, `/altcoin/%5fhub/command`, `/altcoin/_h%75b/command` → 400; child giả không nhận gì;
  - `/altcoin/hub.json` cần Basic, trả `state()` không token, không tới child, chạy cả khi child chết;
  - header `X-Hub-Token`/`X-Hub-Cmd-Token` do client gửi bị xoá; child giả thấy đúng một `X-Hub-Token` do Hub đặt và không thấy `X-Hub-Cmd-Token`;
  - GET `/altcoin/zoom`, `/altcoin/docs`, `/altcoin/privacy` → 308 sang trang stock; POST `/altcoin/webhook/zoom`, `/altcoin/zoom/bot`, `/altcoin/caidat/kenh/telegram_notify`, `/altcoin/caidat/zoomtest` → 404, child không nhận gì;
  - `/altcoin/health` cần Basic;
  - 401, 429 (throttle chung), 503 khi thiếu `WEB_PASSWORD`;
  - CSRF 403 với Origin lạ, `Origin: null`, Referer chéo site; không còn ngoại lệ nào;
  - **400** với `/altcoin/health/../api/export/all.json`, `POST /altcoin/webhook/zoom/../../api/learn/reset`, `%2e%2e`, `%2F`, `//`;
  - 413 khi body > 64 KB;
  - không chuyển tiếp `Authorization`;
  - 308 `/altcoin` → `/altcoin/`;
  - viết lại `Location` cho cả `http`/`https://127.0.0.1:<port>` và host công khai; ca `/altcoin/caidat/` với `X-Forwarded-Proto: https`;
  - legacy host (nếu làm): GET → 308 tuyệt đối, không lặp vòng; POST → 410;
  - stream 5 MB;
  - 503 khi child chết;
  - route stock chạy khi `LateApp` đang khởi động;
  - cùng bộ ca auth chạy với Hub và `create_app`, kết quả giống nhau.
- **`suite_altcoin_web.py`:** `build_app` thật với bot giả, sau `hosted.adapt("/altcoin", …)`.
  - trên mọi trang HTML còn phục vụ (`/`, `/ai`, `/caidat`, `/phienban`), link `href`/`action`/`formaction` tuyệt đối thiếu tiền tố `/altcoin/` chỉ được là **đúng ba** link sang stock: `/` (thanh chuyển mục), `/ai#kenh` và `/zoom` (thẻ kênh / banner). Assert ba link này có mặt và **không** bị viết thành `/altcoin/ai#kenh`, `/altcoin/zoom`; link `/altcoin/` của thanh chuyển mục không thành `/altcoin/altcoin/`; không còn chuỗi `@stock/` nào;
  - `/caidat`: không còn thẻ "Thông báo & tin nhắn" và thẻ "Zoom"; có đúng một thẻ "Kênh do Stock quản lý" với link `/ai#kenh` và `/zoom`; seam `ui.card` mất thì có banner;
  - có thanh chuyển mục;
  - middleware: thiếu token → 403; `/_hub/command` với chỉ `X-Hub-Token` → 403; đúng `X-Hub-Cmd-Token` → 200; trước `start_equity` → 503; lệnh ngoài allowlist → 400; lệnh ghi khi `is_leader=False` → 409 `leader:false`;
  - **khoá lệnh ghi:** bot giả có `close_all` chặn 5 s; gọi `close` lần một rồi bỏ request (timeout client 1 s), gọi `close` lần hai → 409 `busy:true` và `close_all` chỉ chạy **một** lần; xong lần một thì `close` mới lại chạy được; lệnh đọc không bị khoá;
  - tự kiểm báo lỗi rõ khi một seam biến mất;
  - end-to-end tuỳ chọn: spawn child thật (skip 77 nếu thiếu ccxt). `/altcoin/health` báo build `380dfca`; `/_hub/command status` trả về văn bản; khung `notify` "🚀 … khởi động" tới cha qua pipe.
- **`suite_altcoin_bridge.py`** (mới; `TelegramBot` thật với `_call` giả, Zoom giả, supervisor giả):
  - cú pháp: `/alt`, `/alt help`, `/ALT STATUS`, `/alt@Bot status`, `/altcoin status`, Zoom `alt status` (qua `zoom_command` và thêm `/`);
  - lệnh lạ và lệnh altcoin gõ trần (`/close`, `/status`) không bao giờ tới child; lệnh trần nhận gợi ý `/alt …`;
  - `bot.alt is None` (harness stock) → `/alt` trả help như cũ; `HELP` và `/docs` không đổi khi `ALTCOIN_ENABLED` khác 1;
  - quyền: người lạ → câu từ chối của stock, child không nhận gì; người được phép trong nhóm `-100500` → trả lời vào nhóm; Zoom tắt `zoom_commands` → không chạy; `ALTCOIN_ADMIN_IDS` đặt → user được phép nhưng không trong danh sách chạy được `/alt status` mà không chạy được `pause/resume/close/apply`; Zoom: người trong `ZOOM_ALLOWED_USERS` nhưng không phải JID đã ghép đôi → lệnh ghi bị từ chối;
  - hai bước: `close`/`apply`/`resume` không `xacnhan` → không gọi child; `xacnhan` quá 120 s, hoặc từ người khác, hoặc cùng người ở cuộc trò chuyện khác, hoặc lần thứ hai → không gọi; **Zoom:** A gõ `alt close`, B (cũng được phép) gõ `alt close xacnhan` → không gọi; đúng → gọi **một** lần và chỉ gửi tên lệnh;
  - child giả tắt / đang khởi động / `waiting` (có `<tên>@<host>`) / từ chối kết nối / 503 / 409 `leader:false` / 409 `busy:true` / timeout / lỗi → đúng câu ở §7.2; không gửi lại lệnh ghi; `close` thứ hai trong lúc `close` đầu đang chạy → "đang có lệnh"; **timeout ở `close` đầu, rồi `close` + `xacnhan` lần hai → child giả trả `busy`, câu trả lời là "đang có lệnh", không có `close_all` thứ hai**;
  - trả lời 9.000 ký tự → 3 tin Telegram; tới `zoom:N` → `zoom.reply`; trả lời `learn` có "gõ /apply" → thành "gõ /alt apply";
  - thông báo: khung hợp lệ → `broadcast` một lần mỗi chat, có tiền tố; `ALTCOIN_REPORT_CHAT_IDS` đặt → chỉ gửi tới đó; "tiếp quản" bị lọc; "khởi động" tối đa 1/30 phút; `/resume` → `/alt resume`; trùng trong 120 s → một tin; 30 tin trong một phút → ≤ 15 tin + một tin tổng hợp, hai lần gửi cách nhau ≥ 1 s, phần thừa có toàn văn trong log cầu nối; tin "🛑 …" giữa loạt đó vẫn được gửi;
  - sink chặn 120 s (giả `_call` ngủ) trong khi child giả ghi 500 khung → reader không chặn, `dropped` tăng, khung `state` mới vẫn tới `state()`;
  - gửi lỗi: `_call` giả ném 429 hết lượt → `send_failed` tăng; Zoom không nhận (`on_fail`) → `send_failed` tăng;
  - công tắc: `altcoin` tắt → không gửi, `muted` tăng; `telegram`/`zoom_notify` tắt → kênh tương ứng không nhận; `Switches.set("altcoin", …)` hợp lệ, tên lạ vẫn lỗi;
  - trước khi gắn sink → đệm ≤ 50 rồi xả theo thứ tự;
  - che bí mật: chuỗi bằng `ALT_OKX_API_SECRET` trong thông báo hoặc trong câu trả lời → bị che;
  - `/alt zoomtest` → tin thử tới mọi kênh đang bật; Zoom giả từ chối → câu trả lời ghi "Zoom không nhận".
- **Suite stock:**
  - thêm assert: khi `ui.SECTIONS` được đặt thì có đúng một `<h1` và nav "Điều hướng chính" không đổi;
  - `suite_security_web`, `suite_web` và `suite_security_telegram` phải xanh **không sửa**, sau refactor `webguard` và sau khi thêm nhánh `/alt`.
- **Mong đợi:** `python tests/run_all.py -j 4` → **28/28** (22 + 6). `pytest -q` chỉ thu thập `tests/test_suites.py`.
- **Skill dùng khi làm:**
  - `test-driven-development` cho `hub.py`, `altcoin_host.py`, `altcoin_bridge.py`, `hosted.py`, `webguard.py`;
  - `code-review` cho mỗi PR;
  - `security-review` cho Giai đoạn 3, 4 và 5.

---

## 13. Tài liệu

- **`README.md` gốc:**
  - Mục mới ở đầu: "Hai phần: Cổ phiếu (`/`) và Altcoin (`/altcoin/`). Một server Botkeep, một gói zip, một mật khẩu, một bot Telegram, một app Zoom (lệnh altcoin: `/alt …`)".
  - §2 trỏ tới `python scripts/build_botkeep_bundle.py` với số thật (≈165 file, ≈2,55 MB, zip ≈0,92 MB).
  - Python **3.12+** (dòng 27, 39, 562).
  - Bỏ link `trading_vn.git`.
  - "hơn 370 kiểm tra" → "hơn 1.000".
  - `deploy/botkeep/.env.example` → `.env.example`, sửa cả trong docstring `main_botkeep.py` và `vnbot/__init__.py`.
  - Bảng lệnh: thêm `/alt`.
  - Đoạn "Dùng chung với bot OKX" (`README.md:414`: tạo **app thứ hai** hoặc mượn app OKX) và dòng `ZOOM_TO_JID` trong bảng env (`:495`, "mượn app của bot OKX"): viết lại thành "một app Zoom chung cho cả hai mục; lệnh altcoin gõ `alt …`".
- **`docs/ALTCOIN_TRONG_STOCKVN.md`:**
  - quy tắc `ALT_*` / `ALTCOIN_*` và các bảng ở §6, gồm bảng thứ tự ưu tiên và danh sách khoá bị bỏ qua;
  - chép `bot.env`; các khoá `TELEGRAM_*`, `ZOOM_*`, `PEER_URL`, `SELF_URL`, `DASHBOARD_*` trong đó bị bỏ qua;
  - nhập lại bí mật từ nguồn, không chép từ panel bị che;
  - **đừng** dán panel altcoin cũ (không tiền tố) vào panel stock, vì sẽ đè `TELEGRAM_TOKEN`/`DATABASE_URL` của stock;
  - **kênh chung (§7):**
    - bảng lệnh `/alt`, hai bước `xacnhan`, quyền theo user id, `ALTCOIN_ADMIN_IDS`; quyền **vừa hẹp lại vừa rộng ra** so với bot OKX (§7.2);
    - bảng "lệnh cũ → lệnh mới" (Telegram và Zoom; `/vnbot alt …` hoặc `/okxbot alt …` tuỳ app chung);
    - công tắc `altcoin`;
    - `ALTCOIN_REPORT_CHAT_IDS`;
    - checklist chuyển người dùng (§7.6), gồm nhánh `okxbot` là app chung;
    - `/altcoin/caidat` không còn công tắc kênh; mất mát chấp nhận ở §7.5;
    - `/altcoin/hub.json` (trạng thái chi tiết, cần đăng nhập);
  - live gate, chặn một instance, và quy tắc "chỉ bật live sau Giai đoạn 7";
  - chuyển DB; lệnh liệt kê bảng; dừng Render và server cũ trước;
  - `ALTCOIN_ENABLED`, `ALTCOIN_AUTO_INSTALL`, `ALTCOIN_FRESH_START`, `ALTCOIN_LEGACY_HOSTS`;
  - rollback;
  - cảnh báo hồ sơ rủi ro cao của `botkeep.env`;
  - hộp "Cập nhật bot" ở `/altcoin/phienban` mô tả chế độ độc lập (tải zip altcoin, `data/`), không đúng cho chế độ hosted;
  - các hành vi của `run.py` không có trong chế độ hosted: tự cài thư viện (trừ khi `ALTCOIN_AUTO_INSTALL=1`), dọn pip cache và `__pycache__`, log dung lượng đĩa;
  - mỗi bản phát hành chỉ của stock cũng làm altcoin restart, và không còn failover;
  - không có mục Render. Blueprint cũ chỉ còn trong lịch sử git (§3). Phần tạo Supabase Session pooler chép từ §3b cũ của `altcoin/README.md`, bỏ các ý về Render.
- **`.env.example`:** thêm mục `# ===== ALTCOIN =====` gồm `ALTCOIN_ENABLED=0`, `ALTCOIN_AUTO_INSTALL`, `ALTCOIN_FRESH_START`, `ALTCOIN_LEGACY_HOSTS`, `ALTCOIN_REPORT_CHAT_IDS`, `ALTCOIN_ADMIN_IDS`, `ALT_MODE=paper`, `ALT_I_UNDERSTAND_THE_RISK`, `ALT_LIVE_ACK`, `ALT_DATABASE_URL`, `ALT_DATA_DIR`, `ALT_INSTANCE_NAME`, `ALT_OKX_*`, `ALT_NINEROUTER_*`, `ALT_PUBLIC_URL`.
  - Kèm một dòng chú thích: "Telegram/Zoom dùng chung biến của stock; không có `ALT_TELEGRAM_*`/`ALT_ZOOM_*`/`ALT_PEER_*`".
  - Sửa chú thích `ZOOM_TO_JID` (`.env.example:178-181`): bỏ cách "mượn app chatbot của bot OKX", vì sau gộp chỉ còn một app.
- **Trong code stock:** docstring `vnbot/channels.py` (công tắc `altcoin`), docstring `vnbot/zoom.py` (`:9-12`, `:19-20`: bỏ "mượn app OKX" và "app thứ hai"), `HELP` của `telegram_bot.py`, bảng `COMMANDS` của `/docs`.
- **Phía altcoin** (theo bảng ở §2):
  - `altcoin/README.md` thêm 5 dòng đầu (trỏ docs; ghi "trong Stockvn: lệnh qua `/alt`, không dùng token riêng"); **bỏ §3b Render** và dòng `render.yaml` trong danh sách file;
  - `altcoin/check_db.py` nạp `.env` cạnh nó;
  - `.env.example` (`:109-120`) và `botkeep.env` (`:58-60`): khối Render/`PEER_URL`/`SELF_URL` ghi "không dùng – đã bỏ Render"; phần còn lại giữ cho chế độ độc lập.
  - Tất cả nằm ngoài fingerprint.
- **Giấy phép:** chờ anh/chị quyết (§17).

---

## 14. Không giữ Render

**Đã bỏ khỏi kế hoạch:**

- `render.yaml` (xoá khỏi cây ở §3; còn trong lịch sử git);
- Giai đoạn "Render từ stockvn" của bản 1;
- `PEER_URL`, `SELF_URL`, `ALT_PEER_URL`, `ALT_PEER_MODE`, `KEEPALIVE_INTERVAL`;
- điều kiện peer trong live gate;
- chặn SQLite + peer;
- `/altcoin/health` công khai (đích keepalive);
- docs "Render failover";
- `PYTHON_VERSION=3.11.9`, `rootDir`, `buildFilter`.

**Còn trong `altcoin/bot/` nhưng ngủ (không sửa, vì nằm trong fingerprint):**

- `_elect()` vẫn chạy mỗi vòng và thắng ngay ở vòng đầu. `try_lock` trên DB riêng không có đối thủ, và chặn một instance bảo đảm điều đó.
- `_keepalive` thoát ngay, vì `PEER_URL` và `SELF_URL` bị ép rỗng (§1 bước 3).
- Các fallback `RENDER_SERVICE_NAME`/`RENDER_EXTERNAL_URL` không bao giờ có giá trị: env của child dựng từ allowlist, và `INSTANCE_NAME` được niêm phong.
- Chú thích và docstring nhắc Render (`config.py:221,225,245`, `dashboard.py:636`, `storage.py:3`, `run.py`) giữ nguyên.
- `run.py` vẫn chạy được ở chế độ độc lập trên VPS nếu sau này cần, nhưng nằm ngoài phạm vi kế hoạch.

**Hệ quả chấp nhận** (theo quyết định 3):

- Altcoin dừng trong lúc deploy hoặc restart stock (vài giây; tối đa khoảng 2 phút nếu phải chờ một lượt 75).
- Altcoin ngừng khi server Botkeep ngừng, nhưng lúc đó stock cũng ngừng.
- Theo dõi qua `/health` của stock, `/altcoin/hub.json` và health check của Botkeep.
- Không có Render để đỡ một cut-over hỏng, nên các điểm chỉ Botkeep mới có (`pass_fds`, `PR_SET_PDEATHSIG`, `oom_score_adj`, tzdata, cài ccxt, RSS thật) được thử bằng **canary** cuối Giai đoạn 5, khi server altcoin cũ vẫn chạy.

**Tắt Render theo thứ tự** (Giai đoạn 6). Render **không** phải đường rollback (rollback về server Botkeep cũ, §15), nên không giữ nó lại:

1. Tạm dừng service **trước** cut-over. Render đang poll token bot OKX cũ, và nếu dùng chung Supabase thì có thể đang giữ leader lock.
2. Chờ quá `LEADER_TTL` (90 s).
3. Khi Giai đoạn 6 đạt kiểm tra chấp nhận: chép giá trị env nào còn cần từ Render (nếu có), rồi xoá service và ngắt kết nối repo `altcoin_bot` trên Render.

---

## 15. Các giai đoạn (mỗi giai đoạn một PR, xong vẫn deploy được)

| Giai đoạn | Nội dung | Kiểm tra chấp nhận |
|---|---|---|
| **0. Khảo sát và sao lưu** (không code) | **Botkeep:** phiên bản Python (**bắt buộc ≥ 3.12**); có cài lại requirements mỗi lần deploy không; lệnh khởi động; upload có giữ `data/` và `altcoin/bot.env` không; health probe (TCP hay `/health`, GET hay HEAD); IP client sau proxy; giới hạn File Manager; `df`; **tên** các biến env của tiến trình; có `/usr/share/zoneinfo` không. **Kiểm kê kênh:** token bot stock (sẽ là bot chung) và token bot OKX (sẽ ngừng); stock đang dùng app Zoom nào (`vnbot` nhận lệnh, hay "mượn" `okxbot` qua `ZOOM_TO_JID` không có `ZOOM_SECRET_TOKEN`); app `okxbot`; kênh Zoom **riêng** của altcoin (`ZOOM_WEBHOOK_URL`/`ZOOM_TO_JID` trong panel và `bot.env` cũ: có không, tới đâu); `TELEGRAM_ALLOWED_IDS`/`TELEGRAM_REPORT_CHAT_IDS` của stock so với `TELEGRAM_CHAT_ID` cũ của altcoin (cá nhân hay nhóm; bot stock đã ở trong nhóm chưa); ai đang nhận lệnh altcoin; ai trong `TELEGRAM_ALLOWED_IDS` không nên có quyền ghi altcoin (→ `ALTCOIN_ADMIN_IDS`). **Kiểm kê khác:** 2 `DATABASE_URL` và danh sách bảng (truy vấn chỉ đọc ở §8); panel và `bot.env` của altcoin cũ; altcoin đang paper hay live; Render có đang chạy, có dùng chung Supabase không, instance nào giữ leader. **Sao lưu:** `data/` của stock, `data/bot.db` của altcoin cũ, `pg_dump` cả hai DB. | Checklist điền đủ. Phát hiện DB dùng chung trước mọi việc. Python < 3.12 thì dừng lại để nâng cấp trước. Xác định được **app Zoom chung** (§17, mục 4 (Còn cần chốt)). Stock 22/22. Altcoin 5/5 test offline ở `8332222`. |
| **1. Bộ đóng gói** (không sửa `.py` trong `vnbot/`, `tradingagents/`, `main_botkeep.py`) | `scripts/build_botkeep_bundle.py`, `suite_bundle.py`, `pytest.ini`, README §2 + Python 3.12. | Builder báo 137 file / 18 thư mục / ≈2,13 MB, zip ≈0,78 MB, thoát 0. Build stock vẫn `7eaa3df`. Chèn file thứ 251 → suite đỏ. Tải zip lên Botkeep: giải nén ra đủ thư mục lồng (`vnbot/static`, `tradingagents/agents/analysts`); hỏng thì dùng `--dir-entries`. `/health` 200, `/phienban` "đúng bản phát hành", Telegram `/phienban` trả lời. `run_all` 23/23. |
| **2. Nhập altcoin kèm lịch sử** (ngủ yên) | Subtree add + merge (§3); **`git rm altcoin/render.yaml`**; `altcoin/README.md` (5 dòng đầu, bỏ §3b Render và dòng `render.yaml`), khối Render trong `altcoin/.env.example` và `altcoin/botkeep.env` ghi "không dùng"; `.gitignore` (+`!altcoin/dist/`); `altcoin/check_db.py`; `suite_altcoin_scripts`; builder gồm phần altcoin runtime (không có `altcoin/.env.example`). | Số commit như §3. `git show …:render.yaml` còn đọc được. `grep -ri render altcoin/README.md` không còn hướng dẫn deploy Render. `altcoin/tools/backtest_sessions.py` có mặt. `run_all` 24/24. Gói 160 file / 20 thư mục / ≈2,48 MB / zip ≈0,90 MB (nguyên mẫu đo trừ `altcoin/.env.example`). Build `7eaa3df` và `380dfca`. Deploy lên Botkeep hành xử như cũ. |
| **3. Supervisor + `hosted.py`** (chưa có web, chưa có cầu nối lệnh) | `vnbot/altcoin_host.py`, `altcoin/hosted.py` (§1, gồm xoá kênh/Render/đăng nhập riêng, `HubChannel` → pipe, chặn một instance); thread `altcoin-hub` (lúc này chỉ ghi log `[altcoin-notify] …`; khung `state` đã vào `state()`); `main_botkeep.py` (kiểm 3.12, handler tín hiệu, supervisor, `loop="asyncio"`, docstring); `SECRET_KEYS` + `add_secret_values`; `requirements.txt` (`ccxt==4.5.90`, `httpx`); `altcoin/requirements.txt` (ghim); hook `health_extra` (`/health` chỉ thêm `altcoin.state`); stock **v1.15.0** + NOTES + `python -m vnbot.version --stamp`. | `suite_altcoin_host` xanh; `run_all` 25/25, xanh dưới cả `TZ=UTC` lẫn `TZ=Asia/Ho_Chi_Minh`. `pip install --dry-run -r requirements.txt` resolve được, đã ghi phiên bản các gói dùng chung. Deploy với `ALTCOIN_ENABLED` chưa đặt: giống v1.14, RSS không đổi, log "Altcoin: tắt". **Cục bộ** (env theo quy tắc chạy cục bộ dưới bảng: không token Telegram/Zoom thật), với `=1` + `ALTCOIN_FRESH_START=1`, paper, SQLite tạm, và một `altcoin/bot.env` mồi nhử có `TELEGRAM_TOKEN`/`ZOOM_*`/`PEER_URL`: `--check` cho thấy chúng rỗng, `ignored_keys` đủ, child không mở kết nối nào tới `api.telegram.org`/`api.zoom.us`; log `[altcoin] … Dashboard sẵn sàng 127.0.0.1:N` và `[altcoin-notify] 🚀 … khởi động`; heartbeat giả của instance khác → `waiting`, hết TTL thì chạy; chạy tay một `hosted.py` thứ hai cùng `ALT_DATA_DIR` → 75 (`hosted.lock`); `kill -9 <child>` → restart sau 20 s trong khi `/health` stock vẫn 200; `kill -TERM <stock>` → không còn child mồ côi; `oom_score_adj` của child = 800; `data/bot.db` của stock không có `trades`/`locks`. Đo RSS đỉnh khi child chạy và `/phantich` đang chạy (đo lại trên Botkeep ở canary, Giai đoạn 5). |
| **4. Hai mục trên web** | Bước 1: `vnbot/webguard.py` (refactor) + `web.py` (`throttle=`). **Cổng chặn:** `suite_security_web` và `suite_web` xanh không sửa. Bước 2: `vnbot/hub.py` (gồm kiểm đường dẫn thô, danh sách 404/308 trên đường dẫn đã giải mã ở §4, `/altcoin/hub.json` và xoá `X-Hub-*`), thanh chuyển mục ở `ui.py`, thích nghi web trong `hosted.py` (gồm thay thẻ kênh ở `/caidat`), `ALTCOIN_LEGACY_HOSTS` (tuỳ chọn), re-stamp. | Suite proxy và web xanh, kể cả các vector `..`/`%2e`/`%5F`. Suite web/config/version của stock không đổi. Tổng 27/27. **Thử tay** (env cục bộ theo quy tắc dưới bảng): một lần hỏi mật khẩu; qua lại hai mục được; POST với `Origin: https://evil.example` → 403; `/altcoin/zoom` → `/zoom` của stock; `/altcoin/caidat` hiện "Kênh do Stock quản lý", link `/ai#kenh` và `/zoom` mở đúng trang stock; `/altcoin/hub.json` hỏi mật khẩu, `/health` chỉ có `altcoin.state`; `/altcoin/_hub/command` → 404 kể cả đã đăng nhập; `/altcoin` chạy khi stock đang khởi động; giết child thì stock vẫn phục vụ. |
| **5. Cầu nối kênh chung** | `vnbot/altcoin_bridge.py`; nhánh `/alt`, `/altcoin`, gợi ý lệnh trần, HELP có điều kiện, `send` trả `bool`, `broadcast(..., chat_ids=, on_fail=)`, `alt_broadcast` (công tắc `altcoin`, `muted`, `send_failed`), `zoom_command` mang JID người gửi trong `telegram_bot.py`; `vnbot/zoom.py` (truyền `sender_jid`/`reply_jid`, docstring); `channels.py` (`altcoin`); dòng công tắc + `id="kenh"` trong `web.py`; `settings.py` (`ALTCOIN_REPORT_CHAT_IDS`, `ALTCOIN_ADMIN_IDS`); `pages.py` (`COMMANDS`); `/_hub/command` + khoá lệnh ghi + middleware hai token trong `hosted.py`; nối sink trong `main_botkeep.py`; `.env.example`; `README.md` (`:414`, `:495`); docs §7; re-stamp. | `suite_altcoin_bridge` xanh; `suite_security_telegram` xanh không sửa; `run_all` **28/28**. **Deploy production với `ALTCOIN_ENABLED` vẫn 0:** `/alt status` trả "🪙 Altcoin đang tắt"; HELP, `/docs` và mọi lệnh stock không đổi. **Cục bộ** với child paper và một bot Telegram thử (+ app Zoom dev nếu có): `/alt status` được trả lời trên Telegram và trên Zoom; `/alt close` hỏi xác nhận, `/alt close xacnhan` → "Đã đóng 0 lệnh."; `/close` trần chỉ nhận gợi ý; `/alt zoomtest` → đúng một tin ở mỗi chat báo cáo và một tin Zoom; tắt công tắc `altcoin` → không có tin nào, `muted` tăng; `curl http://127.0.0.1:<child>/_hub/command` chỉ với `X-Hub-Token` → 403. **Canary trên Botkeep** (cuối giai đoạn, server altcoin cũ vẫn chạy; xem dưới bảng): `/altcoin/hub.json` có `state=running`, `seam_ok=true`, `leader`/`build` đến từ khung `state` qua pipe; log `[altcoin] oom_score_adj=800`; không lỗi tzdata; ccxt đã cài; đo RSS đỉnh (child + `/phantich`) < 1,2 GB; sau đó `ALTCOIN_ENABLED=0`, `/health` vẫn 200. |
| **6. Cut-over production** | Theo thứ tự: (1) **tạm dừng Render**, chờ > 90 s; (2) nhập lại bí mật thành `ALT_*` hoặc ghi vào `altcoin/bot.env` (không còn `ALT_TELEGRAM_*`/`ALT_ZOOM_*`); (3) **dừng server altcoin cũ**; (3b) **chỉ khi app Zoom chung là `okxbot`:** trỏ URL của `okxbot` về stock, đặt `ZOOM_SECRET_TOKEN` cho stock, ghép đôi trên stock rồi mới xoá `ZOOM_TO_JID` (§7.6); (4) chuyển DB (§8); (5) chuẩn bị kênh (§7.6): người dùng `/start` bot stock, thêm bot stock vào nhóm cũ nếu cần, đặt `ALTCOIN_REPORT_CHAT_IDS` nếu cần, kiểm user id trong `TELEGRAM_ALLOWED_IDS`, đặt `ALTCOIN_ADMIN_IDS` nếu cần; (6) `ALT_INSTANCE_NAME=crypto`, `ALTCOIN_ENABLED=1`, khởi động lại; (7) gửi tin chuyển đổi bằng **token bot OKX cũ** (curl tới `TELEGRAM_CHAT_ID` cũ) và bằng bot stock vào các chat báo cáo (§7.6); (8) khi đạt kiểm tra chấp nhận: **xoá service Render** và ngắt kết nối repo (§14). **Sau 1–2 tuần ổn định:** chỉ khi app chung là `vnbot`: gỡ endpoint hoặc deactivate app `okxbot` (app chung là `okxbot` thì **không bao giờ**); tuỳ chọn revoke token bot OKX; README "đã chuyển" + archive repo cũ (§3). | `/altcoin/hub.json` (Basic): `state=running`, `mode=paper`, `seam_ok=true`, `dropped_secrets` rỗng, `ignored_keys` đúng như dự kiến. `/health` (công khai) chỉ có `altcoin.state`. `/altcoin/phienban` báo 3.2.0, `380dfca`, "đúng bản phát hành". Equity, số trade, tham số đã học giống bản cũ. `heartbeats` chỉ có `crypto` còn sống (không `render`, không `crypto` từ host khác). **Cầu nối:** `/alt status` được trả lời trên Telegram và trên Zoom (`/vnbot alt status` hoặc `/okxbot alt status` trong kênh); `/trangthai` vẫn chạy; thông báo altcoin đầu tiên (lệnh paper hoặc `/alt zoomtest`) tới **đúng một lần** ở mỗi chat báo cáo và một lần trên Zoom, có tiền tố "🪙 Altcoin ›"; `/alt zoomtest` báo Zoom nhận. Không có `409` / `terminated by other getUpdates` trong 1 giờ log. Từ ngoài: `curl -X POST https://HOST/altcoin/_hub/command` và `https://HOST/altcoin/%5Fhub/command` → 404/400, có hay không có Basic; `https://HOST/_hub/command` → 404; cổng child không mở ra ngoài. p95 `/health` không đổi. RAM tăng < 200 MB. |
| **7. Gia cố** (mục nào sửa `bot/` thì chạy `python -m bot.version --stamp` trong `altcoin/`) | **Bắt buộc trước khi bật live:** vùng găng quanh `LiveEngine.open/partial/close`, hoãn `KeyboardInterrupt` do SIGTERM tới khi ra khỏi vùng. **Tuỳ chọn:** đưa `HubChannel` và `/_hub/command` vào `bot/` thành kênh "hub" chính thức (bỏ seam `bot.main.Telegram` và `build_app`); đưa seam vào `ui.py` (`WEB_PREFIX`) và sửa hộp "Cập nhật bot"; `config.py` bỏ qua `__BOTKEEP_KEEP_SECRET_`; thẻ Altcoin trên `/phienban` của stock; xoá dòng heartbeat `render` cũ (sau `pg_dump`). | Suite vẫn xanh. Build altcoin mới khớp trên Botkeep. Test vùng găng: gửi SIGTERM giữa `open` không bỏ dở bước ghi DB. |

**Rollback ở mọi giai đoạn:** đặt `ALTCOIN_ENABLED=0` rồi khởi động lại. `/alt` khi đó trả "Altcoin đang tắt"; stock không bị ảnh hưởng.

Từ Giai đoạn 6, muốn quay về server altcoin cũ:

- dừng child trước, rồi mới bật server cũ;
- server cũ vẫn dùng token bot OKX và app `okxbot`, nên chỉ revoke token / tắt app **sau** thời gian ổn định;
- nếu app Zoom chung là `okxbot`: trỏ lại URL của nó về server cũ; trong thời gian rollback, stock không nhận lệnh Zoom;
- cầu nối khi đó trả "Altcoin đang tắt", còn người dùng nhắn bot OKX như trước.

Supabase không phân kỳ dữ liệu; SQLite phân kỳ kể từ lúc chép.

**Quy tắc chạy cục bộ (Giai đoạn 3, 4, 5):** `TELEGRAM_TOKEN` trống hoặc là **bot thử**, `ZOOM_*` trống hoặc là app Zoom dev. Stock tự poll `getUpdates` khi có `TELEGRAM_TOKEN` (`telegram_bot.py:232-236`), nên một `.env` cục bộ mang token thật sẽ thành poller thứ hai: production bị "terminated by other getUpdates" và nghỉ 30 s mỗi lần (`:466-469`).

**Canary trên Botkeep (cuối Giai đoạn 5).** Không còn Render đỡ cut-over hỏng, nên các điểm chỉ Botkeep có được thử trước, khi server altcoin cũ vẫn chạy:

- Panel: `ALTCOIN_ENABLED=1`, `ALTCOIN_FRESH_START=1`, `ALT_MODE` trống (paper), **không** `ALT_DATABASE_URL` (SQLite), `ALT_DATA_DIR=<DATA_DIR>/altcoin-canary`, `ALT_INSTANCE_NAME=canary`; công tắc `altcoin` **tắt** trước khi khởi động lại, để chat thật không nhận tin canary. Lúc `ALTCOIN_ENABLED` còn 0, trang `/ai` chỉ hiện `_UNSET_BADGE`, nên tắt bằng `POST /channels/altcoin` (Basic + `Origin` đúng host; `NAMES` luôn có `altcoin`). Xong canary thì bật lại.
- DB riêng tạm nên canary không thấy heartbeat hay lock của server cũ, và không đụng DB thật.
- Chạy 15–30 phút, kiểm như cột chấp nhận của Giai đoạn 5; rồi đặt lại `ALTCOIN_ENABLED=0`, bỏ `ALTCOIN_FRESH_START`/`ALT_DATA_DIR`/`ALT_INSTANCE_NAME`, xoá `altcoin-canary/`, khởi động lại. Canary hỏng thì sửa trước khi lên lịch Giai đoạn 6.

---

## 16. Rủi ro và cách giảm

| Rủi ro | Cách giảm |
|---|---|
| Hai instance altcoin cùng chạy lúc cut-over (server cũ hoặc Render chưa tắt) → hai leader, giao dịch trùng | Tạm dừng Render và dừng server cũ trước khi bật. Chặn một instance qua `heartbeats` (75, tự thử lại), và `flock` `hosted.lock` cho hai child chồng nhau trên cùng host. |
| Lộ điểm nội bộ của cầu nối ra Internet (ví dụ chạy `close` không cần mật khẩu) | Thông báo đi qua pipe, không có địa chỉ. `/_hub/command` chỉ ở `127.0.0.1` của child, với token riêng không bao giờ đi qua proxy. Hub kiểm đường dẫn thô (400 cho `%5F`…), trả 404 `/altcoin/_hub` và `/altcoin/_hub/*` trên đường dẫn đã giải mã, và xoá `X-Hub-*` của client. Có test cho cả ba lớp. Giai đoạn 6 kiểm từ bên ngoài. |
| Gõ nhầm lệnh nguy hiểm (`/close` trần, `/alt close`, `/alt apply`) | Không chuyển lệnh trần hay lệnh lạ. `close`/`apply`/`resume` cần `xacnhan` trong 120 s từ cùng người gửi thật và cùng cuộc trò chuyện (Zoom theo JID). Lệnh ghi cần `ALTCOIN_ADMIN_IDS` / JID Zoom đã ghép đôi. Khoá lệnh ghi trong child: không bao giờ hai `close_all` song song, kể cả sau timeout. Không tự gửi lại. |
| Child lỡ cầm token kênh (từ `bot.env` cũ) → 409, stock "điếc" 30 s mỗi lần | `hosted.py` xoá `TELEGRAM_*`/`ZOOM_*` dù đến từ đâu. Env child không có chúng. Test chặn request tới `api.telegram.org`. `ignored_keys` hiện trên `/altcoin/hub.json`. Seam gãy cũng chỉ làm mất thông báo, không sinh 409. |
| Thông báo altcoin dồn dập → Telegram 429, làm chậm cả thông báo stock (hạn mức tính theo bot) | 15 tin/phút, burst 5, cách nhau ≥ 1 s; chống trùng 120 s; gộp phần thừa (tin 🛑/⚠️ không bị gộp). `_call` của stock đã chờ `retry_after`; tin vẫn lỗi được đếm `send_failed`. Có công tắc `altcoin`. |
| Mất thông báo altcoin (restart, hàng đợi đầy, `_start()` lỗi, công tắc tắt) | Reader pipe không bao giờ chặn (`put_nowait`). Đếm `dropped`/`muted`/`send_failed` trong `/altcoin/hub.json`; tin bị cầu nối bỏ có toàn văn trong `events` của stock; `events` của altcoin chỉ giữ 200 ký tự đầu. Chấp nhận mất trong lúc restart. |
| Lệnh `/alt` treo khi OKX chậm | Timeout 15/30/60 s. `close`/`apply` chạy ở thread riêng, nên lane của chat không bị giữ lâu. Lệnh ghi sau timeout vẫn chạy trong child và giữ khoá; lệnh ghi mới nhận "đang có lệnh"; kết quả từng lệnh đóng tới qua thông báo. |
| Người dùng cũ không nhận tin hoặc không ra lệnh được (chưa `/start` bot stock, bot stock chưa ở nhóm cũ, user id chưa được phép, trước đây xác thực theo chat id) | Checklist §7.6 ở Giai đoạn 6. Docs ghi rõ đổi cách xác thực. `/alt zoomtest` để kiểm. |
| Chọn sai app Zoom chung (stock đang "mượn" `okxbot` chỉ để gửi) | Giai đoạn 0 xác định app nào nhận lệnh; §17, mục 4 (Còn cần chốt) chốt. Nếu là `okxbot`: Giai đoạn 6 bước 3b trỏ URL về stock, ghép đôi lại trên stock, không bao giờ deactivate (§7.6). |
| Mất failover khi không còn Render | Chấp nhận theo quyết định 3. Altcoin chỉ ngừng khi server Botkeep ngừng, mà lúc đó stock cũng ngừng. Theo dõi bằng health check Botkeep, `/health` và `/altcoin/hub.json`. Các điểm chỉ Botkeep có được thử bằng canary cuối Giai đoạn 5, khi server cũ còn chạy. |
| Vòng qua đăng nhập bằng `..` / `%2e` (httpx chuẩn hoá dấu chấm) | Hub trả 400 cho đường dẫn thô bất thường (gồm `%XX` của ký tự không cần mã hoá), rồi so danh sách chính xác trên đường dẫn đã giải mã; có test vector. |
| Cùng DB stock qua endpoint khác (pooler/direct) → `prune()` xoá dữ liệu stock | Kiểm bảng `market`/`fills` trước `Store()`. Supervisor kiểm `trades`/`locks` trong DB stock. |
| Live không bao giờ khởi động (`I_UNDERSTAND_THE_RISK`) | `hosted.py` đặt biến này sau gate và chạy lại kiểm tra của `main()`; ma trận test chạy tới `Bot()`. |
| SIGTERM rơi vào giữa đặt lệnh và ghi DB; restart nay xảy ra theo mỗi lần deploy stock | Vùng găng ở Giai đoạn 7 là điều kiện để bật live. Tài liệu ghi rõ. |
| OOM lúc stock phân tích nặng giết cả hai | `oom_score_adj=800` cho child; đo RSS đỉnh ở Giai đoạn 3 và ở canary trên Botkeep; `rss_mb` trong `/altcoin/hub.json`; công tắc khẩn `ALTCOIN_ENABLED=0`. |
| Botkeep chạy Python 3.11 → stock lỗi SyntaxError | Giai đoạn 0 kiểm; builder từ chối < 3.12; `main_botkeep` báo lỗi rõ. |
| ccxt ghim cứng làm đổi thư viện stock dùng chung | Ghim `ccxt==4.5.90`; `pip --dry-run` + `run_all` mỗi lần nâng; `loop="asyncio"`. |
| Seam monkeypatch gãy khi altcoin đổi (`bot.main.Telegram`, `build_app`, `ui.page`, `ui.card`) | `suite_altcoin_web`/`host`/`bridge` ghim từng điểm. Tự kiểm cả hiệu lực (`bot.tg` là `HubChannel` trong `notify.channels`; `seam_ok`), thì chạy giảm chức năng; seam kênh gãy vẫn an toàn vì cfg kênh rỗng. Giai đoạn 7 đưa seam vào code. |
| `pass_fds`/pipe không chạy như mong đợi trên Botkeep | Thử cục bộ ở Giai đoạn 3 và trên Botkeep ở canary cuối Giai đoạn 5, trước khi dừng server cũ. Dự phòng: listener `127.0.0.1` + `HUB_CMD_TOKEN`, giữ nguyên giao thức khung (§7.4). |
| Spawn sai đường dẫn / nạp nhầm `.env` của stock | Đường dẫn tuyệt đối, `cwd=altcoin/`; test spawn từ `/` với `.env` mồi nhử. |
| Child mồ côi, tranh cổng, child chạy không có UI | PDEATHSIG từ thread sống lâu; so `--parent-pid`; cổng mới mỗi lần spawn; quá hạn sẵn sàng thì spawn lại. |
| `stop()` không chạy khi SIGTERM | Handler riêng cài trước `uvicorn.run`; hook lifespan; supervisor là daemon. |
| `/health` bị treo vì supervisor | Snapshot bất biến; test < 100 ms. |
| Bí mật bị che (`KEEP_SECRET`) mất âm thầm | WARNING + `dropped_secrets` trong `/altcoin/hub.json` và trên `/altcoin/`; tài liệu "nhập lại từ nguồn". |
| Lộ bí mật qua log của child, hoặc qua câu trả lời / thông báo của cầu nối | Che hai lớp; cha biết cả giá trị trong `bot.env`; stderr đi chung pipe; cầu nối qua `eventlog.redact()` trước `send`/`broadcast`. |
| Giờ phiên altcoin lệch 7 giờ (thiếu zoneinfo hệ thống) | Kiểm `tm_gmtoff`, fallback tzdata, nếu vẫn lệch thì thoát 78. |
| Botkeep không giải nén đúng zip không có mục thư mục | Thử ở Giai đoạn 1; cờ `--dir-entries`. |
| Botkeep không cài lại requirements | `ALTCOIN_AUTO_INSTALL=1` hoặc lệnh pip tay; trạng thái "thiếu thư viện" hiện trên UI. |
| Vượt 250 file / 4 MB về sau | `suite_bundle` đỏ khi vượt, cảnh báo ở 85 %; hiện dư ≈85 file / ≈1,45 MB. |
| Deploy cả repo qua GitHub | Không khả thi (≈268 mục); chỉ deploy zip do builder tạo. |
| Gộp IP client sau proxy → một lần khoá chặn mọi người (cả hai mục) | Kiểm ở Giai đoạn 0; `FORWARDED_ALLOW_IPS`. |
| Hạn mức OpenRouter free bị dùng chung | Log khi dùng fallback; đặt `ALT_OPENROUTER_API_KEY`. |
| Lộ trạng thái altcoin qua `/health` công khai (live hay paper, lệnh cuối, `<tên>@<host>`) | `/health` chỉ có `altcoin.state`; chi tiết ở `/altcoin/hub.json` sau Basic. |
| Chạy cục bộ bằng token Telegram thật → poller thứ hai, production nghỉ 30 s mỗi lần | Quy tắc chạy cục bộ dưới §15: token trống hoặc bot thử, `ZOOM_*` trống. |
| Mở rộng quyền: người dùng stock đóng được vị thế altcoin | `ALTCOIN_ADMIN_IDS` cho lệnh ghi; Zoom chỉ JID đã ghép đôi; docs ghi quyền đổi theo cả hai chiều. |

**Chưa kiểm chứng:**

- **Botkeep:** health probe, proxy/IP, ngữ nghĩa ghi đè khi upload, có đếm thư mục không, MB hay MiB, cách giải nén zip, Python, có cài lại requirements không, zoneinfo, `pass_fds` (dự kiến chạy được vì là Linux). Các điểm về tiến trình con được kiểm ở canary cuối Giai đoạn 5.
- **Kênh:** stock đang dùng app Zoom nào để nhận lệnh; altcoin có kênh Zoom riêng không; bot stock đã ở trong nhóm altcoin cũ chưa; hạn mức gửi thực tế khi stock và altcoin cùng báo trong một phút.
- **Hệ thống:** RAM đỉnh; hai bot hiện có đang dùng chung DB hay token không; Render có đang giữ lock trên Supabase không.
- **Trình duyệt:** một lần hỏi Basic trên Chrome và Safari.

---

## 17. Quyết định

### Đã chốt (2026-10-29)

- **A. Đăng nhập:** một mật khẩu `WEB_PASSWORD` cho cả hai mục, kiểm ở Hub. Cookie altcoin luôn tắt; `ALT_DASHBOARD_*` bị bỏ qua.
- **B. Kênh:** một bot Telegram và một app Zoom dùng chung. Chỉ stock nhận; lệnh altcoin qua `/alt`; thông báo altcoin qua pipe về stock; child không cầm token kênh.
- **C. Render:** không giữ. Một instance altcoin trên Botkeep; `render.yaml` xoá khỏi cây (còn trong lịch sử git); không peer, không failover.
- **D. Một repo, một server Botkeep:** toàn bộ `altcoin_bot` vào `stockvn/altcoin/`; một gói zip, một lệnh `python main_botkeep.py`; server altcoin cũ tắt ở Giai đoạn 6.

### Hệ quả đã cố định (không còn là lựa chọn)

- Python ≥ 3.12 trên Botkeep: stock bắt buộc.
- Deploy bằng zip do builder tạo, vì deploy cả repo vượt 250 mục.
- Một instance: chặn mọi instance khác qua `heartbeats` và `flock` (75).
- Thông báo altcoin đi qua stock, nên theo `redact()`, cắt tin và công tắc của stock. Công tắc kênh trong kv altcoin không còn tác dụng.
- Mất mát chấp nhận về kênh (§7.5): không còn `telegram_cmd`; không còn tổ hợp công tắc theo mục × kênh; kênh Zoom riêng của altcoin bỏ, trừ khi chọn giữ ở mục 20.
- Render không phải đường rollback; xoá ngay khi Giai đoạn 6 đạt.

### Còn cần anh/chị chốt (in đậm = khuyến nghị)

1. **Mô hình tiến trình:** **altcoin là tiến trình con có supervisor, stock giữ cổng.** Cách còn lại là chạy chung tiến trình: tiết kiệm ≈80 MB và cầu nối đơn giản hơn, nhưng phải sửa khoảng 15 file altcoin và tăng rủi ro cho stock.
2. **Tiền tố URL:** **`/altcoin/`**.
3. **Bot Telegram dùng chung là bot nào:** **bot stock (`TELEGRAM_TOKEN` hiện tại)**. Không đổi token và giữ mọi link, lịch sử chat của stock. Bot OKX ngừng dùng; tuỳ chọn revoke cuối Giai đoạn 6.
4. **App Zoom dùng chung là app nào:** **`vnbot`**, vì URL không đổi và không phải đăng ký lại; `okxbot` tắt sau 1–2 tuần ổn định. Ngoại lệ: Giai đoạn 0 có thể thấy stock chỉ đang "mượn" `okxbot` để gửi và chưa có app riêng nhận lệnh. Khi đó dùng `okxbot` làm app chung, theo §7.6 và Giai đoạn 6 bước 3b:
   - trỏ Bot endpoint, Event Subscription, Deauthorization, OAuth redirect và các trang công khai (`/docs`, `/privacy`, `/terms`, `/support`, `/data-deletion`) của nó về URL gốc của stock, theo danh sách trên trang `/zoom` của stock;
   - đặt `ZOOM_SECRET_TOKEN` của app đó cho stock;
   - ghép đôi trên stock, **rồi** mới xoá `ZOOM_TO_JID`;
   - lệnh trong kênh là `/okxbot alt …`; **không bao giờ** deactivate `okxbot`; rollback phải trỏ URL về server cũ.
5. **Cú pháp lệnh:** **`/alt <lệnh>` + bí danh `/altcoin`; `close`/`apply`/`resume` cần `xacnhan`; không bí danh ngắn; lệnh altcoin gõ trần chỉ nhận gợi ý.** Hoặc bỏ bước xác nhận cho `resume`.
6. **Chat nhận thông báo altcoin:** **chung các chat báo cáo của stock** (`ALTCOIN_REPORT_CHAT_IDS` để trống). Hoặc liệt kê chat riêng, ví dụ nhóm altcoin cũ; khi đó phải thêm bot stock vào nhóm.
7. **Database:** **altcoin giữ DB riêng** (Supabase hiện tại, nếu không có thì `data/altcoin/bot.db`).
8. **Nhánh `ccr-46f71b1d-mqcst3`:** **đưa vào** `altcoin/tools/`.
9. **`altcoin/dist/okx-ai-bot-v3.2.0.zip`:** **giữ tracked + `!altcoin/dist/`**. Hoặc chuyển sang GitHub Release.
10. **Lịch sử git:** **`git subtree` không squash**. Hoặc `filter-repo`.
11. **Nghĩa của "4 MB":** **4.000.000 B cho cả zip lẫn giải nén; file + thư mục ≤ 250.**
12. **`ALTCOIN_ENABLED`:** **mặc định 0 vĩnh viễn**, chỉ bật trên panel sau khi đã chép DB.
13. **Fallback AI:** **cho dùng `ROUTER9_*`/`OPENROUTER_API_KEY` của stock khi thiếu `ALT_*`** (có log). Hoặc bắt khai riêng.
14. **Tên miền cũ** (server Botkeep cũ, không liên quan Render): **chuyển hướng qua `ALTCOIN_LEGACY_HOSTS`** nếu trỏ được về server mới; nếu không thì bỏ cả code.
15. **Phiên bản:** **stock v1.15.0 ("Gộp bot Altcoin: mục /altcoin/, lệnh /alt"); altcoin giữ 3.2.0 / `380dfca`** tới Giai đoạn 7.
16. **Giấy phép:** **ghi rõ Apache-2.0 của repo áp dụng cho `altcoin/`** (hiện altcoin chưa có giấy phép).
17. **ccxt:** **ghim `4.5.90` ở cả hai `requirements.txt`**, chấp nhận hạ `idna`/`charset_normalizer` của stock.
18. **`ALTCOIN_AUTO_INSTALL`:** **mặc định tắt**; bật nếu Botkeep không cài lại requirements khi cập nhật.
19. **Live:** **chỉ bật sau mục vùng găng của Giai đoạn 7.** Không còn điều kiện về Render hay peer.
20. **Kênh Zoom riêng của altcoin** (nếu Giai đoạn 0 thấy đang dùng `ZOOM_WEBHOOK_URL`/`ZOOM_TO_JID` riêng): **bỏ, tin altcoin đi theo Zoom của stock**. Hoặc thêm `ALTCOIN_ZOOM_WEBHOOK_URL`/`_TOKEN` do stock đọc và gửi.
21. **Quyền lệnh ghi altcoin:** **`ALTCOIN_ADMIN_IDS` trống (= `TELEGRAM_ALLOWED_IDS`) nếu mọi người dùng stock đều nên có quyền đóng vị thế altcoin; nếu không thì liệt kê**. Zoom: luôn chỉ JID đã ghép đôi.
