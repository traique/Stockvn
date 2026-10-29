"""Giao diện web: 1 stylesheet, khung trang và vài thành phần nhỏ. Render phía server, không cần build.

Phong cách "champagne trên obsidian" (theo hướng gói tham khảo): giấy ngà + mực đen với điểm nhấn vàng
champagne (sáng), đen obsidian cùng tông vàng (tối); tiêu đề Playfair Display, chữ Be Vietnam Pro.
Giao diện theo thiết bị, đổi được bằng nút trên đầu trang (nhớ trong trình duyệt, nếu được).
Trạng thái không chỉ dựa vào màu: luôn kèm chữ/biểu tượng (▲▼, "Bật/Tắt", nhãn).
"""
import html

e = html.escape

FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700'
         '&family=Playfair+Display:wght@500;600;700&display=swap" rel="stylesheet">')

THEME_BOOT = ("<script>(function(){try{var t=localStorage.getItem('okx-theme');"
              "if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t);}catch(e){}})();</script>")

THEME_TOGGLE = """<script>
(function(){
  var b=document.getElementById('theme-toggle');if(!b)return;
  function cur(){var t=document.documentElement.getAttribute('data-theme');
    if(t)return t;return window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}
  function label(){b.setAttribute('aria-label',cur()==='dark'?'Chuyển sang giao diện sáng':'Chuyển sang giao diện tối');}
  label();
  b.addEventListener('click',function(){var n=cur()==='dark'?'light':'dark';
    document.documentElement.setAttribute('data-theme',n);try{localStorage.setItem('okx-theme',n);}catch(e){}label();
    window.dispatchEvent(new Event('themechange'));});
})();
</script>"""

_LIGHT = """
  --bg:#F6F4EF; --bg-glow:#EFE4C8; --surface:#FFFFFF; --surface-2:#F4F1EA; --surface-3:#ECE7DC;
  --ink:#14161B; --ink-2:#464A53; --muted:#6F737D; --line:#E7E2D7; --line-strong:#D5CDBC;
  --gold:#B8963E; --gold-2:#E3C979; --gold-ink:#1A1505; --brand-text:#7E6014; --brand-soft:#F5EDD8;
  --up:#1E7A4C; --up-soft:#E2F2E9; --down:#B3362B; --down-soft:#FAE5E2; --warn:#8A5D08; --warn-soft:#F7EDD4;
  --neutral-soft:#EFECE6; --info:#2A5FA8; --info-soft:#E6EEF8;
  --shadow:0 1px 2px rgba(20,22,27,.04),0 12px 32px -12px rgba(20,22,27,.12);
  --hair:linear-gradient(90deg,transparent,rgba(184,150,62,.55),transparent);
"""

_DARK = """
  --bg:#0B0D12; --bg-glow:#1D1A10; --surface:#14171F; --surface-2:#1A1E28; --surface-3:#222734;
  --ink:#ECEEF2; --ink-2:#B9BEC8; --muted:#8A909C; --line:#252A36; --line-strong:#333A49;
  --gold:#C9A445; --gold-2:#EBD38A; --gold-ink:#17130A; --brand-text:#E2C470; --brand-soft:#2A2414;
  --up:#5CC795; --up-soft:#13281E; --down:#F0857A; --down-soft:#2E1716; --warn:#E6B65A; --warn-soft:#2B2414;
  --neutral-soft:#1F232D; --info:#7FB0EE; --info-soft:#16223A;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 18px 40px -18px rgba(0,0,0,.7);
  --hair:linear-gradient(90deg,transparent,rgba(212,180,90,.5),transparent);
"""

# --mut/--dn/--card/--bg2/--ink2: tên ngắn dùng trong script vẽ dữ liệu
_ALIASES = "--mut:var(--muted);--dn:var(--down);--card:var(--surface);--bg2:var(--surface-2);--ink2:var(--ink-2);"

