"""The look of the web app: one stylesheet, a page shell and small components.

Server-rendered, no build step. The LCTA look of the owner's portfolio app: a soft
lavender page, white rounded cards and tiles, one navy accent for what is selected,
bold uppercase section titles over a quiet label, and the money in large figures.
Light and dark themes follow the device and can be switched in the header (the choice
is kept in the browser, best effort).

Design contract: mobile first (one column to 768 px, then two where it helps); spacing on
a 4 px scale; semantic colour tokens only; every control a real <button>, <a> or form
field with a visible label and a focus ring; touch targets of 44 px; gains and losses
carry a sign and a word, never colour alone. The header carries the date (solar and
lunar), the market's phase, the theme switch and the version.

Chart colours: the portfolio line is red-pink and VNINDEX orange, as in the owner's
app; each line has a legend entry and the numbers are in the tooltip and the chips, so
identity never rests on colour alone.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

STATIC_DIR = Path(__file__).with_name("static")
STATIC_FILES = ("logo.png", "favicon.png")
LOGO = '<img class="logo" src="/static/logo.png" alt="LCTA" width="62" height="44">'
FAVICON = '<link rel="icon" type="image/png" href="/static/favicon.png">'

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700;800'
    '&family=Playfair+Display:wght@700&display=swap" rel="stylesheet">'
)

THEME_BOOT = (
    "<script>(function(){try{var t=localStorage.getItem('ta-theme');"
    "if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t);}catch(e){}})();</script>"
)

THEME_TOGGLE = """<script>
(function(){
  var b=document.getElementById('theme-toggle');
  function cur(){var t=document.documentElement.getAttribute('data-theme');
    if(t)return t;return window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}
  function label(){if(b){b.setAttribute('aria-label',cur()==='dark'?'Chuyển sang giao diện sáng':'Chuyển sang giao diện tối');}}
  label();
  if(b)b.addEventListener('click',function(){var n=cur()==='dark'?'light':'dark';
    document.documentElement.setAttribute('data-theme',n);try{localStorage.setItem('ta-theme',n);}catch(e){}label();});
  // The "Tiện ích" menu closes on a click outside it and on Escape.
  document.addEventListener('click',function(ev){document.querySelectorAll('details.dd[open]').forEach(function(d){
    if(!d.contains(ev.target))d.removeAttribute('open');});});
  document.addEventListener('keydown',function(ev){if(ev.key!=='Escape')return;
    document.querySelectorAll('details.dd[open]').forEach(function(d){d.removeAttribute('open');var s=d.querySelector('summary');if(s)s.focus();});});
  // An anchor naming a closed section (#ghi, #nhatky...) opens it.
  function openHash(){var id=location.hash.slice(1);if(!id)return;var el=document.getElementById(id);
    if(el&&el.tagName==='DETAILS')el.setAttribute('open','');}
  openHash();window.addEventListener('hashchange',openHash);
  // Tabs: buttons with role=tab inside [data-tabs]; without JavaScript every panel shows.
  document.querySelectorAll('[data-tabs]').forEach(function(box){
    var tabs=box.querySelectorAll('[role=tab]'),panels=box.querySelectorAll('[role=tabpanel]');
    function show(i){tabs.forEach(function(t,j){t.setAttribute('aria-selected',j===i?'true':'false');t.tabIndex=j===i?0:-1;});
      panels.forEach(function(p,j){p.hidden=j!==i;});}
    tabs.forEach(function(t,i){t.addEventListener('click',function(){show(i);});
      t.addEventListener('keydown',function(ev){var k=ev.key==='ArrowRight'?1:ev.key==='ArrowLeft'?-1:0;
        if(k){ev.preventDefault();var n=(i+k+tabs.length)%tabs.length;show(n);tabs[n].focus();}});});
    show(0);});
})();
</script>"""

_LIGHT = """
  --bg:#E9EBFB; --bg-2:#E1E4F8; --glow-a:rgba(255,190,214,.38); --glow-b:rgba(164,190,255,.42);
  --card:rgba(255,255,255,.66); --card-line:rgba(255,255,255,.9); --solid:#F7F8FE;
  --tile:rgba(255,255,255,.86); --tile-line:#E2E5F4; --surface-2:#F1F2FB; --surface-3:#E4E7F6;
  --line:#DDE0F1; --line-strong:#C8CCE6;
  --ink:#0E1226; --ink-2:#353B59; --muted:#5B6184; --label:#465073;
  --brand:#101633; --brand-ink:#FFFFFF; --pill-on:#FFFFFF;
  --accent:#4F6FE0; --accent-2:#9BB5FA; --focus:rgba(79,111,224,.55);
  --up:#0B7A52; --up-soft:#DDF3EA; --up-line:#A6DCC6;
  --down:#BE1238; --down-soft:#FCE5EC; --down-line:#F2B4C5;
  --warn:#8A4A00; --warn-soft:#FCEBD4; --warn-line:#EBC489;
  --teal:#0C7666; --teal-soft:#DCF2EE; --teal-line:#A4DBD1;
  --info:#3150C4; --info-soft:#E3E8FD; --info-line:#BBC7F5;
  --c-port:#E0364F; --c-vni:#E8900F; --c-cap:#4A5174;
  --c-factor:#3A63D8; --c-engine:#B7790A; --c-mom:#13937A; --c-bench:#7C82A2;
  --shadow:0 14px 34px -18px rgba(48,56,140,.35); --shadow-sm:0 6px 16px -10px rgba(48,56,140,.38);
"""

_DARK = """
  --bg:#0B0E1C; --bg-2:#0E1226; --glow-a:rgba(170,80,140,.16); --glow-b:rgba(70,100,230,.2);
  --card:rgba(23,27,48,.78); --card-line:rgba(255,255,255,.06); --solid:#161A2F;
  --tile:rgba(33,38,66,.72); --tile-line:#2B3152; --surface-2:#1B2039; --surface-3:#272D4C;
  --line:#2A3050; --line-strong:#3A416A;
  --ink:#EEF0FB; --ink-2:#C4C8E2; --muted:#9AA0C2; --label:#AAB0D2;
  --brand:#E8EBFF; --brand-ink:#0E1226; --pill-on:#2C3358;
  --accent:#7E99F6; --accent-2:#3D58C2; --focus:rgba(126,153,246,.65);
  --up:#46CC97; --up-soft:#11291F; --up-line:#1F5A43;
  --down:#FF7090; --down-soft:#331521; --down-line:#6A2338;
  --warn:#F2B44E; --warn-soft:#30240F; --warn-line:#644A1C;
  --teal:#56D5C1; --teal-soft:#0F2B27; --teal-line:#1E5A51;
  --info:#93A9FF; --info-soft:#18224A; --info-line:#2D3C7C;
  --c-port:#FF6680; --c-vni:#F5A938; --c-cap:#A9AFD3;
  --c-factor:#6E8FF0; --c-engine:#D9A43A; --c-mom:#3CC3A2; --c-bench:#8A90B3;
  --shadow:0 18px 40px -20px rgba(0,0,0,.75); --shadow-sm:0 6px 18px -10px rgba(0,0,0,.7);
"""

STYLE = (
    ":root{" + _LIGHT + "color-scheme:light}"
    "@media (prefers-color-scheme:dark){:root:not([data-theme=light]){" + _DARK + "color-scheme:dark}}"
    ":root[data-theme=dark]{" + _DARK + "color-scheme:dark}"
    + """
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;color:var(--ink);background-color:var(--bg);
  background-image:radial-gradient(60vw 42vh at 88% -4%,var(--glow-a),transparent 70%),
    radial-gradient(56vw 46vh at -6% 18%,var(--glow-b),transparent 70%),linear-gradient(180deg,var(--bg),var(--bg-2));
  background-attachment:fixed;font:16px/1.55 "Be Vietnam Pro",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline;text-underline-offset:3px}
:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
.num,td.num,.kpi .v,.stat .v,.big{font-variant-numeric:tabular-nums}
::selection{background:var(--info-soft)}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.skip{position:absolute;left:16px;top:-60px;z-index:50;background:var(--brand);color:var(--brand-ink);padding:12px 16px;border-radius:12px}
.skip:focus{top:12px}
/* header */
.top{position:sticky;top:0;z-index:20;padding:12px 0 0}
.top-in{max-width:1120px;margin:0 auto;padding:0 16px}
.head{display:grid;grid-template-areas:"meta" "nav";gap:8px;padding:12px;border-radius:28px;background:var(--card);
  border:1px solid var(--card-line);box-shadow:var(--shadow);backdrop-filter:saturate(1.4) blur(16px);
  -webkit-backdrop-filter:saturate(1.4) blur(16px)}
.head-meta{grid-area:meta;display:flex;align-items:center;gap:8px;min-width:0}
.head-nav{grid-area:nav;display:flex;align-items:center;gap:8px;flex-wrap:wrap;min-width:0}
@media (min-width:900px){.head{grid-template-areas:"nav meta";grid-template-columns:minmax(0,1fr) auto;align-items:center;padding:10px 12px}}
.pill{display:inline-flex;align-items:center;gap:8px;min-height:44px;padding:0 16px;border-radius:999px;background:var(--tile);
  border:1px solid var(--tile-line);font:700 14px/1 "Be Vietnam Pro",sans-serif;color:var(--ink);white-space:nowrap}
.date-pill{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis}
.date-pill svg{width:18px;height:18px;flex:none;color:var(--ink-2)}
.date-pill .sep{color:var(--muted)}
.ver-pill{display:inline-flex;align-items:center;min-height:44px;padding:0 16px;border-radius:999px;background:var(--tile);
  border:1px solid var(--tile-line);font:800 14px/1 "Be Vietnam Pro",sans-serif;letter-spacing:.04em;color:var(--ink);white-space:nowrap}
.ver-pill:hover{text-decoration:none;border-color:var(--accent)}
.ver-pill.warn{color:var(--down);border-color:var(--down-line);background:var(--down-soft)}
.icon-btn{width:44px;height:44px;border-radius:999px;border:1px solid var(--tile-line);background:var(--tile);color:var(--ink);
  display:grid;place-items:center;cursor:pointer;flex:none}
.icon-btn:hover{border-color:var(--accent)}
.icon-btn svg{width:20px;height:20px}
.i-sun{display:none}:root[data-theme=dark] .i-sun{display:block}:root[data-theme=dark] .i-moon{display:none}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]) .i-sun{display:block}:root:not([data-theme=light]) .i-moon{display:none}}
.wordmark{font:700 26px/1 "Playfair Display",Georgia,serif;letter-spacing:.14em;color:var(--ink);padding:8px 8px 8px 4px}
.wordmark:hover{text-decoration:none}
.nav{display:flex;align-items:center;gap:4px;flex-wrap:wrap}
.nav>a,.dd>summary{display:inline-flex;align-items:center;gap:6px;min-height:44px;padding:0 14px;border-radius:999px;
  font:700 14px/1 "Be Vietnam Pro",sans-serif;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-2);cursor:pointer}
.nav>a:hover,.dd>summary:hover{text-decoration:none;color:var(--ink)}
.nav>a.on,.dd>summary.on{background:var(--pill-on);color:var(--ink);box-shadow:var(--shadow-sm)}
.dd{position:relative}
.dd>summary{list-style:none}.dd>summary::-webkit-details-marker{display:none}
.dd>summary svg{width:16px;height:16px;transition:transform .15s}.dd[open]>summary svg{transform:rotate(180deg)}
.dd-menu{position:absolute;right:0;top:calc(100% + 8px);z-index:30;min-width:232px;display:flex;flex-direction:column;gap:2px;
  padding:8px;border-radius:20px;background:var(--solid);border:1px solid var(--line);box-shadow:var(--shadow)}
.dd-menu a{display:flex;align-items:center;min-height:44px;padding:0 14px;border-radius:14px;color:var(--ink);font-weight:600}
.dd-menu a:hover{text-decoration:none;background:var(--surface-2)}
.dd-menu a.on{background:var(--surface-2);font-weight:800}
@media (max-width:440px){.head{padding:10px}.wordmark{font-size:20px;letter-spacing:.06em;padding:8px 2px}
  .head-nav{gap:4px}.nav{gap:2px}.nav>a,.dd>summary{padding:0 10px;font-size:13px;letter-spacing:.02em}
  .pill,.ver-pill{padding:0 12px;font-size:13px}.date-pill{gap:6px}}
/* layout */
main{max-width:1120px;margin:0 auto;padding:20px 16px 56px}
.grid{display:grid;gap:16px;align-items:start}
.grid>*{min-width:0}
.g2,.g3,.g4,.g-wide{grid-template-columns:1fr}
@media (min-width:768px){.g2,.g-wide{grid-template-columns:repeat(2,minmax(0,1fr))}.g3,.g4{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (min-width:1024px){.g3{grid-template-columns:repeat(3,minmax(0,1fr))}.g4{grid-template-columns:repeat(4,minmax(0,1fr))}
  .g-wide{grid-template-columns:minmax(0,1.35fr) minmax(0,1fr)}}
.stack>*+*{margin-top:16px}
.mt{margin-top:16px}
h1{font:800 30px/1.12 "Be Vietnam Pro",sans-serif;letter-spacing:-.02em;text-transform:uppercase;margin:0}
h1 .tick{letter-spacing:.02em}
h2{font:800 22px/1.15 "Be Vietnam Pro",sans-serif;letter-spacing:-.015em;text-transform:uppercase;margin:0}
h3{font:700 13px/1.2 "Be Vietnam Pro",sans-serif;letter-spacing:.08em;text-transform:uppercase;color:var(--label);margin:24px 0 12px}
.kicker{font:700 13px/1.2 "Be Vietnam Pro",sans-serif;letter-spacing:.08em;text-transform:uppercase;color:var(--label);margin-bottom:6px}
.lead{color:var(--ink-2);margin:10px 0 0;max-width:720px;font-size:16px}
.muted{color:var(--muted)}.small{font-size:14px}
.pos{color:var(--up)}.neg{color:var(--down)}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:1px}
/* cards */
.card{position:relative;background:var(--card);border:1px solid var(--card-line);border-radius:28px;box-shadow:var(--shadow)}
.card-h{display:flex;flex-wrap:wrap;align-items:flex-start;justify-content:space-between;gap:12px;padding:24px 24px 16px}
.card-h>div:first-child{flex:1 1 220px;min-width:0}
.card-h .sub{color:var(--muted);font-size:14px;margin-top:6px}
.card-b{padding:0 24px 24px}
.card-b.flush{padding:0 0 8px}
.card-b.flush .table-wrap{border-top:1px solid var(--line)}
@media (max-width:620px){.card-h{padding:20px 18px 12px}.card-b{padding:0 18px 20px}h1{font-size:26px}h2{font-size:20px}}
.accent-peach,.accent-sa,.accent-factor,.accent-gold{border-left:4px solid var(--accent)}
.accent-peach{border-left-color:var(--c-engine)}.accent-sa{border-left-color:var(--c-mom)}.accent-factor{border-left-color:var(--c-factor)}
.hero{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:16px;padding:24px;margin:0 0 16px}
.hero .eyebrow{font:700 13px/1.2 "Be Vietnam Pro",sans-serif;letter-spacing:.08em;text-transform:uppercase;color:var(--label);margin-bottom:8px}
@media (max-width:620px){.hero{padding:20px 18px}}
/* tiles */
.kpi,.stat,.tile{position:relative;background:var(--tile);border:1px solid var(--tile-line);border-radius:22px;padding:16px 18px;min-width:0}
.kpi .l,.stat .l,.tile .l{font:700 13px/1.2 "Be Vietnam Pro",sans-serif;letter-spacing:.06em;text-transform:uppercase;color:var(--label)}
.kpi .v,.stat .v,.tile .v{font:800 clamp(18px,4.8vw,26px)/1.15 "Be Vietnam Pro",sans-serif;letter-spacing:-.01em;margin-top:8px}
.kpi .s,.stat .s,.tile .s{font-size:14px;color:var(--muted);margin-top:4px}
.stat .s .badge,.tile .s .badge{white-space:normal}
.tile .ic{position:absolute;right:14px;top:14px;width:22px;height:22px;color:var(--ink-2)}
.tile.has-ic .l{padding-right:30px}
.strip{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:16px}
@media (min-width:1024px){.strip{grid-template-columns:repeat(4,minmax(0,1fr))}}
@media (max-width:380px){.strip{grid-template-columns:1fr}}
.tiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
@media (min-width:1024px){.tiles.t3{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:380px){.tiles{grid-template-columns:1fr}}
@media (max-width:620px){.kpi,.stat,.tile{padding:14px}}
.meter{height:8px;border-radius:999px;background:var(--surface-3);overflow:hidden;margin-top:10px}
.meter>span{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,var(--accent),var(--accent-2))}
.meter.hot>span{background:var(--down)}
/* forms */
label.f{display:block;font:700 13px/1.2 "Be Vietnam Pro",sans-serif;color:var(--label);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px}
.row{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end}
.row>.fld{flex:1 1 160px;min-width:0}
input,select,textarea{width:100%;font:inherit;min-height:48px;padding:12px 16px;border-radius:16px;border:1px solid var(--line-strong);
  background:var(--tile);color:var(--ink);outline:none;transition:border-color .15s,box-shadow .15s}
input:focus,select:focus,textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--focus)}
input.ticker{text-transform:uppercase;font-weight:800;letter-spacing:.06em}
input[type=file]{padding:10px 12px}
input[type=checkbox]{width:20px;height:20px;min-height:0;padding:0;accent-color:var(--brand)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:44px;padding:8px 20px;border-radius:999px;
  border:1px solid transparent;font:800 14px/1.25 "Be Vietnam Pro",sans-serif;letter-spacing:.05em;text-transform:uppercase;
  text-align:center;cursor:pointer;max-width:100%;transition:filter .15s,background .15s,border-color .15s,transform .05s}
