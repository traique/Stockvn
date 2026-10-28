"""Kiểm tra DATABASE_URL (PostgreSQL dùng chung, ví dụ Supabase) trước khi đặt lên Botkeep:

    python check_db.py

Đọc DATABASE_URL từ biến môi trường hoặc file .env cạnh file này, kết nối, tạo một bảng thử,
ghi, đọc rồi xoá nó. Không đặt DATABASE_URL thì bot dùng SQLite (data/bot.db): không cần kiểm tra.
"""

import os
import sys
import time

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def main() -> int:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        print("Chưa có DATABASE_URL (trong .env hoặc biến môi trường): bot sẽ dùng SQLite data/bot.db.")
        return 1
    if "supabase.co" in url and "pooler.supabase.com" not in url:
        print("⚠️  Đây là kết nối TRỰC TIẾP của Supabase (db.<ref>.supabase.co): chỉ có IPv6, nhiều máy chủ không ra được.")
        print("    Lấy chuỗi 'Session pooler' (aws-0-<vùng>.pooler.supabase.com:5432) trong Supabase -> Connect.")
    if "sslmode=" not in url and "host=/" not in url and "localhost" not in url and "127.0.0.1" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    try:
        import psycopg
    except ImportError:
        print("Thiếu thư viện psycopg: pip install 'psycopg[binary]' (đã có trong requirements.txt).")
        return 1
    started = time.time()
    try:
        with psycopg.connect(url, connect_timeout=20, prepare_threshold=None) as conn:
            version = conn.execute("select version()").fetchone()[0]
            conn.execute("create table if not exists _vnbot_ping (ts double precision)")
            conn.execute("insert into _vnbot_ping values (%s)", (time.time(),))
            conn.execute("select count(*) from _vnbot_ping").fetchone()
            conn.execute("drop table _vnbot_ping")
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - said to the person running it
        print("❌ Lỗi:", type(exc).__name__, str(exc).replace(url, "<DATABASE_URL>")[:300])
        print("   Kiểm tra: mật khẩu có ký tự đặc biệt phải mã hoá URL (@ -> %40, # -> %23, / -> %2F), user dạng")
        print("   postgres.<ref> khi dùng pooler, và project Supabase chưa bị tạm dừng (pause sau 7 ngày không dùng).")
        return 2
    print(f"✅ Kết nối được ({time.time() - started:.1f} giây): {version[:60]}")
    print("   Tạo, ghi, đọc, xoá bảng thử thành công: bot sẽ lưu lịch sử vào PostgreSQL này.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