STYLE = (
    ":root{" + _LIGHT + _ALIASES + "color-scheme:light}"
    "@media (prefers-color-scheme:dark){:root:not([data-theme=light]){" + _DARK + "color-scheme:dark}}"
    ":root[data-theme=dark]{" + _DARK + "color-scheme:dark}"
    + """
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  background-image:radial-gradient(900px 420px at 90% -8%,var(--bg-glow),transparent 62%),
    radial-gradient(700px 360px at -10% 0%,color-mix(in srgb,var(--bg-glow) 45%,transparent),transparent 60%);
  background-attachment:fixed;font:15px/1.6 "Be Vietnam Pro",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased}
a{color:var(--brand-text);text-decoration:none}a:hover{text-decoration:underline;text-underline-offset:3px}
:focus-visible{outline:2px solid var(--gold);outline-offset:2px;border-radius:6px}
.skip{position:absolute;left:-9999px;top:8px;z-index:30;background:var(--surface);padding:8px 12px;border-radius:10px;border:1px solid var(--gold)}
.skip:focus{left:12px}
.num,.stat .v,.kpi .v,td.num,h1,.card-h h2{font-variant-numeric:lining-nums tabular-nums}
::selection{background:var(--brand-soft)}
/* header */
.top{position:sticky;top:0;z-index:10;background:color-mix(in srgb,var(--bg) 80%,transparent);
  backdrop-filter:saturate(1.3) blur(14px);-webkit-backdrop-filter:saturate(1.3) blur(14px)}
.top::after{content:"";position:absolute;left:0;right:0;bottom:0;height:1px;background:var(--hair)}
.top-in{max-width:1120px;margin:0 auto;padding:12px 16px;display:flex;align-items:center;gap:14px}
.brand{display:flex;align-items:center;gap:11px;color:var(--ink);flex:none}
.brand:hover{text-decoration:none}
.mark{width:38px;height:38px;border-radius:11px;display:grid;place-items:center;flex:none;
  background:linear-gradient(135deg,var(--gold),var(--gold-2));color:var(--gold-ink);font:700 13px/1 "Be Vietnam Pro",sans-serif;letter-spacing:.04em}
.brand-name{font:600 17px/1.1 "Playfair Display",Georgia,serif;letter-spacing:.01em}
.brand small{display:block;font:600 10px/1.4 "Be Vietnam Pro",sans-serif;color:var(--muted);letter-spacing:.18em;text-transform:uppercase;margin-top:3px}
.nav{display:flex;gap:2px;margin-left:auto;min-width:0;overflow-x:auto;scrollbar-width:none}.nav::-webkit-scrollbar{display:none}
.nav a{position:relative;padding:8px 13px;border-radius:10px;color:var(--ink-2);font-weight:500;font-size:14px;white-space:nowrap}
.nav a:hover{text-decoration:none;color:var(--ink);background:var(--surface-2)}
.nav a[aria-current=page]{color:var(--ink)}
.nav a[aria-current=page]::after{content:"";position:absolute;left:13px;right:13px;bottom:2px;height:2px;border-radius:2px;
  background:linear-gradient(90deg,var(--gold),var(--gold-2))}
.icon-btn{width:38px;height:38px;border-radius:11px;border:1px solid var(--line);background:var(--surface);color:var(--ink-2);
  display:grid;place-items:center;cursor:pointer;flex:none;transition:border-color .15s,color .15s}
.icon-btn:hover{color:var(--brand-text);border-color:var(--gold)}
.icon-btn svg{width:18px;height:18px}
.i-sun{display:none}:root[data-theme=dark] .i-sun{display:block}:root[data-theme=dark] .i-moon{display:none}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]) .i-sun{display:block}:root:not([data-theme=light]) .i-moon{display:none}}
.ver-pill{flex:none;font:600 11px/1 "Be Vietnam Pro",sans-serif;letter-spacing:.04em;color:var(--brand-text);
  background:var(--brand-soft);border:1px solid var(--gold);border-radius:999px;padding:5px 9px;white-space:nowrap}
.ver-pill:hover{text-decoration:none;border-color:var(--gold-2)}
.ver-pill.warn{color:var(--down);background:var(--down-soft);border-color:var(--down)}
/* layout */
main{max-width:1120px;margin:0 auto;padding:28px 16px 56px}
.hero{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:16px;margin:0 0 22px}
.eyebrow{font:600 11px/1 "Be Vietnam Pro",sans-serif;letter-spacing:.2em;text-transform:uppercase;color:var(--brand-text);
  display:flex;align-items:center;gap:10px;margin-bottom:12px}
.eyebrow::before{content:"";width:28px;height:1px;background:linear-gradient(90deg,var(--gold),transparent)}
h1{font:600 38px/1.1 "Playfair Display",Georgia,serif;letter-spacing:-.015em;margin:0}
.lead{color:var(--ink-2);margin:10px 0 0;max-width:720px;font-size:15px}
h2{font-size:15px;font-weight:600;margin:0}
h3{font:600 11px/1 "Be Vietnam Pro",sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin:20px 0 10px}
.grid{display:grid;gap:18px;align-items:start}
.grid>*{min-width:0}
.g2{grid-template-columns:repeat(2,minmax(0,1fr))}.g4{grid-template-columns:repeat(4,minmax(0,1fr))}
.g-wide{grid-template-columns:minmax(0,1.35fr) minmax(0,1fr)}
@media (max-width:880px){.g4{grid-template-columns:repeat(2,minmax(0,1fr))}.g-wide{grid-template-columns:1fr}}
@media (max-width:620px){.g2{grid-template-columns:1fr}h1{font-size:28px}
  .top-in{flex-wrap:wrap;gap:8px 10px;padding:10px 16px 6px}.brand-name,.brand small{display:none}
  .nav{order:3;flex-basis:100%;margin-left:-6px}.nav a{padding:9px 10px;font-size:13.5px}
  #theme-toggle{margin-left:auto}main{padding-top:20px}
  .stat{padding:14px 16px}.stat .v{font-size:20px}.card-h{padding:16px 16px 12px}.card-b{padding:4px 16px 16px}}
.mt{margin-top:18px}
.card{position:relative;background:var(--surface);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow);overflow:hidden}
.card::before{content:"";position:absolute;left:24px;right:24px;top:0;height:1px;background:var(--hair);opacity:.8}
.card-h{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:18px 20px 12px;flex-wrap:wrap}
.card-h h2{font:600 18px/1.25 "Playfair Display",Georgia,serif}
.card-h .sub{color:var(--muted);font-size:13px;margin-top:3px}
.card-b{padding:4px 20px 18px}
.card-b.flush{padding:0;border-top:1px solid var(--line)}
.muted{color:var(--muted)}.small{font-size:13px}
.up,.pos{color:var(--up)}.dn,.neg{color:var(--down)}
/* stat strip & KPI */
.strip{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));background:var(--surface);border:1px solid var(--line);
  border-radius:18px;box-shadow:var(--shadow);margin-bottom:18px;overflow:hidden}
.stat{padding:16px 20px;position:relative;min-width:0}
.stat+.stat::before{content:"";position:absolute;left:0;top:18px;bottom:18px;width:1px;background:var(--line)}
.stat .l,.kpi .l{font-size:11px;color:var(--muted);font-weight:600;letter-spacing:.12em;text-transform:uppercase}
.stat .v{font:600 26px/1.2 "Playfair Display",Georgia,serif;margin-top:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.stat .v small{font:500 13px/1 "Be Vietnam Pro",sans-serif;color:var(--muted);margin-left:4px}
.stat .s,.kpi .s{font-size:12.5px;color:var(--muted)}
@media (max-width:880px){.strip{grid-template-columns:repeat(2,minmax(0,1fr))}.stat:nth-child(3)::before{display:none}
  .stat:nth-child(n+3){border-top:1px solid var(--line)}}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
@media (max-width:880px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
.kpi{background:var(--surface-2);border:1px solid var(--line);border-radius:14px;padding:12px 14px;min-width:0}
.kpi .v{font:600 20px/1.25 "Playfair Display",Georgia,serif;margin-top:5px;overflow:hidden;text-overflow:ellipsis}
.meter,.bar{height:8px;border-radius:999px;background:var(--surface-3);overflow:hidden;margin-top:8px}
.meter>span,.bar>i{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,var(--gold),var(--gold-2))}
/* badges, pills */
.badge{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;
  font:600 11.5px/1.7 "Be Vietnam Pro",sans-serif;letter-spacing:.04em;white-space:nowrap}
.badge::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.b-ok{background:var(--up-soft);color:var(--up)}.b-err{background:var(--down-soft);color:var(--down)}
.b-warn{background:var(--warn-soft);color:var(--warn)}.b-none{background:var(--neutral-soft);color:var(--muted)}
.b-gold{background:var(--brand-soft);color:var(--brand-text)}
.pill{display:inline-block;font-size:11px;padding:3px 9px;border-radius:999px;border:1px solid var(--line);background:var(--surface-2);
  color:var(--ink-2);letter-spacing:.03em;font-weight:600;white-space:nowrap}
.pill.gold{border-color:var(--gold);color:var(--brand-text);background:var(--brand-soft)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.side{font-size:10.5px;font-weight:700;padding:2px 8px;border-radius:6px;letter-spacing:.06em}
.side.long{background:var(--up-soft);color:var(--up)}.side.short{background:var(--down-soft);color:var(--down)}
.out{font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:6px;letter-spacing:.04em;white-space:nowrap}
.out.TP{background:var(--up-soft);color:var(--up)}.out.SL{background:var(--down-soft);color:var(--down)}
.out.TRAIL{background:var(--brand-soft);color:var(--brand-text)}
.out.TIMEOUT,.out.PENDING,.out.NODATA{background:var(--neutral-soft);color:var(--ink-2)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--up);margin-right:6px;vertical-align:1px}
.dot.off{background:var(--muted)}.dot.warn{background:var(--warn)}
/* alerts */
.alert{padding:12px 16px;border-radius:14px;margin-bottom:18px;font-size:14px}
.alert-err{background:var(--down-soft);color:var(--down)}.alert-ok{background:var(--up-soft);color:var(--up)}
.alert-info{background:var(--brand-soft);color:var(--brand-text)}
/* buttons */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;font:600 14px/1 "Be Vietnam Pro",sans-serif;
  padding:12px 18px;border-radius:12px;border:1px solid transparent;cursor:pointer;white-space:nowrap;min-height:44px;
  transition:filter .15s,border-color .15s,transform .05s}
.btn:active{transform:translateY(1px)}
.btn-primary{background:linear-gradient(135deg,var(--gold),var(--gold-2));color:var(--gold-ink)}
.btn-primary:hover{filter:brightness(1.05);text-decoration:none}
.btn-ghost{background:transparent;color:var(--ink);border-color:var(--line-strong)}
.btn-ghost:hover{border-color:var(--gold);color:var(--brand-text);text-decoration:none}
.btn-danger{background:transparent;color:var(--down);border-color:var(--down)}
.act{white-space:nowrap;border:1px solid var(--line-strong);background:var(--surface);color:var(--ink);border-radius:10px;
  padding:8px 12px;font:600 13px/1 "Be Vietnam Pro",sans-serif;cursor:pointer;min-height:36px}
.act:hover{border-color:var(--gold);color:var(--brand-text)}
.act.gold{background:linear-gradient(135deg,var(--gold),var(--gold-2));color:var(--gold-ink);border:0}
.act.warn{color:var(--down)}
/* on/off switch (form – chạy cả khi tắt JavaScript) */
.switch{display:inline-flex;align-items:center;gap:10px;border:0;background:none;padding:6px 0;cursor:pointer;
  font:600 13px/1 "Be Vietnam Pro",sans-serif;color:var(--muted);min-height:44px}
.switch .knob{position:relative;width:46px;height:26px;border-radius:999px;background:var(--surface-3);
  border:1px solid var(--line-strong);transition:background .2s,border-color .2s;flex:none}
.switch .knob::after{content:"";position:absolute;top:2px;left:2px;width:20px;height:20px;border-radius:50%;
  background:var(--surface);box-shadow:0 1px 3px rgba(0,0,0,.3);transition:transform .2s}
.switch.on{color:var(--up)}
.switch.on .knob{background:linear-gradient(135deg,var(--gold),var(--gold-2));border-color:transparent}
.switch.on .knob::after{transform:translateX(20px)}
.switch:focus-visible{outline:none}.switch:focus-visible .knob{box-shadow:0 0 0 4px var(--brand-soft);border-color:var(--gold)}
.switch .st{min-width:28px;text-align:left}
/* tables */
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:14px}
th{font-size:10.5px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.14em;text-align:left;
  padding:10px 16px;white-space:nowrap;border-bottom:1px solid var(--line);background:var(--surface-2)}
td{padding:12px 16px;border-top:1px solid var(--line);vertical-align:middle}
tbody tr:first-child td{border-top:0}
td.num,th.num{text-align:right;white-space:nowrap}
td code,p code,li code{font-size:12.5px;background:var(--surface-2);padding:2px 7px;border-radius:6px;border:1px solid var(--line)}
.mini{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
.mini td,.mini th{padding:8px 16px;border-top:1px solid var(--line);text-align:right;white-space:nowrap;background:none}
.mini td:first-child,.mini th:first-child{text-align:left;white-space:normal}
/* lists (dữ liệu sống) */
.list{display:flex;flex-direction:column}
.row{display:grid;grid-template-columns:1fr auto;gap:4px 12px;padding:12px 20px;border-top:1px solid var(--line);align-items:center}
.row .t{font-weight:600;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.row .m{font-size:12.5px;color:var(--ink-2);grid-column:1/-1;line-height:1.5}
.row .r{text-align:right;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
.row .r small{display:block;font-size:11px;color:var(--muted);font-weight:500}
.empty{padding:22px 20px;color:var(--muted);font-size:13.5px;text-align:center;border-top:1px solid var(--line)}
.skel{height:14px;border-radius:7px;background:var(--surface-3);margin:14px 20px;animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{50%{opacity:.45}}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
.tabs{display:flex;gap:6px;padding:0 20px 12px;overflow:auto;scrollbar-width:none}
.tab{border:1px solid var(--line);background:var(--surface-2);color:var(--ink-2);border-radius:999px;padding:8px 13px;font:600 12.5px/1 "Be Vietnam Pro",sans-serif;cursor:pointer;white-space:nowrap;min-height:36px}
.tab[aria-selected=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.filters{display:flex;gap:8px;padding:0 20px 12px}
.filters select,.filters input{border:1px solid var(--line-strong);background:var(--surface-2);color:var(--ink);border-radius:10px;padding:9px 11px;font:inherit;font-size:13.5px;min-width:0}
.filters input{flex:1}
.ev{font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;padding:8px 20px;border-top:1px solid var(--line);white-space:pre-wrap;word-break:break-word}
.ev .lv{font-weight:700;margin-right:6px}.ev .lv.WARNING{color:var(--warn)}.ev .lv.ERROR,.ev .lv.CRITICAL{color:var(--down)}.ev .lv.INFO{color:var(--muted)}
details{border-top:1px solid var(--line)}
summary{padding:12px 20px;cursor:pointer;font-size:13.5px;font-weight:600;color:var(--ink-2);list-style:none}
summary::-webkit-details-marker{display:none}summary::after{content:"▾";float:right;color:var(--muted)}details[open] summary::after{content:"▴"}
.note{padding:0 20px 14px;font-size:13.5px;color:var(--ink-2);line-height:1.6}
/* chart */
.chart{padding:0 12px 10px;position:relative}
.chart svg{width:100%;height:220px;display:block;touch-action:none}
.tip{position:absolute;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--line-strong);box-shadow:var(--shadow);
  font-size:12px;padding:6px 9px;border-radius:10px;transform:translate(-50%,-120%);white-space:nowrap;opacity:0;transition:opacity .1s;font-variant-numeric:tabular-nums}
/* settings rows */
.setting{display:grid;grid-template-columns:1fr auto;gap:4px 16px;align-items:center;padding:12px 20px;border-top:1px solid var(--line)}
.setting:first-child{border-top:0}
.setting .n{font-weight:600}.setting .d{font-size:13px;color:var(--muted)}
.dls{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.dl{display:flex;flex-direction:column;gap:2px;color:var(--ink);border:1px solid var(--line);background:var(--surface-2);border-radius:12px;padding:11px 13px;font-size:13.5px;font-weight:600;min-height:44px}
.dl:hover{border-color:var(--gold);text-decoration:none}
.dl small{color:var(--muted);font-weight:500;font-size:11.5px}
ol.steps,ul.notes{margin:0;padding-left:20px}ol.steps li,ul.notes li{margin:4px 0}
footer{max-width:1120px;margin:0 auto;padding:0 16px 36px;color:var(--muted);font-size:12px;display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px}
footer::before{content:"";flex-basis:100%;height:1px;background:var(--hair);margin-bottom:14px}
"""
)