.btn:active{transform:translateY(1px)}
.btn svg{width:18px;height:18px;flex:none}
.btn-primary,.btn-sa{background:var(--brand);color:var(--brand-ink)}
.btn-primary:hover,.btn-sa:hover{filter:brightness(1.15);text-decoration:none}
.btn-ghost{background:var(--tile);color:var(--ink);border-color:var(--tile-line)}
.btn-ghost:hover{border-color:var(--accent);text-decoration:none}
.btn-danger{background:var(--down-soft);color:var(--down);border-color:var(--down-line)}
.btn-danger:hover{filter:brightness(.98);text-decoration:none}
.btn-block{display:flex;width:100%}
.btn[disabled]{opacity:.55;cursor:not-allowed}
.checks{display:flex;flex-wrap:wrap;gap:8px 20px;margin:16px 0 8px}
label.check{display:inline-flex;align-items:center;gap:10px;font-size:15px;color:var(--ink-2);cursor:pointer;min-height:44px}
label.check.warn{display:flex;align-items:flex-start;color:var(--warn);font-size:14px;margin-top:8px;min-height:0}
label.check.warn input{margin-top:2px;flex:none}
/* on/off switch */
.switch{display:inline-flex;align-items:center;gap:10px;border:0;background:none;padding:4px 0;cursor:pointer;min-height:44px;
  font:700 13px/1 "Be Vietnam Pro",sans-serif;color:var(--muted)}
.switch .knob{position:relative;width:48px;height:28px;border-radius:999px;background:var(--surface-3);
  border:1px solid var(--line-strong);transition:background .2s,border-color .2s;flex:none}
.switch .knob::after{content:"";position:absolute;top:3px;left:3px;width:20px;height:20px;border-radius:50%;
  background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.3);transition:transform .2s}
.switch.on{color:var(--up)}
.switch.on .knob{background:var(--brand);border-color:transparent}
.switch.on .knob::after{transform:translateX(20px)}
.switch .st{min-width:24px;text-align:left}
/* badges, chips, segmented controls */
.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;border:1px solid transparent;
  font:800 12px/1.3 "Be Vietnam Pro",sans-serif;letter-spacing:.05em;text-transform:uppercase;white-space:nowrap}
.b-buy,.b-ok{background:var(--up-soft);color:var(--up);border-color:var(--up-line)}
.b-sell,.b-err{background:var(--down-soft);color:var(--down);border-color:var(--down-line)}
.b-hold,.b-info,.b-run{background:var(--info-soft);color:var(--info);border-color:var(--info-line)}
.b-watch{background:var(--warn-soft);color:var(--warn);border-color:var(--warn-line)}
.b-none{background:var(--surface-2);color:var(--muted);border-color:var(--line)}
.b-run::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor;animation:pulse 1.2s ease-in-out infinite}
@keyframes pulse{50%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.b-run::before{animation:none}*{transition:none!important}}
.chips{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.chip{display:inline-flex;align-items:center;gap:6px;min-height:36px;padding:0 14px;border-radius:999px;background:var(--tile);
  border:1px solid var(--tile-line);font:800 14px/1 "Be Vietnam Pro",sans-serif;letter-spacing:.02em;color:var(--ink);white-space:nowrap}
.chip svg{width:16px;height:16px}
.chip-pos{background:var(--teal-soft);border-color:var(--teal-line);color:var(--teal)}
.chip-neg{background:var(--down-soft);border-color:var(--down-line);color:var(--down)}
.chip-amber{background:var(--warn-soft);border-color:var(--warn-line);color:var(--warn)}
.chip-info{background:var(--info-soft);border-color:var(--info-line);color:var(--info)}
.seg{display:inline-flex;flex-wrap:wrap;gap:4px;padding:4px;border-radius:999px;background:var(--tile);border:1px solid var(--tile-line)}
.seg button,.seg a{min-height:40px;min-width:40px;padding:0 14px;border-radius:999px;border:0;background:none;color:var(--ink-2);
  font:800 14px/1 "Be Vietnam Pro",sans-serif;cursor:pointer;display:inline-flex;align-items:center;justify-content:center}
.seg button.on,.seg button[aria-selected=true],.seg a.on{background:var(--brand);color:var(--brand-ink)}
.seg a:hover{text-decoration:none}
.tgl{min-height:40px;padding:0 16px;border-radius:999px;border:1px solid var(--line);background:var(--tile);color:var(--muted);
  font:800 14px/1 "Be Vietnam Pro",sans-serif;cursor:pointer}
.tgl[aria-pressed=true]{background:var(--warn-soft);border-color:var(--warn-line);color:var(--warn)}
@media (max-width:440px){.seg button,.seg a{padding:0 10px;font-size:13px}}
/* alerts */
.alert{padding:14px 18px;border-radius:20px;border:1px solid transparent;margin-bottom:16px;font-size:15px;white-space:pre-line}
.alert-err{background:var(--down-soft);border-color:var(--down-line);color:var(--down)}
.alert-ok{background:var(--up-soft);border-color:var(--up-line);color:var(--up)}
.alert-info{background:var(--info-soft);border-color:var(--info-line);color:var(--info)}
/* tables */
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:15px}
th{font:700 12px/1.2 "Be Vietnam Pro",sans-serif;color:var(--label);text-transform:uppercase;letter-spacing:.06em;text-align:left;
  padding:12px 16px;white-space:nowrap;border-bottom:1px solid var(--line)}
td{padding:12px 16px;border-top:1px solid var(--line);vertical-align:middle}
tbody tr:first-child td{border-top:0}
tbody tr:hover td{background:color-mix(in srgb,var(--surface-2) 60%,transparent)}
td.num,th.num{text-align:right;white-space:nowrap}
code,td code{font:13px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--surface-2);padding:2px 7px;border-radius:8px;
  border:1px solid var(--line)}
