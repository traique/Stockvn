"""Kiểm tra kết nối Supabase/Postgres trước khi deploy:  python check_db.py
Đọc ALT_DATABASE_URL (ưu tiên) hoặc DATABASE_URL từ .env / bot.env CẠNH file này (bot.env thắng .env,
giá trị trong file thắng biến môi trường, như bot/config.py); từ biến môi trường chỉ lấy ALT_DATABASE_URL.
Không đi ngược lên .env ở gốc Stockvn và không lấy DATABASE_URL của môi trường: trong Stockvn đó là DB
của bot cổ phiếu, kiểm nhầm DB stock."""
import os
import sys
import time
from pathlib import Path

from dotenv import dotenv_values

env, source = {"ALT_DATABASE_URL": os.environ.get("ALT_DATABASE_URL", "")}, {}  # DATABASE_URL môi trường = stock
for name in (".env", "bot.env"):                     # bot.env nạp sau → thắng .env
    path = Path(__file__).resolve().parent / name
    if path.is_file():
        for k, v in dotenv_values(path).items():
            v = (v or "").strip()
            if k and v and not (v.startswith("<") and v.endswith(">")):
                env[k], source[k] = v, name
key = next((k for k in ("ALT_DATABASE_URL", "DATABASE_URL") if (env.get(k) or "").strip()), "")
if not key:
    sys.exit("Chưa có ALT_DATABASE_URL / DATABASE_URL trong .env, bot.env (cạnh check_db.py) hay ALT_DATABASE_URL "
             "trong biến môi trường (DATABASE_URL của môi trường là DB stock: bỏ qua)")
url = env[key].strip()
print(f"Kiểm tra {key} (từ {source.get(key, 'biến môi trường')})")
if "pooler.supabase.com" not in url and "supabase.co" in url:
    print("⚠️  Đang dùng kết nối TRỰC TIẾP của Supabase (db.xxx.supabase.co) – chỉ có IPv6, máy không có IPv6 sẽ KHÔNG kết nối được.")
    print("    Hãy lấy chuỗi 'Session pooler' (aws-0-<region>.pooler.supabase.com:5432) trong Supabase → Connect.")
if "sslmode=" not in url:
    url += ("&" if "?" in url else "?") + "sslmode=require"
try:
    import psycopg
except ImportError:
    sys.exit("Thiếu psycopg: pip install 'psycopg[binary]'")
t = time.time()
try:
    with psycopg.connect(url, connect_timeout=20, prepare_threshold=None) as conn:
        v = conn.execute("select version()").fetchone()[0]
        conn.execute("create table if not exists _ping(ts double precision)")
        conn.execute("insert into _ping values(%s)", (time.time(),))
        conn.execute("drop table _ping")
        conn.commit()
    print(f"✅ Kết nối OK ({time.time()-t:.1f}s): {v[:60]}")
    print(f"   Ghi/xoá bảng thử thành công – bot sẽ chạy được với {key} này.")
except Exception as e:
    print("❌ Lỗi:", e)
    print("   Kiểm tra: mật khẩu có ký tự đặc biệt phải mã hoá URL (@ → %40, # → %23, / → %2F...),")
    print("   user dạng postgres.<project-ref> khi dùng pooler, và project Supabase chưa bị tạm dừng (pause).")