SUN = ('<svg class="i-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">'
       '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2'
       'M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>')
MOON = ('<svg class="i-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">'
        '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>')

NAV = (("home", "./", "Tổng quan"), ("ai", "ai", "AI & Tự học"), ("settings", "caidat", "Cài đặt"),
       ("version", "phienban", "Phiên bản"))


def page(title, body, *, active="", mode="paper", scripts=""):
    """Khung 1 trang riêng tư (đã đăng nhập)."""
    from . import version as ver
    ok = ver.is_release()
    pill = (f'<a class="ver-pill{"" if ok else " warn"}" href="phienban" title="{e(ver.label())}">'
            f'v{e(ver.VERSION)}{"" if ok else " ⚠"}</a>')
    links = "".join(f'<a href="{href}"{" aria-current=page" if key == active else ""}>{label}</a>'
                    for key, href, label in NAV)
    mode_txt = "Chạy ảo – không dùng tiền thật" if mode == "paper" else "GIAO DỊCH THẬT"
    return (
        '<!doctype html><html lang="vi"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="theme-color" content="#F6F4EF" media="(prefers-color-scheme: light)">'
        '<meta name="theme-color" content="#0B0D12" media="(prefers-color-scheme: dark)">'
        f"<title>{e(title)} · OKX Altcoin Bot</title>{THEME_BOOT}{FONTS}<style>{STYLE}</style></head><body>"
        '<a class="skip" href="#main">Bỏ qua tới nội dung</a>'
        '<header class="top"><div class="top-in">'
        '<a class="brand" href="./"><span class="mark" aria-hidden="true">OKX</span>'
        '<span class="brand-name">OKX Altcoin Bot<small>Futures · AI · Tự học</small></span></a>'
        f'{pill}<nav class="nav" aria-label="Trang">{links}</nav>'
        f'<button class="icon-btn" id="theme-toggle" type="button" aria-label="Đổi giao diện sáng/tối">{MOON}{SUN}</button>'
        f'</div></header><main id="main">{body}</main>'
        f'<footer><span>{e(mode_txt)} · Thông tin chỉ mang tính tham khảo, không phải lời khuyên đầu tư.</span>'
        f'<span>OKX Altcoin Bot {e(ver.label())} · <a href="phienban">lịch sử phiên bản</a> · '
        '<a href="zoom">Zoom</a> · <a href="docs">Hướng dẫn</a> · <a href="privacy">Quyền riêng tư</a></span></footer>'
        f"{THEME_TOGGLE}{scripts}</body></html>")