td.empty{color:var(--muted);text-align:center;padding:28px}
.sym{font-weight:800;letter-spacing:.04em;color:var(--ink)}
a.sym:hover{color:var(--accent)}
tr.changed td{background:var(--info-soft)}
tr.void td{opacity:.5;text-decoration:line-through}
/* feeds, reports, steps */
.feed{list-style:none;margin:0;padding:0}
.feed li{display:flex;gap:12px;padding:12px 0;border-top:1px solid var(--line);font-size:15px}
.feed li:first-child{border-top:0;padding-top:4px}
.feed .t{flex:none;color:var(--muted);font-size:13px;width:52px;padding-top:2px;font-variant-numeric:tabular-nums}
.feed .empty{color:var(--muted);border:0}
.report{white-space:pre-wrap;font:15px/1.7 "Be Vietnam Pro",sans-serif;margin:0;color:var(--ink);overflow-wrap:anywhere}
.report.mono{font:13px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}
.steps{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 16px}
.step{display:flex;align-items:center;gap:8px;min-height:36px;padding:0 14px;border-radius:999px;background:var(--tile);
  border:1px solid var(--tile-line);color:var(--muted);font-size:14px;font-weight:700}
.step.done{background:var(--up-soft);border-color:var(--up-line);color:var(--up)}
.step.now{background:var(--info-soft);border-color:var(--info-line);color:var(--info)}
.step.fail{background:var(--down-soft);border-color:var(--down-line);color:var(--down)}
/* accordions */
.acc{background:var(--card);border:1px solid var(--card-line);border-radius:28px;box-shadow:var(--shadow)}
.acc+.acc,.acc+.card,.card+.acc{margin-top:16px}
.acc>summary{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:22px 24px;cursor:pointer;list-style:none;
  border-radius:28px}
.acc>summary::-webkit-details-marker{display:none}
.acc .ak{font:400 17px/1.2 "Be Vietnam Pro",sans-serif;color:var(--ink-2)}
.acc .at{font:800 22px/1.15 "Be Vietnam Pro",sans-serif;text-transform:uppercase;letter-spacing:-.02em;margin-top:6px;color:var(--ink)}
.chev{flex:none;width:48px;height:48px;border-radius:16px;background:var(--tile);border:1px solid var(--tile-line);display:grid;place-items:center;color:var(--ink)}
.chev svg{width:20px;height:20px;transition:transform .2s}
.acc[open] .chev svg{transform:rotate(180deg)}
.acc-b{padding:0 24px 24px}
@media (max-width:620px){.acc>summary{padding:20px 18px}.acc-b{padding:0 18px 20px}.acc .at{font-size:20px}}
/* bars */
.bars{display:flex;flex-direction:column;gap:16px}
.bars .h{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:8px}
.bars .h b{font-size:17px}
.bars .h .num{color:var(--ink-2);font-weight:600}
.track{position:relative;height:10px;border-radius:999px;background:var(--surface-3)}
.track>span{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,var(--accent),var(--accent-2))}
.track>i{position:absolute;top:-4px;width:3px;height:18px;border-radius:2px;background:var(--ink-2)}
.watch .track>span{background:var(--warn)}.danger .track>span{background:var(--down)}
.bars .note{font-size:14px;color:var(--muted);margin-top:6px}
/* charts */
.chart{position:relative}
.chart svg{display:block;width:100%;height:auto;touch-action:pan-y}
.chart .tip{position:absolute;z-index:2;pointer-events:none;min-width:240px;max-width:min(300px,90%);background:var(--solid);
  border:1px solid var(--line);border-radius:22px;box-shadow:var(--shadow);padding:14px 16px;font-size:14px;opacity:0;
  transition:opacity .1s;color:var(--ink)}
.chart .tip b{display:block;margin-bottom:6px;font-size:15px}
.tip .tr{display:flex;justify-content:space-between;gap:12px;padding:3px 0}
.tip .tr span:first-child{font:700 12px/1.6 "Be Vietnam Pro",sans-serif;letter-spacing:.05em;text-transform:uppercase;color:var(--label)}
.tip .tr span:last-child{font-weight:800;text-align:right}
.tip .ft{border-top:1px solid var(--line);margin-top:8px;padding-top:8px;color:var(--muted)}
.legend{display:flex;flex-wrap:wrap;justify-content:center;gap:8px 20px;font-size:15px;color:var(--ink-2);margin:8px 0}
.legend .ln{display:inline-block;width:22px;height:0;border-top:3px solid;vertical-align:5px;margin-right:8px;border-radius:2px}
.legend .ln.dash{border-top-style:dashed}
.chart-bar{display:flex;flex-wrap:wrap;gap:12px;justify-content:space-between;align-items:center;margin-bottom:12px}
footer{max-width:1120px;margin:0 auto;padding:0 16px 40px;color:var(--muted);font-size:13px;display:flex;flex-wrap:wrap;
  justify-content:space-between;gap:8px}
