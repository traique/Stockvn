"""Kiểm tra kết nối Supabase/Postgres trước khi deploy:  python check_db.py
Đọc DATABASE_URL từ .env hoặc biến môi trường."""
import os
import sys
import time
from dotenv import load_dotenv

load_dotenv()
url = os.getenv("DATABASE_URL", "").strip()
if not url:
    sys.exit("Chưa có DATABASE_URL trong .env")
if "pooler.supabase.com" not in url and "supabase.co" in url:
    print("⚠️  Đang dùng kết nối TRỰC TIẾP của Supabase (db.xxx.supabase.co) – chỉ có IPv6, Render free sẽ KHÔNG kết nối được.")
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
    print("   Ghi/xoá bảng thử thành công – bot sẽ chạy được với DATABASE_URL này.")
except Exception as e:
    print("❌ Lỗi:", e)
    print("   Kiểm tra: mật khẩu có ký tự đặc biệt phải mã hoá URL (@ → %40, # → %23, / → %2F...),")
    print("   user dạng postgres.<project-ref> khi dùng pooler, và project Supabase chưa bị tạm dừng (pause).")