def hero(title, lead="", right="", *, eyebrow="", lead_id=""):
    top = f'<div class="eyebrow">{eyebrow}</div>' if eyebrow else ""
    lid = f' id="{lead_id}"' if lead_id else ""
    return (f'<div class="hero"><div>{top}<h1>{title}</h1>'
            + (f'<p class="lead"{lid}>{lead}</p>' if lead or lead_id else "") + f"</div>{right}</div>")


def strip(items):
    """(nhãn, giá trị html, phụ html)."""
    return '<div class="strip">' + "".join(
        f'<div class="stat"><div class="l">{label}</div><div class="v">{value}</div>'
        + (f'<div class="s">{sub}</div>' if sub else "") + "</div>" for label, value, sub in items) + "</div>"


def card(title, body, *, sub="", action="", flush=False, cls="", sub_id="", body_id=""):
    sid = f' id="{sub_id}"' if sub_id else ""
    head = (f'<div class="card-h"><div><h2>{title}</h2>'
            + (f'<div class="sub"{sid}>{sub}</div>' if sub or sub_id else "")
            + f"</div>{action}</div>") if title else ""
    bid = f' id="{body_id}"' if body_id else ""
    return f'<section class="card {cls}">{head}<div class="card-b{" flush" if flush else ""}"{bid}>{body}</div></section>'


def alert(text, kind="info"):
    return f'<div class="alert alert-{kind}" role="status">{e(text)}</div>' if text else ""


def switch_form(key, on, label):
    """Công tắc bật/tắt: 1 form POST tới /caidat/kenh/<key> (chạy được cả khi tắt JavaScript)."""
    state = "Đang bật · chạm để tắt" if on else "Đang tắt · chạm để bật"
    return (f'<form method="post" action="caidat/kenh/{e(key)}" style="margin:0;display:inline-flex">'
            f'<input type="hidden" name="on" value="{0 if on else 1}">'
            f'<button class="switch{" on" if on else ""}" type="submit" role="switch" aria-checked="{"true" if on else "false"}" '
            f'aria-label="{e(label)}: {state}" title="{state}"><span class="knob" aria-hidden="true"></span>'
            f'<span class="st">{"Bật" if on else "Tắt"}</span></button></form>')


def table(headers, rows, empty):
    head = "".join(f'<th class="{c}" scope="col">{h}</th>' for h, c in headers)
    body = "".join(rows) or f'<tr><td class="empty" colspan="{len(headers)}">{empty}</td></tr>'
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