footer::before{content:"";flex-basis:100%;height:1px;background:var(--line);margin-bottom:14px}
.inline{display:inline;margin:0}
.linkbtn{border:0;background:none;padding:8px 4px;font:700 14px "Be Vietnam Pro",sans-serif;color:var(--down);cursor:pointer;min-height:44px}
.linkbtn:hover{text-decoration:underline}
"""
)

# ─── icons (stroke, currentColor) ─────────────────────────────────────────────

_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
ICONS = {
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M8 3v4M16 3v4M3 10h18"/>',
    "chev": '<path d="m6 9 6 6 6-6"/>',
    "down": '<path d="M12 4v14M6 12l6 6 6-6"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
    "warn": '<path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17.5v.5"/>',
    "up": '<path d="m3 17 6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
    "fall": '<path d="m3 7 6 6 4-4 8 8"/><path d="M15 17h6v-6"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "news": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h8M8 11h8M8 15h5"/>',
    "sliders": '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    "x": '<path d="M6 6l12 12M18 6 6 18"/>',
    "sunrise": '<path d="M12 3v5M5 10l1.5 1.5M19 10l-1.5 1.5M3 18h18M7 18a5 5 0 0 1 10 0M9 6l3-3 3 3"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    "bank": '<path d="M3 10h18L12 4 3 10zM5 10v8M9 10v8M15 10v8M19 10v8M3 20h18"/>',
    "wallet": '<path d="M4 7h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a1 1 0 0 1-1-1V7z"/><path d="M4 7l11-3v3M16 13h2"/>',
    "arrow-in": '<path d="M7 7l10 10M17 9v8H9"/>',
    "pie": '<path d="M12 3a9 9 0 1 0 9 9h-9V3z"/><path d="M15 3.5A9 9 0 0 1 20.5 9H15V3.5z"/>',
    "flame": '<path d="M12 3s5 4.5 5 10a5 5 0 0 1-10 0c0-2.5 1.5-4 1.5-4S9 12 11 12c0-4 1-9 1-9z"/>',
    "list": '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
}


def icon(name: str, cls: str = "") -> str:
    svg = _SVG.replace("<svg ", f'<svg class="{cls}" ', 1) if cls else _SVG
    return svg + ICONS[name] + "</svg>"


SUN = ('<svg class="i-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">'
       + ICONS["sun"] + "</svg>")
MOON = ('<svg class="i-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">'
        + ICONS["moon"] + "</svg>")

e = html.escape

# The main tabs, then the "Tiện ích" menu (key, path, label).
NAV = (("home", "/", "Home"), ("taisan", "/taisan", "Danh mục"))
TOOLS = (("ai", "/ai", "AI & cảnh báo"), ("bot", "/bot", "Bot giao dịch"), ("phienban", "/phienban", "Phiên bản"),
         ("zoom", "/zoom", "Cấu hình Zoom"))
_WEEKDAY = ("Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "CN")
_PHASE = {"closed": "Thị trường nghỉ", "pre_open": "Trước giờ mở cửa", "ato": "Phiên ATO", "continuous": "Đang khớp lệnh",
          "lunch": "Nghỉ trưa", "atc": "Phiên ATC", "put_through": "Thoả thuận", "after_close": "Đã đóng cửa"}


def date_pill() -> str:
    """"Thứ 7 24/10 · 15/09 ÂL · Thị trường nghỉ" (Vietnam time)."""
    from tradingagents.market import vn_rules
    from vnbot import amlich

    now = vn_rules.now_vn()
    try:
        lunar = amlich.short(now.date())
    except Exception:  # noqa: BLE001 - the pill shows the solar date anyway
        lunar = ""
    try:
        phase = _PHASE.get(vn_rules.session_phase(now), "")
    except Exception:  # noqa: BLE001
        phase = ""
    parts = [f"{_WEEKDAY[now.weekday()]} {now:%d/%m}"] + [p for p in (lunar, phase) if p]
    text = ' <span class="sep">·</span> '.join(e(p) for p in parts)
    return f'<span class="pill date-pill" title="{e(" · ".join(parts))}">{icon("calendar")}<span>{text}</span></span>'


def header(active: str, show_bot: bool, show_version: bool) -> str:
    from vnbot import version as ver

    pill = ""
    if show_version:
        ok = ver.is_release()
        pill = (f'<a class="ver-pill{"" if ok else " warn"}" href="/phienban" '
                f'title="{e(ver.label())}">{e(ver.VERSION)}{"" if ok else " ⚠"}</a>')
    links = "".join(f'<a href="{href}" class="{"on" if key == active else ""}"'
                    + (' aria-current="page"' if key == active else "") + f">{label}</a>" for key, href, label in NAV)
    tools = [(k, h, t) for k, h, t in TOOLS if show_bot or k != "bot"]
    tool_on = any(k == active for k, _, _ in tools)
    menu = "".join(f'<a href="{h}" class="{"on" if k == active else ""}"' + (' aria-current="page"' if k == active else "")
                   + f">{t}</a>" for k, h, t in tools)
    dropdown = (f'<details class="dd"><summary class="{"on" if tool_on else ""}">Tiện ích {icon("chev")}</summary>'
                f'<div class="dd-menu">{menu}</div></details>')
    return (
        '<a class="skip" href="#main">Bỏ qua tới nội dung</a>'
        '<header class="top"><div class="top-in"><div class="head">'
        f'<div class="head-meta">{date_pill()}'
        f'<button class="icon-btn" id="theme-toggle" type="button" aria-label="Đổi giao diện sáng/tối">{MOON}{SUN}</button>{pill}</div>'
        '<div class="head-nav"><a class="wordmark" href="/" aria-label="LCTA, về trang chủ">LCTA</a>'
        f'<nav class="nav" aria-label="Điều hướng chính">{links}{dropdown}</nav></div>'
        "</div></div></header>"
    )


def page(title: str, body: str, *, active: str = "", refresh: int | None = None, show_bot: bool = True,
         show_version: bool = True) -> str:
    """A private page. ``show_version``: the version in the header and the footer (off on
    the error pages, which a visitor without the password can see)."""
    from vnbot import version as ver

    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    foot = (f'<span>TradingAgents VN {e(ver.label())} · <a href="/phienban">lịch sử phiên bản</a></span>'
            if show_version else "")
    return (
        '<!doctype html><html lang="vi"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="theme-color" content="#E9EBFB" media="(prefers-color-scheme: light)">'
        '<meta name="theme-color" content="#0B0E1C" media="(prefers-color-scheme: dark)">'
        f"{meta}<title>{e(title)} · TradingAgents VN</title>{FAVICON}{THEME_BOOT}{FONTS}<style>{STYLE}</style></head><body>"
        f"{header(active, show_bot, show_version)}<main id=\"main\" tabindex=\"-1\">{body}</main>"
        "<footer><span>Thông tin chỉ mang tính tham khảo, không phải khuyến nghị đầu tư.</span>"
        f"<span>Bot không đặt lệnh tới công ty chứng khoán.</span>{foot}</footer>"
        f"{THEME_TOGGLE}</body></html>"
    )


def hero(title: str, lead: str = "", right: str = "", *, eyebrow: str = "") -> str:
    """The page's intro card: a small label, the page title (h1), a lead and actions."""
    top = f'<div class="eyebrow">{eyebrow}</div>' if eyebrow else ""
    return (f'<section class="hero card"><div>{top}<h1>{title}</h1>'
            + (f'<p class="lead">{lead}</p>' if lead else "") + f"</div>{right}</section>")


def strip(items: list[tuple[str, str, str]]) -> str:
    """The headline numbers of a page: (label, value html, sub html), as tiles."""
    return '<div class="strip">' + "".join(
        f'<div class="stat"><div class="l">{label}</div><div class="v">{value}</div>'
        + (f'<div class="s">{sub}</div>' if sub else "") + "</div>" for label, value, sub in items) + "</div>"


def meter(used: int, cap: int | None) -> str:
    """A bar of ``used`` out of ``cap`` (none without a cap); red from 85%."""
    if not cap:
        return ""
    share = max(0.0, min(1.0, used / cap))
    return (f'<div class="meter{" hot" if share >= 0.85 else ""}" role="img" aria-label="{used}/{cap}">'
            f'<span style="width:{share * 100:.1f}%"></span></div>')


def card(title: str, body: str, *, sub: str = "", action: str = "", flush: bool = False, cls: str = "",
         kicker: str = "", id: str = "") -> str:  # noqa: A002 - the HTML attribute
    """A section: ``kicker`` (small label) over ``title`` (h2), ``sub`` under it, ``action`` on the right."""
    head = ""
    if title or kicker:
        head = ('<div class="card-h"><div>' + (f'<div class="kicker">{kicker}</div>' if kicker else "")
                + (f"<h2>{title}</h2>" if title else "") + (f'<div class="sub">{sub}</div>' if sub else "")
                + f"</div>{action}</div>")
    ident = f' id="{e(id)}"' if id else ""
    return f'<section class="card {cls}"{ident}>{head}<div class="card-b{" flush" if flush else ""}">{body}</div></section>'


def acc(ident: str, kicker: str, title: str, body: str, *, opened: bool = False) -> str:
    """A section that opens and closes (native <details>: keyboard and screen readers for free)."""
    return (f'<details class="acc" id="{e(ident)}"{" open" if opened else ""}><summary>'
            f'<span><span class="ak">{e(kicker)}</span><span class="at" style="display:block">{e(title)}</span></span>'
            f'<span class="chev">{icon("chev")}</span></summary><div class="acc-b">{body}</div></details>')


def chip(text: str, kind: str = "") -> str:
    return f'<span class="chip{" chip-" + kind if kind else ""}">{text}</span>'


def tile(label: str, value: str, sub: str = "", *, cls: str = "", ic: str = "") -> str:
    return (f'<div class="tile{" has-ic" if ic else ""}">' + (icon(ic, "ic") if ic else "") + f'<div class="l">{label}</div>'
            f'<div class="v {cls}">{value}</div>' + (f'<div class="s">{sub}</div>' if sub else "") + "</div>")


def tabs(name: str, items: list[tuple[str, str]]) -> str:
    """Tabs (label, panel html) - every panel shows without JavaScript."""
    buttons = "".join(f'<button type="button" role="tab" id="{name}-t{i}" aria-controls="{name}-p{i}" '
                      f'aria-selected="{"true" if i == 0 else "false"}">{label}</button>' for i, (label, _) in enumerate(items))
    panels = "".join(f'<div role="tabpanel" id="{name}-p{i}" aria-labelledby="{name}-t{i}">{panel}</div>'
                     for i, (_, panel) in enumerate(items))
    return f'<div data-tabs><div class="seg" role="tablist" style="margin-bottom:16px">{buttons}</div>{panels}</div>'


def alert(text: str, kind: str = "info") -> str:
    role = "alert" if kind == "err" else "status"
    return f'<div class="alert alert-{kind}" role="{role}">{e(text)}</div>' if text else ""


_ACTION = {
    "BUY": ("b-buy", "MUA"), "SELL": ("b-sell", "BÁN"), "HOLD": ("b-hold", "GIỮ"),
    "WATCH": ("b-watch", "THEO DÕI"), "NO_TRADE": ("b-none", "ĐỨNG NGOÀI"),
}


def action_badge(action: str) -> str:
    if not action:
        return '<span class="muted">—</span>'
    cls, label = _ACTION.get(action, ("b-none", action))
    return f'<span class="badge {cls}" title="{e(action)}">{e(label)}</span>'


_STATUS = {
    "queued": ("b-none", "Đang chờ"), "quick": ("b-run", "Đang lập quyết định"),
    "running": ("b-run", "Agent đang phân tích"), "done": ("b-ok", "Hoàn tất"), "failed": ("b-err", "Lỗi"),
}


def status_badge(status: str) -> str:
    cls, label = _STATUS.get(status, ("b-none", status))
    return f'<span class="badge {cls}">{e(label)}</span>'


def stepper(status: str) -> str:
    order = ["queued", "quick", "running", "done"]
    labels = {"queued": "Xếp hàng", "quick": "Quyết định định lượng", "running": "Agent AI phân tích", "done": "Hoàn tất"}
    if status == "failed":
        return '<div class="steps"><span class="step fail">Không thành công · xem chi tiết bên dưới</span></div>'
    at = order.index(status) if status in order else 0
    items = []
    for i, key in enumerate(order):
        cls = "done" if i < at or status == "done" else ("now" if i == at else "")
        current = ' aria-current="step"' if cls == "now" else ""
        items.append(f'<span class="step {cls}"{current}>{labels[key]}</span>')
    return f'<div class="steps">{"".join(items)}</div>'


def money(value) -> str:
    return "—" if value is None else f"{value:,.0f}".replace(",", ".")


def money_short(value) -> str:
    """1.012.300.000 -> "1,012 tỷ"; 532.600.000 -> "532,6 tr" (KPI tiles)."""
    if value is None:
        return "—"
    if abs(value) >= 1e9:
        return f"{value / 1e9:,.3f} tỷ".replace(".", ",")
    if abs(value) >= 1e6:
        return f"{value / 1e6:,.1f} tr".replace(",", "X").replace(".", ",").replace("X", ".")
    return money(value)


def pct(value, signed: bool = True) -> str:
    if value is None:
        return "—"
    text = f"{value:+.2f}%" if signed else f"{value:.2f}%"
    return text.replace(".", ",")


def tone(value) -> str:
    if value is None or value == 0:
        return ""
    return "pos" if value > 0 else "neg"


def kpi(label: str, value: str, sub: str = "", cls: str = "") -> str:
    return (f'<div class="kpi"><div class="l">{label}</div><div class="v {cls}">{value}</div>'
            + (f'<div class="s">{sub}</div>' if sub else "") + "</div>")


def table(headers: list[tuple[str, str]], rows: list[str], empty: str) -> str:
    """``headers`` as (label, css class); ``rows`` already rendered <tr>...</tr>."""
    head = "".join(f'<th scope="col" class="{c}">{h}</th>' for h, c in headers)
    body = "".join(rows) or f'<tr><td class="empty" colspan="{len(headers)}">{empty}</td></tr>'
    # Focusable: a table wider than a phone scrolls sideways, with the keyboard too.
    return (f'<div class="table-wrap" tabindex="0" role="region" aria-label="Bảng, cuộn ngang được">'
            f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')


def sparkline(values: list[float], *, label: str = "") -> str:
    """A small line of ``values`` (oldest first), red when it ends lower, green when higher."""
    values = [float(v) for v in values if v]
    if len(values) < 2:
        return '<span class="spark muted small">chưa có dữ liệu</span>'
    w, h = 120, 36
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pts = " ".join(f"{i / (len(values) - 1) * w:.1f},{h - 3 - (v - lo) / span * (h - 6):.1f}" for i, v in enumerate(values))
    color = "var(--up)" if values[-1] >= values[0] else "var(--down)"
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{e(label)}">'
            f'<polyline points="{pts}" fill="none" style="stroke:{color};stroke-width:2;stroke-linejoin:round;stroke-linecap:round"/></svg>')


# ─── NAV chart (the bot's paper books) ────────────────────────────────────────

_CHART_JS = """<script>
(function(){
  document.querySelectorAll('.chart[data-series]').forEach(function(box){
    var d=JSON.parse(box.getAttribute('data-series')),svg=box.querySelector('svg'),tip=box.querySelector('.tip'),
        hair=svg.querySelector('.hair'),hit=svg.querySelector('.hit');
    function fmt(v){return v==null?'—':(v-100>=0?'+':'')+(v-100).toFixed(2).replace('.',',')+'%';}
    function at(i){
      var r=svg.getBoundingClientRect(),px=d.l+(d.dates.length>1?i/(d.dates.length-1):0)*(d.w-d.l-d.r);
      hair.setAttribute('x1',px);hair.setAttribute('x2',px);hair.style.opacity=1;
      var html='<b>'+d.dates[i]+'</b>';
      d.series.forEach(function(s){html+='<div class="tr"><span><span class="dot" style="background:'+s.color+'"></span>'+s.name+'</span><span>'+fmt(s.values[i])+'</span></div>';});
      tip.innerHTML=html;tip.style.opacity=1;
      var left=px/d.w*r.width+12;if(left+250>r.width)left=Math.max(0,px/d.w*r.width-260);tip.style.left=left+'px';tip.style.top='8px';
    }
    var cur=d.dates.length-1;
    function move(ev){var r=svg.getBoundingClientRect(),x=(ev.clientX-r.left)/r.width*d.w;
      cur=Math.round((x-d.l)/(d.w-d.l-d.r)*(d.dates.length-1));cur=Math.max(0,Math.min(d.dates.length-1,cur));at(cur);}
    function out(){tip.style.opacity=0;hair.style.opacity=0;}
    hit.addEventListener('pointermove',move);hit.addEventListener('pointerdown',move);hit.addEventListener('pointerleave',out);
    svg.addEventListener('keydown',function(ev){var k=ev.key==='ArrowRight'?1:ev.key==='ArrowLeft'?-1:0;if(!k)return;
      ev.preventDefault();cur=Math.max(0,Math.min(d.dates.length-1,cur+k));at(cur);});
    svg.addEventListener('blur',out);
  });
})();
</script>"""


def nav_chart(dates: list[str], series: list[dict], *, run_time: str | None = None) -> str:
    """Indexed (start = 100) lines on one axis. ``series``: {name, color (css), values, dashed};
    ``run_time``: when the bot runs each day (HH:MM), for the empty-chart hint."""
    if len(dates) < 2:
        when = f"tự động lúc {html.escape(run_time)} mỗi ngày" if run_time else "sau mỗi phiên"
        return ('<p class="muted small" style="margin:0">Biểu đồ NAV sẽ hiển thị sau hai phiên giao dịch của bot '
                f'({when}, hoặc chọn “Chạy phiên ngay”).</p>')
    w, h, left, right, top, bottom = 680, 270, 52, 172, 16, 32
    values = [v for s in series for v in s["values"] if v is not None]
    lo, hi = min(values + [100.0]), max(values + [100.0])
    pad = max((hi - lo) * 0.12, 0.5)
    lo, hi = lo - pad, hi + pad

    def x(i):
        return left + i / (len(dates) - 1) * (w - left - right)

    def y(v):
        return top + (hi - v) / (hi - lo) * (h - top - bottom)

    grid = []
    for k in range(4):
        v = lo + (hi - lo) * k / 3
        grid.append(f'<line x1="{left}" x2="{w - right}" y1="{y(v):.1f}" y2="{y(v):.1f}" '
                    f'style="stroke:var(--line-strong);stroke-width:1;stroke-dasharray:4 5"/>'
                    f'<text x="{left - 8}" y="{y(v) + 4:.1f}" text-anchor="end" '
                    f'style="fill:var(--label);font-size:13px;font-weight:700">{v - 100:+.0f}%</text>')
    base = (f'<line x1="{left}" x2="{w - right}" y1="{y(100):.1f}" y2="{y(100):.1f}" '
            'style="stroke:var(--muted);stroke-width:1"/>')
    lines, labels = [], []
    used_y: list[float] = []
    for s in series:
        pts = [(x(i), y(v)) for i, v in enumerate(s["values"]) if v is not None]
        if len(pts) < 2:
            continue
        dash = "stroke-dasharray:5 4;" if s.get("dashed") else ""
        path = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        lines.append(f'<polyline points="{path}" fill="none" style="stroke:{s["color"]};stroke-width:2.5;'
                     f'{dash}stroke-linejoin:round;stroke-linecap:round"/>')
        last = next(v for v in reversed(s["values"]) if v is not None)
        ly = pts[-1][1]
        while any(abs(ly - u) < 18 for u in used_y):
            ly += 18
        used_y.append(ly)
        lines.append(f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="4" '
                     f'style="fill:{s["color"]};stroke:var(--solid);stroke-width:2"/>')
        change = f"{last - 100:+.1f}%".replace(".", ",")
        labels.append(f'<text x="{w - right + 10}" y="{ly + 5:.1f}" style="fill:var(--ink-2);font-size:14px;font-weight:700">'
                      f'{e(s["name"])} {change}</text>')
    axis = (f'<text x="{left}" y="{h - 8}" style="fill:var(--label);font-size:13px;font-weight:700">{e(dates[0])}</text>'
            f'<text x="{w - right}" y="{h - 8}" text-anchor="end" style="fill:var(--label);font-size:13px;font-weight:700">{e(dates[-1])}</text>')
    hair = (f'<line class="hair" x1="0" x2="0" y1="{top}" y2="{h - bottom}" '
            'style="stroke:var(--muted);stroke-width:1;stroke-dasharray:3 3;opacity:0"/>')
    hit = f'<rect class="hit" x="{left}" y="0" width="{w - left - right}" height="{h}" fill="transparent"/>'
    data = {"w": w, "l": left, "r": right, "dates": dates,
            "series": [{"name": s["name"], "color": s["color"], "values": s["values"]} for s in series]}
    legend = "".join(
        f'<span><span class="ln{" dash" if s.get("dashed") else ""}" style="border-color:{s["color"]}"></span>{e(s["name"])}</span>'
        for s in series)
    return (f'<div class="legend">{legend}</div>'
            f'<div class="chart" data-series="{e(json.dumps(data))}">'
            f'<svg viewBox="0 0 {w} {h}" role="img" tabindex="0" '
            f'aria-label="NAV các tài khoản so với VNINDEX, chỉ số hoá 100; phím mũi tên để xem từng ngày">'
            f'{"".join(grid)}{base}{"".join(lines)}{"".join(labels)}{axis}{hair}{hit}</svg>'
            f'<div class="tip" role="status" aria-live="polite"></div></div>{_CHART_JS}')


# ─── asset chart (Tài sản) ────────────────────────────────────────────────────

_ASSET_JS = """<script>
(function(){
  document.querySelectorAll('.achart[data-points]').forEach(function(box){
    var P=JSON.parse(box.getAttribute('data-points')),svg=box.querySelector('svg'),tip=box.querySelector('.tip'),
        leg=box.querySelector('.legend'),dm=box.querySelector('[data-x=dm]'),vn=box.querySelector('[data-x=vni]'),
        cmp=box.querySelector('[data-cmp]'),W=680,H=300,L=58,R=14,T=16,B=34,
        st={range:box.getAttribute('data-range')||'0',cmp:true},cur=null;
    function size(){W=Math.max(260,Math.round(svg.parentNode.clientWidth||680));H=W<560?Math.round(Math.max(220,W*.72)):300;
      L=W<560?46:58;svg.setAttribute('viewBox','0 0 '+W+' '+H);}
    function money(v){if(v==null)return'—';var a=Math.abs(v),s=v<0?'-':'';
      if(a>=1e9)return s+(a/1e9).toFixed(2).replace('.',',')+' tỷ';
      if(a>=1e6)return s+(a/1e6).toFixed(1).replace('.',',')+' tr';return s+Math.round(a).toLocaleString('vi-VN');}
    function full(v){return v==null?'—':Math.round(v).toLocaleString('vi-VN')+'đ';}
    function pc(v){return v==null?'—':(v>=0?'+':'')+v.toFixed(2).replace('.',',')+'%';}
    function dm2(d){return d.slice(8,10)+'/'+d.slice(5,7);}
    function dmy(d){return dm2(d)+'/'+d.slice(0,4);}
    function span(){var n=P.d.length,days=+st.range;if(!days)return[0,n];
      var t=new Date(P.d[n-1]+'T00:00:00Z');t.setUTCDate(t.getUTCDate()-days);var from=t.toISOString().slice(0,10),i=0;
      for(var j=0;j<n;j++){if(P.d[j]<=from)i=j;}return[i,n];}
    function rebase(arr,a,b){var base=null;for(var j=a;j<b;j++){if(arr[j]!=null){base=arr[j];break;}}
      var out=[];for(var k=a;k<b;k++)out.push(base&&arr[k]!=null?(arr[k]/base-1)*100:null);return out;}
    function nice(x){var p=Math.pow(10,Math.floor(Math.log10(x))),f=x/p;return (f<=1?1:f<=2?2:f<=2.5?2.5:f<=5?5:10)*p;}
    function el(tag,attrs){var e=document.createElementNS('http://www.w3.org/2000/svg',tag);for(var k in attrs)e.setAttribute(k,attrs[k]);return e;}
    function txt(x,y,s,anchor){var t=el('text',{x:x,y:y,'text-anchor':anchor||'start',style:'fill:var(--label);font-size:13px;font-weight:700'});t.textContent=s;return t;}
    function chip(node,label,v){if(!node)return;node.textContent=label+' '+pc(v);
      node.className='chip '+(node.getAttribute('data-x')==='vni'?'chip-amber':(v==null||v>=0?'chip-pos':'chip-neg'));}
    function draw(){
      size();var r=span(),a=r[0],b=r[1],m=b-a;svg.innerHTML='';tip.style.opacity=0;
      var nav=rebase(P.n,a,b),vni=rebase(P.v,a,b);
      chip(dm,'DM',nav[m-1]);chip(vn,'VNI',vni[m-1]);
      if(m<2){leg.innerHTML='<span class="muted">Chưa đủ dữ liệu cho khoảng này.</span>';cur=null;return;}
      var S=st.cmp?[{n:'Danh mục',c:'var(--c-port)',v:nav,area:1},{n:'VN-Index',c:'var(--c-vni)',v:vni}]
                  :[{n:'Tổng tài sản',c:'var(--c-port)',v:P.t.slice(a,b),area:1},{n:'Vốn gốc',c:'var(--c-cap)',v:P.c.slice(a,b),dash:1}];
      var vals=[];S.forEach(function(s){s.v.forEach(function(x){if(x!=null)vals.push(x);});});
      if(st.cmp)vals.push(0);
      var lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals);if(hi===lo){hi+=1;lo-=1;}
      var step=nice((hi-lo)/4);lo=Math.floor(lo/step)*step;hi=Math.ceil(hi/step)*step;
      function x(i){return L+i/(m-1)*(W-L-R);}function y(v){return T+(hi-v)/(hi-lo)*(H-T-B);}
      for(var v=lo;v<=hi+step/2;v+=step){svg.appendChild(el('line',{x1:L,x2:W-R,y1:y(v),y2:y(v),style:'stroke:var(--line-strong);stroke-width:1;stroke-dasharray:4 5'}));
        svg.appendChild(txt(L-8,y(v)+4,st.cmp?(Math.round(v*10)/10+'%').replace('.',','):money(v),'end'));}
      if(st.cmp)svg.appendChild(el('line',{x1:L,x2:W-R,y1:y(0),y2:y(0),style:'stroke:var(--muted);stroke-width:1.5;stroke-dasharray:6 5'}));
      var ticks=Math.min(W<560?4:6,m);for(var q=0;q<ticks;q++){var i=Math.round(q*(m-1)/(ticks-1||1));
        svg.appendChild(txt(x(i),H-10,dm2(P.d[a+i]),q===0?'start':q===ticks-1?'end':'middle'));}
      S.forEach(function(s){var pts=[];s.v.forEach(function(v,i){if(v!=null)pts.push([x(i),y(v)]);});if(pts.length<2)return;
        var d=pts.map(function(p){return p[0].toFixed(1)+','+p[1].toFixed(1);}).join(' ');
        if(s.area){var base=st.cmp?y(0):H-B;svg.appendChild(el('polygon',{points:pts[0][0].toFixed(1)+','+base+' '+d+' '+pts[pts.length-1][0].toFixed(1)+','+base,
          style:'fill:'+s.c+';opacity:.1'}));}
        svg.appendChild(el('polyline',{points:d,fill:'none',style:'stroke:'+s.c+';stroke-width:2.5;stroke-linejoin:round;stroke-linecap:round;'+(s.dash?'stroke-dasharray:6 5':'')}));});
      var hair=el('line',{x1:0,x2:0,y1:T,y2:H-B,style:'stroke:var(--muted);stroke-width:1;stroke-dasharray:3 3;opacity:0'});svg.appendChild(hair);
      var hit=el('rect',{x:L,y:0,width:W-L-R,height:H,fill:'transparent'});svg.appendChild(hit);
      leg.innerHTML=S.map(function(s){return '<span><span class="ln'+(s.dash?' dash':'')+'" style="border-color:'+s.c+'"></span>'+s.n+'</span>';}).join('');
      cur={a:a,m:m,x:x,hair:hair,nav:nav,vni:vni,i:m-1};
      function show(i){var j=a+i,hx=x(i),rc=svg.getBoundingClientRect();cur.i=i;
        hair.setAttribute('x1',hx);hair.setAttribute('x2',hx);hair.style.opacity=1;
        var pnl=P.t[j]-P.c[j],pp=P.c[j]?pnl/P.c[j]*100:null,rows=[['Tổng tài sản',full(P.t[j]),''],['Tiền mặt',full(P.k[j]),'pos'],
          ['Lãi / lỗ',full(pnl)+' ('+pc(pp)+')',pnl>=0?'pos':'neg'],['Vốn gốc',full(P.c[j]),'']];
        if(vni[i]!=null)rows.push(['VN-Index',(100+vni[i]).toFixed(1).replace('.',',')+' đ.cơ sở ('+pc(vni[i])+')','']);
        if(nav[i]!=null)rows.push(['Danh mục',(100+nav[i]).toFixed(1).replace('.',',')+' đ.cơ sở ('+pc(nav[i])+')','']);
        tip.innerHTML='<b>'+dmy(P.d[j])+'</b>'+rows.map(function(r){return '<div class="tr"><span>'+r[0]+'</span><span class="'+r[2]+'">'+r[1]+'</span></div>';}).join('')
          +(P.h?'<div class="ft">'+P.h[j]+' mã đang nắm giữ</div>':'');
        tip.style.opacity=1;var w=tip.offsetWidth||260,left=hx/W*rc.width+14;if(left+w>rc.width)left=Math.max(0,hx/W*rc.width-w-14);
        tip.style.left=left+'px';tip.style.top='4px';}
      function move(ev){var rc=svg.getBoundingClientRect(),px=(ev.clientX-rc.left)/rc.width*W,
          i=Math.round((px-L)/(W-L-R)*(m-1));show(Math.max(0,Math.min(m-1,i)));}
      hit.addEventListener('pointermove',move);hit.addEventListener('pointerdown',move);
      hit.addEventListener('pointerleave',function(){tip.style.opacity=0;hair.style.opacity=0;});
      cur.show=show;
    }
    svg.addEventListener('keydown',function(ev){if(!cur||!cur.show)return;var k=ev.key==='ArrowRight'?1:ev.key==='ArrowLeft'?-1:0;if(!k)return;
      ev.preventDefault();cur.show(Math.max(0,Math.min(cur.m-1,cur.i+k)));});
    svg.addEventListener('blur',function(){tip.style.opacity=0;});
    box.querySelectorAll('[data-r]').forEach(function(btn){btn.addEventListener('click',function(){st.range=btn.getAttribute('data-r');
      box.querySelectorAll('[data-r]').forEach(function(o){o.classList.toggle('on',o===btn);o.setAttribute('aria-pressed',o===btn?'true':'false');});draw();});});
    if(cmp)cmp.addEventListener('click',function(){st.cmp=!st.cmp;cmp.setAttribute('aria-pressed',st.cmp?'true':'false');draw();});
    var tm=null,lastW=0;window.addEventListener('resize',function(){clearTimeout(tm);tm=setTimeout(function(){
      var w=Math.round(svg.parentNode.clientWidth);if(w!==lastW){lastW=w;draw();}},150);});
    draw();
  });
})();
</script>"""

ASSET_RANGES = (("7", "7N"), ("30", "1T"), ("90", "3T"), ("180", "6T"), ("365", "1N"), ("0", "Tất cả"))


def asset_chart(points: list[dict], *, default_range: str = "0", total: float | None = None,
                capital: float | None = None) -> str:
    """The Tài sản history: the portfolio (as fund units) against VNINDEX in %, or total
    assets against capital in đồng (the "So sánh VN-Index" switch), over 7 days .. all;
    drawn in the browser from ``points`` (``TaiSan.history``)."""
    if len(points) < 2:
        return ('<p class="muted" style="margin:0">Biểu đồ hiện sau hai phiên có giá đóng cửa: bot ghi giá mỗi '
                "ngày lúc 15:30 (sau khi nhập Excel, bot tự dựng lại lịch sử từ ngày đầu tiên trong sổ).</p>")
    data = {"d": [p["day"] for p in points], "t": [p["total"] for p in points], "c": [p["capital"] for p in points],
            "k": [p["cash"] for p in points], "n": [p["nav"] for p in points],
            "v": [p.get("vni_index") for p in points], "h": [p.get("held", 0) for p in points]}
    first, last = points[0], points[-1]
    total = last["total"] if total is None else total
    capital = last["capital"] if capital is None else capital
    dm = (last["nav"] / first["nav"] - 1) * 100 if first.get("nav") else None
    vni = (last["vni_index"] / first["vni_index"] - 1) * 100 if first.get("vni_index") and last.get("vni_index") else None
    ranges = "".join(f'<button type="button" data-r="{k}" class="{"on" if k == default_range else ""}" '
                     f'aria-pressed="{"true" if k == default_range else "false"}">{label}</button>' for k, label in ASSET_RANGES)
    return (f'<div class="achart" data-range="{e(default_range)}" data-points="{e(json.dumps(data))}">'
            f'<div class="big" style="font:800 clamp(28px,7vw,34px)/1.1 \'Be Vietnam Pro\',sans-serif;letter-spacing:-.02em">{money(total)}đ</div>'
            f'<div class="chips" style="margin:14px 0 16px">'
            f'<span class="chip {"chip-pos" if (dm or 0) >= 0 else "chip-neg"}" data-x="dm">DM {pct(dm)}</span>'
            f'<span class="chip chip-amber" data-x="vni">VNI {pct(vni)}</span><span class="muted">trong kỳ</span></div>'
            '<div class="chart-bar"><div class="seg" role="group" aria-label="Khoảng thời gian">' + ranges + "</div>"
            '<button type="button" class="tgl" data-cmp aria-pressed="true">So sánh VN-Index</button></div>'
            '<div class="chart"><svg viewBox="0 0 680 300" role="img" tabindex="0" '
            'aria-label="Hiệu suất danh mục so với VN-Index; phím mũi tên để xem từng ngày"></svg>'
            '<div class="tip" role="status" aria-live="polite"></div></div>'
            '<div class="legend"></div>'
            '<div class="tiles" style="margin-top:12px">'
            + tile("Tổng tài sản", f"{money(total)}đ") + tile("Vốn gốc", f"{money(capital)}đ") + "</div>"
            f'<p class="muted small" style="margin:12px 0 0">{len(points)} điểm dữ liệu · cập nhật 15:30 các ngày giao dịch</p>'
            '<noscript><p class="muted small">Bật JavaScript để xem biểu đồ.</p></noscript></div>'
            f"{_ASSET_JS}")
