"""The look of the web app: one stylesheet, a page shell and small components.

Server-rendered, no build step. An achromatic ledger: the chrome holds no colour, money
and counts are set in ink on white panels over a blue-grey ground, the one chart hue
(#1d5fbf) marks the screen's main data element (the portfolio line) and the only other
hue is a small signal where a figure changed (a gain, a loss, a warning). One typeface,
Geist (with its Vietnamese subset), tabular figures everywhere a number can change.

Shell: an open plane. The labelled sidebar sits on the page ground to the left of the
canvas with no top bar; on a phone it folds into a 48 px bar with the screen's name and
a menu that opens a full-screen sheet (``:target``, so it works without JavaScript).

Design contract: mobile first (one column to 860 px, then the sidebar and two columns
where it helps); one gap value (12 px) and one card padding (20 px); flat surfaces (a
1 px border, no shadow, no gradient, no motion that is not feedback); every control a
real <button>, <a> or form field with a visible label and a focus ring; gains and losses
carry a sign and a word, never colour alone; a zero is a zero and a missing figure a dash.
Light and dark follow the device and can be switched in the sidebar (kept in the browser).
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

STATIC_DIR = Path(__file__).with_name("static")
STATIC_FILES = ("logo.png", "favicon.png")
FAVICON = '<link rel="icon" type="image/png" href="/static/favicon.png">'

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700'
    '&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">'
)

THEME_BOOT = (
    "<script>(function(){try{var t=localStorage.getItem('ta-theme');"
    "if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t);}catch(e){}})();</script>"
)

THEME_TOGGLE = """<script>
(function(){
  var b=document.getElementById('theme-toggle');
  if(b){b.addEventListener('click',function(){
    var cur=document.documentElement.getAttribute('data-theme')||
      (window.matchMedia&&window.matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');
    var next=cur==='dark'?'light':'dark';
    document.documentElement.setAttribute('data-theme',next);
    try{localStorage.setItem('ta-theme',next);}catch(e){}
  });}
  // A link to a closed section (#nhatky) opens it.
  function openHash(){var id=location.hash.slice(1);if(!id)return;var el=document.getElementById(id);
    if(el&&el.tagName==='DETAILS')el.open=true;}
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

# The dealt style (achromatic ledger) with the ground drawn from the product's world: a
# blue-grey one level off white, as a ledger's paper. Cards stay white.
_LIGHT = """
  --ground:#f2f4f7; --surface:#ffffff; --raised:#eeeeee; --active:#e9e9e9; --border:#e6e6e6; --rule:#f0f0f0;
  --control:#d4d4d4; --text:#0a0a0a; --muted:#6b6b6b; --accent:#141414; --primary:#141414; --primary-ink:#ffffff;
  --positive:#2e7d32; --negative:#c4242a; --warning:#92400e; --info:#1d5fbf; --focus:#1d5fbf;
  --chart-1:#141414; --chart-2:#8a8a8a; --chart-3:#1d5fbf; --chart-muted:#c9c9c9; --grid:#ececec;
  --pill-positive-bg:#dcfae5; --pill-positive-ink:#04250e;
  --pill-negative-bg:#ffedea; --pill-negative-ink:#831c1e;
  --pill-warning-bg:#fffbeb; --pill-warning-ink:#92400e;
  --pill-info-bg:#e8f0fd; --pill-info-ink:#123f82;
  --pill-neutral-bg:#eeeeee; --pill-neutral-ink:#3d3d3d;
"""

_DARK = """
  --ground:#000000; --surface:#111111; --raised:#222222; --active:#262626; --border:#262626; --rule:#1c1c1c;
  --control:#444444; --text:#ffffff; --muted:#b4b4b4; --accent:#ffffff; --primary:#ffffff; --primary-ink:#000000;
  --positive:#3fc07a; --negative:#e5566f; --warning:#e5a93b; --info:#7aa7f0; --focus:#7aa7f0;
  --chart-1:#ffffff; --chart-2:#8a8a8a; --chart-3:#7aa7f0; --chart-muted:#4a4a4a; --grid:#2b2b2b;
  --pill-positive-bg:#172a1d; --pill-positive-ink:#3fc07a;
  --pill-negative-bg:#2c1a20; --pill-negative-ink:#e5566f;
  --pill-warning-bg:#2b2414; --pill-warning-ink:#e5a93b;
  --pill-info-bg:#152238; --pill-info-ink:#9dbdf4;
  --pill-neutral-bg:#222222; --pill-neutral-ink:#ffffff;
"""

# Older names the page modules still use, mapped onto the ledger tokens.
_ALIAS = """
  --ink:var(--text); --ink-2:var(--text); --label:var(--muted); --line:var(--border); --line-strong:var(--control);
  --surface-2:var(--raised); --surface-3:var(--raised); --tile:var(--surface); --tile-line:var(--border); --solid:var(--surface);
  --up:var(--positive); --down:var(--negative); --warn:var(--warning);
  --up-soft:var(--pill-positive-bg); --up-line:var(--pill-positive-bg); --down-soft:var(--pill-negative-bg); --down-line:var(--pill-negative-bg);
  --warn-soft:var(--pill-warning-bg); --warn-line:var(--pill-warning-bg); --info-soft:var(--pill-info-bg); --info-line:var(--pill-info-bg);
  --c-port:var(--chart-3); --c-vni:var(--chart-2); --c-cap:var(--chart-1);
  --c-factor:var(--chart-3); --c-engine:var(--chart-1); --c-mom:var(--chart-2); --c-bench:var(--chart-muted);
"""

STYLE = (
    ":root{" + _LIGHT + _ALIAS + "color-scheme:light}"
    "@media (prefers-color-scheme:dark){:root:not([data-theme=light]){" + _DARK + "color-scheme:dark}}"
    ":root[data-theme=dark]{" + _DARK + "color-scheme:dark}"
    + """
:root{--font:"Geist",Inter,system-ui,-apple-system,"Segoe UI",sans-serif;--mono:"Geist Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --fs-title:24px;--fs-section:16px;--fs-kpi:28px;--fs-body:14px;--fs-small:13px;--fs-tiny:12px;
  --sidebar:240px;--gap:12px;--pad:20px;--radius:8px;--radius-control:6px;--control-h:36px;--row-h:44px}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{margin:0;background:var(--ground);color:var(--text);font:var(--fs-body)/20px var(--font);-webkit-font-smoothing:antialiased}
h1,h2,h3,h4,p,figure,ul,ol,dl,dd{margin:0}
img,svg,video,canvas{display:block;max-width:100%}
button,input,select,textarea{font:inherit;color:inherit}
a{color:inherit;text-decoration:none}
a:hover{text-decoration:underline;text-underline-offset:3px}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px;border-radius:4px}
.num,td.num,th.num,table,.kpi .v,.stat .v,.tile .v,.big,.kpi-value{font-variant-numeric:tabular-nums}
::selection{background:var(--pill-info-bg)}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.skip{position:absolute;left:16px;top:-60px;z-index:50;background:var(--primary);color:var(--primary-ink);padding:10px 14px;border-radius:var(--radius-control)}
.skip:focus{top:12px}
/* shell: open plane */
.app{display:grid;grid-template-columns:var(--sidebar) minmax(0,1fr);min-height:100svh}
.sidebar{position:sticky;top:0;height:100svh;display:flex;flex-direction:column;gap:2px;padding:20px 12px 16px;overflow:auto;
  border-right:1px solid var(--border)}
.brand{display:flex;flex-direction:column;gap:2px;padding:4px 8px 18px}
.brand:hover{text-decoration:none}
.brand .bm{font-weight:600;font-size:16px;letter-spacing:.08em}
.brand .bs{color:var(--muted);font-size:var(--fs-small)}
.group-label{margin:22px 8px 6px;font-size:var(--fs-small);color:var(--muted)}
.nav-item{display:flex;align-items:center;gap:10px;height:32px;padding:0 8px;border-radius:var(--radius-control);color:var(--muted);
  border:0;background:none;width:100%;text-align:left;cursor:pointer}
.nav-item svg{width:16px;height:16px;flex:none;stroke-width:1.5}
.nav-item:hover{background:var(--raised);color:var(--text);text-decoration:none}
.nav-item.on,.nav-item[aria-current=page]{background:var(--active);color:var(--text)}
.sidebar .bottom{margin-top:auto;padding-top:16px;display:flex;flex-direction:column;gap:6px}
.side-meta{padding:0 8px 6px;font-size:var(--fs-small);color:var(--muted);line-height:18px}
.side-meta b{display:block;color:var(--text);font-weight:500}
.ver-pill{display:inline-flex;align-items:center;gap:6px;height:24px;padding:0 8px;margin:0 8px;border-radius:999px;width:max-content;
  background:var(--pill-neutral-bg);color:var(--pill-neutral-ink);font-size:var(--fs-small);font-weight:500}
.ver-pill .mono{font-family:var(--mono)}
.ver-pill:hover{text-decoration:none;background:var(--active)}
.ver-pill.warn{background:var(--pill-negative-bg);color:var(--pill-negative-ink)}
.i-sun{display:none}:root[data-theme=dark] .i-sun{display:block}:root[data-theme=dark] .i-moon{display:none}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]) .i-sun{display:block}:root:not([data-theme=light]) .i-moon{display:none}}
.topbar,.sheet-close{display:none}
.canvas{min-width:0;padding:32px 40px 40px}
main{max-width:1120px;margin:0 auto}
main>*+*{margin-top:var(--gap)}
footer{max-width:1120px;margin:32px auto 0;padding-top:12px;border-top:1px solid var(--border);color:var(--muted);font-size:var(--fs-small);
  display:flex;flex-wrap:wrap;gap:4px 16px}
/* title block */
.title-row{display:flex;align-items:flex-end;justify-content:space-between;gap:12px 16px;flex-wrap:wrap;margin-bottom:20px}
.title-row>div:first-child{min-width:0;flex:1 1 320px}
h1{font-size:var(--fs-title);line-height:1.2;font-weight:600;letter-spacing:-.01em}
h1 .tick{font-weight:600}
.title-row .sub,.lead{margin-top:6px;color:var(--muted);max-width:72ch}
.title-row .actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
h2{font-size:var(--fs-section);line-height:1.3;font-weight:600}
h3{font-size:var(--fs-body);line-height:20px;font-weight:500;margin:20px 0 8px}
.lbl{font-size:var(--fs-small);color:var(--muted);margin-bottom:4px}
.muted{color:var(--muted)}.small{font-size:var(--fs-small);line-height:18px}
.pos{color:var(--positive)}.neg{color:var(--negative)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:1px}
/* layout */
.grid{display:grid;gap:var(--gap);align-items:start}
.grid>*{min-width:0}
.g2,.g3,.g4,.g-wide{grid-template-columns:1fr}
@media (min-width:768px){.g2,.g-wide{grid-template-columns:repeat(2,minmax(0,1fr))}.g3,.g4{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (min-width:1100px){.g3{grid-template-columns:repeat(3,minmax(0,1fr))}.g4{grid-template-columns:repeat(4,minmax(0,1fr))}
  .g-wide{grid-template-columns:minmax(0,1.35fr) minmax(0,1fr)}}
.stack>*+*{margin-top:var(--gap)}
.mt{margin-top:var(--gap)}
/* panels */
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);min-width:0}
.card-h{display:flex;flex-wrap:wrap;align-items:flex-start;justify-content:space-between;gap:8px 12px;padding:var(--pad) var(--pad) 12px}
.card-h>div:first-child{flex:1 1 220px;min-width:0}
.card-h .sub{color:var(--muted);font-size:var(--fs-small);line-height:16px;margin-top:4px}
.card-b{padding:0 var(--pad) var(--pad)}
.card-h+.card-b>.table-wrap:first-child,.card-h+.card-b>.feed:first-child{margin-top:4px}
.card-b.flush{padding:0 0 4px}
.card-b.flush .table-wrap{border-top:1px solid var(--border)}
.card>.card-b:first-child{padding-top:var(--pad)}
.accent-peach,.accent-sa,.accent-factor,.accent-gold,.danger-card{border-color:var(--border)}
/* KPIs: one divided strip, label over figure, no icons, no tiles */
.strip,.tiles,.kpis{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--border);border:1px solid var(--border);
  border-radius:var(--radius);overflow:hidden}
@media (min-width:768px){.strip,.kpis{grid-template-columns:repeat(4,minmax(0,1fr))}.kpis.c3{grid-template-columns:repeat(3,minmax(0,1fr))}.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}
  .tiles.t3{grid-template-columns:repeat(3,minmax(0,1fr))}.tiles.t4{grid-template-columns:repeat(4,minmax(0,1fr))}}
.kpi,.stat,.tile{background:var(--surface);padding:14px var(--pad);min-width:0}
.kpi .l,.stat .l,.tile .l{color:var(--muted);font-size:var(--fs-small);line-height:16px}
.kpi .v,.stat .v,.tile .v{font-size:var(--fs-kpi);line-height:1.15;font-weight:500;letter-spacing:-.01em;margin-top:6px;overflow-wrap:break-word}
.kpi .v.zero,.stat .v.zero,.tile .v.zero{color:var(--muted)}
.kpi .s,.stat .s,.tile .s{font-size:var(--fs-small);line-height:16px;color:var(--muted);margin-top:4px}
.kpi .v .muted,.stat .v .muted,.tile .v .muted{font-size:14px;font-weight:400}
.tile .v{font-size:22px}
.meter{height:6px;border-radius:3px;background:var(--raised);overflow:hidden;margin-top:8px}
.meter>span{display:block;height:100%;background:var(--text)}
.meter.hot>span{background:var(--negative)}
/* forms */
label.f{display:block;font-weight:500;margin-bottom:6px}
.hint{color:var(--muted);font-size:var(--fs-small);line-height:16px;margin-top:6px}
.row{display:flex;flex-wrap:wrap;gap:var(--gap);align-items:flex-end}
.row>.fld{flex:1 1 160px;min-width:0}
input,select,textarea{width:100%;height:38px;padding:0 12px;border-radius:var(--radius-control);border:1px solid var(--control);
  background:var(--surface);color:var(--text);outline:none}
textarea{height:auto;padding:8px 12px}
input:focus,select:focus,textarea:focus{border-color:var(--text)}
input.ticker{text-transform:uppercase;font-weight:500;letter-spacing:.04em}
input[type=file]{padding:7px 12px}
input[type=checkbox]{width:16px;height:16px;height:16px;padding:0;accent-color:var(--text)}
input::placeholder{color:var(--muted)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;height:var(--control-h);padding:0 14px;border-radius:var(--radius-control);
  border:1px solid var(--control);background:var(--surface);color:var(--text);font-weight:500;cursor:pointer;max-width:100%;white-space:nowrap}
.btn:hover{background:var(--raised);text-decoration:none}
.btn:active{transform:translateY(1px)}
.btn svg{width:16px;height:16px;flex:none}
.btn-primary,.btn-sa{background:var(--primary);color:var(--primary-ink);border-color:transparent}
.btn-primary:hover,.btn-sa:hover{background:var(--primary);opacity:.9}
.btn-ghost{background:var(--surface)}
.btn-danger{color:var(--negative);border-color:var(--control)}
.btn-block{display:flex;width:100%}
.btn[disabled]{opacity:.5;cursor:not-allowed}
.checks{display:flex;flex-wrap:wrap;gap:8px 20px;margin:12px 0 4px}
label.check{display:inline-flex;align-items:center;gap:8px;cursor:pointer;min-height:32px}
label.check.warn{display:flex;align-items:flex-start;color:var(--warning);margin-top:8px;min-height:0}
label.check.warn input{margin-top:2px;flex:none}
/* on/off switch (a button with role=switch): 32x20, the knob 16 */
.switch{display:inline-flex;align-items:center;gap:10px;border:0;background:none;padding:4px 0;cursor:pointer;min-height:32px;color:var(--muted)}
.switch .knob{position:relative;width:32px;height:20px;border-radius:999px;background:var(--control);flex:none}
.switch .knob::after{content:"";position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff}
.switch.on{color:var(--text)}
.switch.on .knob{background:var(--text)}
.switch.on .knob::after{transform:translateX(12px)}
.switch .st{min-width:24px;text-align:left}
input.switch{appearance:none;-webkit-appearance:none;width:32px;height:20px;border-radius:999px;background:var(--control);position:relative;
  cursor:pointer;padding:0;border:0;flex:none;margin:0}
input.switch::after{content:"";position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff}
input.switch:checked{background:var(--text)}
input.switch:checked::after{transform:translateX(12px)}
/* pills: badges and chips, a pale tint with the dark ink of the same hue */
.badge,.chip,.step{display:inline-flex;align-items:center;gap:6px;min-height:22px;padding:2px 8px;border-radius:999px;
  background:var(--pill-neutral-bg);color:var(--pill-neutral-ink);font-size:var(--fs-tiny);line-height:16px;font-weight:500;white-space:nowrap}
.chip{white-space:normal}
.chip svg{width:14px;height:14px}
.b-buy,.b-ok,.chip-pos,.step.done{background:var(--pill-positive-bg);color:var(--pill-positive-ink)}
.b-sell,.b-err,.chip-neg,.step.fail{background:var(--pill-negative-bg);color:var(--pill-negative-ink)}
.b-hold,.b-info,.b-run,.chip-info,.step.now{background:var(--pill-info-bg);color:var(--pill-info-ink)}
.b-watch,.chip-amber{background:var(--pill-warning-bg);color:var(--pill-warning-ink)}
.b-none{background:var(--pill-neutral-bg);color:var(--pill-neutral-ink)}
.chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.steps{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 var(--gap)}
/* segmented and text tabs */
.seg{display:inline-flex;flex-wrap:wrap;gap:2px;padding:2px;border-radius:var(--radius);background:var(--raised)}
.seg button,.seg a{height:28px;min-width:36px;padding:0 10px;border-radius:var(--radius-control);border:0;background:none;color:var(--muted);
  cursor:pointer;display:inline-flex;align-items:center;justify-content:center}
.seg button.on,.seg button[aria-selected=true],.seg a.on,.seg button[aria-pressed=true]{background:var(--surface);color:var(--text)}
.seg a:hover{text-decoration:none}
.tabs{display:flex;gap:20px;border-bottom:1px solid var(--border);margin-bottom:var(--gap);overflow-x:auto}
.tabs button{position:relative;padding:8px 0;border:0;background:none;color:var(--muted);cursor:pointer;white-space:nowrap}
.tabs button[aria-selected=true]{color:var(--text)}
.tabs button[aria-selected=true]::after{content:"";position:absolute;left:0;right:0;bottom:-1px;height:2px;background:var(--text)}
.tgl{height:28px;padding:0 10px;border-radius:var(--radius-control);border:1px solid var(--control);background:var(--surface);color:var(--muted);cursor:pointer}
.tgl[aria-pressed=true]{background:var(--active);color:var(--text)}
/* alerts */
.alert{padding:10px 12px;border-radius:var(--radius);border:1px solid transparent;white-space:pre-line}
.alert-err{background:var(--pill-negative-bg);color:var(--pill-negative-ink)}
.alert-ok{background:var(--pill-positive-bg);color:var(--pill-positive-ink)}
.alert-info{background:var(--pill-info-bg);color:var(--pill-info-ink)}
.alert+*{margin-top:var(--gap)}
/* tables: a ledger, no bands, no hover fill */
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse}
th{height:36px;padding:0 12px;text-align:left;font-weight:400;font-size:var(--fs-small);color:var(--muted);border-bottom:1px solid var(--border);white-space:nowrap}
td{padding:12px;border-bottom:1px solid var(--rule);vertical-align:baseline}
tbody tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right;white-space:nowrap}
th:first-child,td:first-child{padding-left:var(--pad)}th:last-child,td:last-child{padding-right:var(--pad)}
.card-b:not(.flush) th:first-child,.card-b:not(.flush) td:first-child{padding-left:0}
.card-b:not(.flush) th:last-child,.card-b:not(.flush) td:last-child{padding-right:0}
code,td code{font:12px/16px var(--mono);background:var(--raised);padding:2px 6px;border-radius:4px}
td.empty{color:var(--muted);text-align:center;padding:24px}
.sym{font-weight:500}
a.sym:hover{text-decoration:underline}
tr.changed td:nth-child(4){font-weight:500}
tr.void td{opacity:.5;text-decoration:line-through}
/* feeds, reports */
.feed{list-style:none;margin:0;padding:0}
.feed li{display:flex;gap:12px;padding:10px 0;border-top:1px solid var(--rule);min-height:var(--row-h);align-items:center}
.feed li:first-child{border-top:0;padding-top:0}
.feed .t{flex:none;color:var(--muted);font-size:var(--fs-small);width:52px;font-variant-numeric:tabular-nums}
.feed .empty{color:var(--muted);border:0}
.report{white-space:pre-wrap;font:var(--fs-body)/22px var(--font);margin:0;color:var(--text);overflow-wrap:anywhere}
.report.mono{font:12px/18px var(--mono)}
/* accordions */
.acc{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius)}
.acc+.acc,.acc+.card,.card+.acc{margin-top:var(--gap)}
.acc>summary{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px var(--pad);cursor:pointer;list-style:none;min-height:52px}
.acc>summary::-webkit-details-marker{display:none}
.acc .ak{color:var(--muted);font-size:var(--fs-small);display:block}
.acc .at{font-size:var(--fs-section);font-weight:600;line-height:1.3;color:var(--text)}
.chev{flex:none;width:16px;height:16px;color:var(--muted);display:grid;place-items:center}
.chev svg{width:16px;height:16px}
.acc[open] .chev svg{transform:rotate(180deg)}
.acc-b{padding:0 var(--pad) var(--pad)}
/* bars */
.bars{display:flex;flex-direction:column;gap:12px}
.bars .h{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:6px}
.bars .h b{font-weight:500}
.bars .h .num{color:var(--muted)}
.track{position:relative;height:6px;border-radius:3px;background:var(--raised)}
.track>span{display:block;height:100%;border-radius:3px;background:var(--chart-1)}
.track>i{position:absolute;top:-3px;width:2px;height:12px;background:var(--chart-2)}
.watch .track>span{background:var(--warning)}.danger .track>span{background:var(--negative)}
.bars .note{font-size:var(--fs-small);line-height:16px;color:var(--muted);margin-top:4px}
/* charts */
.chart{position:relative;min-width:0}
.chart svg{display:block;width:100%;height:auto;touch-action:pan-y}
.chart .tip{position:absolute;z-index:2;pointer-events:none;min-width:220px;max-width:min(300px,90%);background:var(--surface);
  border:1px solid var(--border);border-radius:var(--radius);padding:10px 12px;font-size:var(--fs-small);line-height:16px;opacity:0;color:var(--text)}
.chart .tip b{display:block;margin-bottom:4px;font-weight:500}
.tip .tr{display:flex;justify-content:space-between;gap:12px;padding:2px 0}
.tip .tr span:first-child{color:var(--muted)}
.tip .tr span:last-child{font-variant-numeric:tabular-nums;text-align:right}
.tip .ft{border-top:1px solid var(--rule);margin-top:6px;padding-top:6px;color:var(--muted)}
.legend{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:var(--fs-small);color:var(--muted);margin:8px 0 0}
.legend .ln{display:inline-block;width:16px;height:0;border-top:2px solid;vertical-align:3px;margin-right:6px}
.legend .ln.dash{border-top-style:dashed}
.chart-bar{display:flex;flex-wrap:wrap;gap:8px;justify-content:space-between;align-items:center;margin-bottom:8px}
.big{font-size:32px;line-height:1.15;font-weight:500;letter-spacing:-.01em}
.inline{display:inline;margin:0}
.linkbtn{border:0;background:none;padding:4px 0;color:var(--negative);cursor:pointer;font-weight:500;min-height:32px}
.linkbtn:hover{text-decoration:underline}
/* settings: a label column beside a panel, sections split by a rule */
.set{display:grid;grid-template-columns:minmax(0,1fr);gap:16px;padding:28px 0;border-top:1px solid var(--border)}
.set:first-of-type{border-top:0;padding-top:0}
.set-l h2{font-size:var(--fs-section)}
.set-l p{color:var(--muted);margin-top:4px}
.set-l .now{margin-top:12px;font-size:var(--fs-small);line-height:18px;color:var(--muted)}
.set-p{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:16px}
.set-p .fld+.fld,.set-p .sw-row+.fld,.set-p .fld+.sw-row{margin-top:16px}
.set-p .sw-row+.sw-row{margin-top:12px}
.sw-row{display:flex;align-items:center;justify-content:space-between;gap:16px;min-height:36px}
.sw-row>span{min-width:0}.sw-row b{font-weight:500;display:block}.sw-row small{display:block;color:var(--muted);font-size:var(--fs-small);line-height:16px}
.set-f{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:16px}
.set-f .note{color:var(--muted);font-size:var(--fs-small);flex:1 1 200px}
.tag{display:inline-block;font-family:var(--mono);font-size:11px;color:var(--muted)}
@media (min-width:900px){.set{grid-template-columns:minmax(0,5fr) minmax(0,7fr);gap:40px}}
/* phone: the sidebar folds into a 48px bar and a full-screen sheet; the canvas takes the width */
@media (max-width:860px){
  .app{grid-template-columns:minmax(0,1fr)}
  .topbar{display:flex;align-items:center;gap:8px;height:48px;padding:0 8px 0 16px;border-bottom:1px solid var(--border);background:var(--ground);
    position:sticky;top:0;z-index:10}
  .topbar .tt{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500}
  .menu-toggle{display:inline-flex;align-items:center;justify-content:center;width:40px;height:40px;border:0;background:none;border-radius:var(--radius-control);color:var(--text)}
  .menu-toggle:hover{text-decoration:none;background:var(--raised)}
  .menu-toggle svg{width:20px;height:20px}
  .sidebar{position:fixed;inset:0;width:100%;height:100%;z-index:20;background:var(--surface);border-right:0;transform:translateX(-100%);transition:transform .2s cubic-bezier(.22,1,.36,1)}
  .sidebar:target,.sidebar.open{transform:none}
  .sheet-close{display:inline-flex;align-items:center;justify-content:center;position:absolute;right:8px;top:8px;width:40px;height:40px;border-radius:var(--radius-control);color:var(--text)}
  .sheet-close:hover{text-decoration:none;background:var(--raised)}
  .sheet-close svg{width:20px;height:20px}
  .canvas{padding:16px}
  .kpi,.stat,.tile{padding:12px 14px}
  .kpi .v,.stat .v{font-size:24px}
  .big{font-size:28px}
  .card-h{padding:16px 16px 8px}.card-b{padding:0 16px 16px}.card>.card-b:first-child{padding-top:16px}.acc>summary{padding:12px 16px}.acc-b{padding:0 16px 16px}
  th:first-child,td:first-child{padding-left:16px}th:last-child,td:last-child{padding-right:16px}
  .title-row{margin-bottom:16px}
}
@media (max-width:480px){.seg{display:flex;width:100%}.seg button,.seg a{flex:1}.strip,.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
  .kpi:last-child:nth-child(odd),.stat:last-child:nth-child(odd){grid-column:1/-1}}
@media (max-width:360px){.strip,.kpis,.tiles{grid-template-columns:1fr}.kpi:last-child:nth-child(odd),.stat:last-child:nth-child(odd){grid-column:auto}}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{transition-duration:.01ms!important;animation:none!important}}
"""
)

# ─── icons (stroke, currentColor) ─────────────────────────────────────────────

_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
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
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    "home": '<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/>',
    "wallet": '<path d="M4 7h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a1 1 0 0 1-1-1V7z"/><path d="M4 7l11-3v3M16 13h2"/>',
    "bell": '<path d="M6 16V11a6 6 0 0 1 12 0v5l2 2H4l2-2z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    "spark": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/>',
    "bot": '<rect x="4" y="8" width="16" height="12" rx="3"/><path d="M12 4v4M9 14h.01M15 14h.01"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "video": '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3"/>',
    "tag": '<path d="M3 12V4h8l9 9-8 8-9-9z"/><path d="M7 8h.01"/>',
    "list": '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
    "book": '<path d="M4 4h7a2 2 0 0 1 2 2v14a2 2 0 0 0-2-2H4z"/><path d="M20 4h-7a2 2 0 0 0-2 2v14a2 2 0 0 1 2-2h7z"/>',
    "life": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/><path d="M5.6 5.6 9 9M15 15l3.4 3.4M5.6 18.4 9 15M15 9l3.4-3.4"/>',
    "shield": '<path d="M12 3 5 6v6c0 4 3 7 7 9 4-2 7-5 7-9V6z"/>',
    "file": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
    "eraser": '<path d="m4 15 9-9 6 6-6 6H8z"/><path d="M4 20h16"/>',
}


def icon(name: str, cls: str = "") -> str:
    svg = _SVG.replace("<svg ", f'<svg class="{cls}" ', 1) if cls else _SVG
    return svg + ICONS[name] + "</svg>"


SUN = icon("sun", "i-sun")
MOON = icon("moon", "i-moon")

e = html.escape

# The sidebar: the main screens, then the tools and the system pages (key, path, label, icon).
NAV = (("home", "/", "Tổng quan", "home"), ("taisan", "/taisan", "Danh mục", "wallet"))
TOOLS = (("ai", "/ai", "AI & cảnh báo", "bell"), ("claude", "/claude", "Claude chọn bóng", "spark"),
         ("bot", "/bot", "Bot giao dịch", "bot"))
SYSTEM = (("caidat", "/caidat", "Cài đặt", "gear"), ("zoom", "/zoom", "Cấu hình Zoom", "video"),
          ("phienban", "/phienban", "Phiên bản", "tag"))
_WEEKDAY = ("Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật")
_PHASE = {"closed": "Thị trường nghỉ", "pre_open": "Trước giờ mở cửa", "ato": "Phiên ATO", "continuous": "Đang khớp lệnh",
          "lunch": "Nghỉ trưa", "atc": "Phiên ATC", "put_through": "Thoả thuận", "after_close": "Đã đóng cửa"}


def date_line() -> str:
    """"Thứ 7 25/10" over "15/09 âm lịch · Thị trường nghỉ" (Vietnam time), for the sidebar."""
    from tradingagents.market import vn_rules
    from vnbot import amlich

    now = vn_rules.now_vn()
    try:
        lunar = amlich.short(now.date())
    except Exception:  # noqa: BLE001 - the line shows the solar date anyway
        lunar = ""
    try:
        phase = _PHASE.get(vn_rules.session_phase(now), "")
    except Exception:  # noqa: BLE001
        phase = ""
    rest = " · ".join(p for p in (lunar, phase) if p)
    return f'<div class="side-meta"><b>{e(_WEEKDAY[now.weekday()])} {now:%d/%m}</b>{e(rest)}</div>'


def nav_link(key: str, href: str, label: str, ic: str, active: str) -> str:
    on = key == active
    return (f'<a href="{href}" class="nav-item{" on" if on else ""}"' + (' aria-current="page"' if on else "")
            + f">{icon(ic)}<span>{e(label)}</span></a>")


def sidebar_nav(active: str, show_bot: bool) -> str:
    links = "".join(nav_link(k, h, t, i, active) for k, h, t, i in NAV)
    tools = "".join(nav_link(k, h, t, i, active) for k, h, t, i in TOOLS if show_bot or k != "bot")
    system = "".join(nav_link(k, h, t, i, active) for k, h, t, i in SYSTEM)
    return (f'<nav aria-label="Điều hướng chính">{links}<div class="group-label">Công cụ</div>{tools}'
            f'<div class="group-label">Hệ thống</div>{system}</nav>')


def theme_button() -> str:
    return (f'<button class="nav-item" id="theme-toggle" type="button" aria-label="Đổi giao diện sáng/tối">{MOON}{SUN}'
            "<span>Sáng / tối</span></button>")


def shell(title: str, body: str, *, nav: str, bottom: str = "", foot: str = "", refresh: int | None = None,
          brand_href: str = "/", brand_sub: str = "TradingAgents VN", head: str = "") -> str:
    """The page: the sidebar (``nav`` and ``bottom``), the canvas with ``body`` and the
    footer lines ``foot``. ``title`` is the document title and the phone bar's name."""
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return (
        '<!doctype html><html lang="vi"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="theme-color" content="#f2f4f7" media="(prefers-color-scheme: light)">'
        '<meta name="theme-color" content="#000000" media="(prefers-color-scheme: dark)">'
        f"{meta}<title>{e(title)} · TradingAgents VN</title>{FAVICON}{THEME_BOOT}{FONTS}<style>{STYLE}</style>{head}</head><body>"
        '<a class="skip" href="#main">Bỏ qua tới nội dung</a>'
        '<div class="app">'
        f'<aside class="sidebar" id="sidebar"><a class="sheet-close" href="#main" aria-label="Đóng menu">{icon("x")}</a>'
        f'<a class="brand" href="{brand_href}" aria-label="LCTA, về trang đầu"><span class="bm">LCTA</span><span class="bs">{e(brand_sub)}</span></a>'
        f'{nav}<div class="bottom">{bottom}</div></aside>'
        '<div class="canvas">'
        f'<header class="topbar"><span class="tt">{e(title)}</span>'
        f'<a class="menu-toggle" href="#sidebar" aria-label="Mở menu">{icon("menu")}</a></header>'
        f'<main id="main" tabindex="-1">{body}</main>'
        f"<footer>{foot}</footer></div></div>{THEME_TOGGLE}</body></html>"
    )


def page(title: str, body: str, *, active: str = "", refresh: int | None = None, show_bot: bool = True,
         show_version: bool = True) -> str:
    """A private page. ``show_version``: the version in the sidebar and the footer (off on
    the error pages, which a visitor without the password can see)."""
    from vnbot import version as ver

    pill, foot = "", ""
    if show_version:
        ok = ver.is_release()
        pill = (f'<a class="ver-pill{"" if ok else " warn"}" href="/phienban" title="{e(ver.label())}">'
                f'<span class="mono">{e(ver.VERSION)}</span>{"" if ok else " · khác bản phát hành"}</a>')
        foot = f'<span>TradingAgents VN {e(ver.label())} · <a href="/phienban">lịch sử phiên bản</a></span>'
    bottom = date_line() + theme_button() + pill
    foot = ("<span>Thông tin chỉ mang tính tham khảo, không phải khuyến nghị đầu tư.</span>"
            "<span>Bot không đặt lệnh tới công ty chứng khoán.</span>" + foot)
    return shell(title, body, nav=sidebar_nav(active, show_bot), bottom=bottom, foot=foot, refresh=refresh)


def title(text: str, lead: str = "", right: str = "") -> str:
    """The page's title block: the h1, one line under it, the actions on the right."""
    return (f'<div class="title-row"><div><h1>{text}</h1>' + (f'<p class="sub">{lead}</p>' if lead else "")
            + "</div>" + (f'<div class="actions">{right}</div>' if right else "") + "</div>")


def _cell(label: str, value: str, sub: str, cls: str, tag: str = "kpi") -> str:
    zero = " zero" if value in ("0", "—", "0đ", "0 đ") else ""
    return (f'<div class="{tag}"><div class="l">{label}</div><div class="v {cls}{zero}">{value}</div>'
            + (f'<div class="s">{sub}</div>' if sub else "") + "</div>")


def strip(items: list[tuple[str, str, str]], *, cls: str = "") -> str:
    """The headline numbers of a page: (label, value html, sub html), one divided strip."""
    return f'<div class="strip{" " + cls if cls else ""}">' + "".join(_cell(l, v, s, "", "stat") for l, v, s in items) + "</div>"


def meter(used: int, cap: int | None) -> str:
    """A bar of ``used`` out of ``cap`` (none without a cap); red from 85%."""
    if not cap:
        return ""
    share = max(0.0, min(1.0, used / cap))
    return (f'<div class="meter{" hot" if share >= 0.85 else ""}" role="img" aria-label="{used}/{cap}">'
            f'<span style="width:{share * 100:.1f}%"></span></div>')


def card(title: str, body: str, *, sub: str = "", action: str = "", flush: bool = False, cls: str = "",
         kicker: str = "", id: str = "") -> str:  # noqa: A002 - the HTML attribute
    """A panel: ``title`` (h2) with ``sub`` under it, ``action`` on the right; ``kicker`` is
    kept for callers but no longer drawn (the title says it)."""
    head = ""
    if title:
        head = ('<div class="card-h"><div>' + (f"<h2>{title}</h2>" if title else "")
                + (f'<div class="sub">{sub}</div>' if sub else "") + f"</div>{action}</div>")
    ident = f' id="{e(id)}"' if id else ""
    return f'<section class="card {cls}"{ident}>{head}<div class="card-b{" flush" if flush else ""}">{body}</div></section>'


def acc(ident: str, kicker: str, title: str, body: str, *, opened: bool = False) -> str:
    """A section that opens and closes (native <details>: keyboard and screen readers for free)."""
    return (f'<details class="acc" id="{e(ident)}"{" open" if opened else ""}><summary>'
            f'<span><span class="at">{e(title)}</span><span class="ak">{e(kicker)}</span></span>'
            f'<span class="chev">{icon("chev")}</span></summary><div class="acc-b">{body}</div></details>')


def chip(text: str, kind: str = "") -> str:
    return f'<span class="chip{" chip-" + kind if kind else ""}">{text}</span>'


def tile(label: str, value: str, sub: str = "", *, cls: str = "", ic: str = "") -> str:
    return _cell(label, value, sub, cls, "tile")


def tabs(name: str, items: list[tuple[str, str]]) -> str:
    """Text tabs (label, panel html) - every panel shows without JavaScript."""
    buttons = "".join(f'<button type="button" role="tab" id="{name}-t{i}" aria-controls="{name}-p{i}" '
                      f'aria-selected="{"true" if i == 0 else "false"}">{label}</button>' for i, (label, _) in enumerate(items))
    panels = "".join(f'<div role="tabpanel" id="{name}-p{i}" aria-labelledby="{name}-t{i}">{panel}</div>'
                     for i, (_, panel) in enumerate(items))
    return f'<div data-tabs><div class="tabs" role="tablist">{buttons}</div>{panels}</div>'


_LEAD_GLYPH = re.compile(r"^[\u2600-\u27BF\u2B50\U0001F300-\U0001FAFF][\uFE0F]?\s*")


def alert(text: str, kind: str = "info") -> str:
    """A flat status line; a leading glyph of the bot's message (✅, ⚠️) is dropped, the tint says it."""
    role = "alert" if kind == "err" else "status"
    text = _LEAD_GLYPH.sub("", text or "")
    return f'<div class="alert alert-{kind}" role="{role}">{e(text)}</div>' if text else ""


_ACTION = {
    "BUY": ("b-buy", "Mua"), "SELL": ("b-sell", "Bán"), "HOLD": ("b-hold", "Giữ"),
    "WATCH": ("b-watch", "Theo dõi"), "NO_TRADE": ("b-none", "Đứng ngoài"),
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
    """1.012.300.000 -> "1,012 tỷ"; 532.600.000 -> "532,6 tr" (KPI cells)."""
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
    return _cell(label, value, sub, cls, "kpi")


def table(headers: list[tuple[str, str]], rows: list[str], empty: str) -> str:
    """``headers`` as (label, css class); ``rows`` already rendered <tr>...</tr>."""
    head = "".join(f'<th scope="col" class="{c}">{h}</th>' for h, c in headers)
    body = "".join(rows) or f'<tr><td class="empty" colspan="{len(headers)}">{empty}</td></tr>'
    # Focusable: a table wider than a phone scrolls sideways, with the keyboard too.
    return (f'<div class="table-wrap" tabindex="0" role="region" aria-label="Bảng, cuộn ngang được">'
            f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')


def sparkline(values: list[float], *, label: str = "") -> str:
    """A small line of ``values`` (oldest first) in grey; the change is said in words beside it."""
    values = [float(v) for v in values if v]
    if len(values) < 2:
        return '<span class="spark muted small">chưa có dữ liệu</span>'
    w, h = 120, 32
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pts = " ".join(f"{i / (len(values) - 1) * w:.1f},{h - 2 - (v - lo) / span * (h - 4):.1f}" for i, v in enumerate(values))
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{e(label)}">'
            f'<polyline points="{pts}" fill="none" style="stroke:var(--chart-2);stroke-width:1.5;stroke-linejoin:round"/></svg>')


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

_TICK = "fill:var(--muted);font-size:12px"


def nav_chart(dates: list[str], series: list[dict], *, run_time: str | None = None) -> str:
    """Indexed (start = 100) lines on one axis. ``series``: {name, color (css), values, dashed};
    ``run_time``: when the bot runs each day (HH:MM), for the empty-chart hint."""
    if len(dates) < 2:
        when = f"tự động lúc {html.escape(run_time)} mỗi ngày" if run_time else "sau mỗi phiên"
        return ('<p class="muted" style="margin:0">Biểu đồ NAV sẽ hiển thị sau hai phiên giao dịch của bot '
                f'({when}, hoặc chọn “Chạy phiên ngay”).</p>')
    w, h, left, right, top, bottom = 680, 260, 48, 150, 12, 28
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
        grid.append(f'<line x1="{left}" x2="{w - right}" y1="{y(v):.1f}" y2="{y(v):.1f}" style="stroke:var(--grid);stroke-width:1"/>'
                    f'<text x="{left - 8}" y="{y(v) + 4:.1f}" text-anchor="end" style="{_TICK}">{v - 100:+.0f}%</text>')
    base = (f'<line x1="{left}" x2="{w - right}" y1="{y(100):.1f}" y2="{y(100):.1f}" '
            'style="stroke:var(--chart-2);stroke-width:1"/>')
    lines, labels = [], []
    used_y: list[float] = []
    for s in series:
        pts = [(x(i), y(v)) for i, v in enumerate(s["values"]) if v is not None]
        if len(pts) < 2:
            continue
        dash = "stroke-dasharray:5 4;" if s.get("dashed") else ""
        path = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        lines.append(f'<polyline points="{path}" fill="none" style="stroke:{s["color"]};stroke-width:2;'
                     f'{dash}stroke-linejoin:round;stroke-linecap:round"/>')
        last = next(v for v in reversed(s["values"]) if v is not None)
        ly = pts[-1][1]
        while any(abs(ly - u) < 16 for u in used_y):
            ly += 16
        used_y.append(ly)
        change = f"{last - 100:+.1f}%".replace(".", ",")
        labels.append(f'<text x="{w - right + 8}" y="{ly + 4:.1f}" style="fill:var(--text);font-size:12px;font-weight:500">'
                      f'{e(s["name"])} {change}</text>')
    axis = (f'<text x="{left}" y="{h - 8}" style="{_TICK}">{e(dates[0])}</text>'
            f'<text x="{w - right}" y="{h - 8}" text-anchor="end" style="{_TICK}">{e(dates[-1])}</text>')
    hair = (f'<line class="hair" x1="0" x2="0" y1="{top}" y2="{h - bottom}" '
            'style="stroke:var(--muted);stroke-width:1;stroke-dasharray:3 3;opacity:0"/>')
    hit = f'<rect class="hit" x="{left}" y="0" width="{w - left - right}" height="{h}" fill="transparent"/>'
    data = {"w": w, "l": left, "r": right, "dates": dates,
            "series": [{"name": s["name"], "color": s["color"], "values": s["values"]} for s in series]}
    legend = "".join(
        f'<span><span class="ln{" dash" if s.get("dashed") else ""}" style="border-color:{s["color"]}"></span>{e(s["name"])}</span>'
        for s in series)
    return (f'<div class="chart" data-series="{e(json.dumps(data))}">'
            f'<svg viewBox="0 0 {w} {h}" role="img" tabindex="0" '
            f'aria-label="NAV các tài khoản so với VNINDEX, chỉ số hoá 100; phím mũi tên để xem từng ngày">'
            f'{"".join(grid)}{base}{"".join(lines)}{"".join(labels)}{axis}{hair}{hit}</svg>'
            f'<div class="tip" role="status" aria-live="polite"></div></div>'
            f'<div class="legend">{legend}</div>{_CHART_JS}')


# ─── asset chart (Tài sản) ────────────────────────────────────────────────────

_ASSET_JS = """<script>
(function(){
  document.querySelectorAll('.achart[data-points]').forEach(function(box){
    var P=JSON.parse(box.getAttribute('data-points')),svg=box.querySelector('svg'),tip=box.querySelector('.tip'),
        leg=box.querySelector('.legend'),dm=box.querySelector('[data-x=dm]'),vn=box.querySelector('[data-x=vni]'),
        cmp=box.querySelector('[data-cmp]'),W=680,H=260,L=58,R=14,T=12,B=30,
        st={range:box.getAttribute('data-range')||'0',cmp:true},cur=null;
    function size(){W=Math.max(260,Math.round(svg.parentNode.clientWidth||680));H=W<560?Math.round(Math.max(180,W*.6)):260;
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
    function txt(x,y,s,anchor){var t=el('text',{x:x,y:y,'text-anchor':anchor||'start',style:'fill:var(--muted);font-size:12px'});t.textContent=s;return t;}
    function chip(node,label,v){if(!node)return;node.textContent=label+' '+pc(v);
      node.className='chip '+(v==null?'':v>=0?'chip-pos':'chip-neg');}
    function draw(){
      size();var r=span(),a=r[0],b=r[1],m=b-a;svg.innerHTML='';tip.style.opacity=0;
      var nav=rebase(P.n,a,b),vni=rebase(P.v,a,b);
      chip(dm,'Danh mục',nav[m-1]);chip(vn,'VN-Index',vni[m-1]);
      if(m<2){leg.innerHTML='<span class="muted">Chưa đủ dữ liệu cho khoảng này.</span>';cur=null;return;}
      var S=st.cmp?[{n:'Danh mục',c:'var(--chart-3)',v:nav},{n:'VN-Index',c:'var(--chart-2)',v:vni}]
                  :[{n:'Tổng tài sản',c:'var(--chart-3)',v:P.t.slice(a,b)},{n:'Vốn gốc',c:'var(--chart-1)',v:P.c.slice(a,b),dash:1}];
      var vals=[];S.forEach(function(s){s.v.forEach(function(x){if(x!=null)vals.push(x);});});
      if(st.cmp)vals.push(0);
      var lo=Math.min.apply(null,vals),hi=Math.max.apply(null,vals);if(hi===lo){hi+=1;lo-=1;}
      var step=nice((hi-lo)/4);lo=Math.floor(lo/step)*step;hi=Math.ceil(hi/step)*step;
      function x(i){return L+i/(m-1)*(W-L-R);}function y(v){return T+(hi-v)/(hi-lo)*(H-T-B);}
      for(var v=lo;v<=hi+step/2;v+=step){svg.appendChild(el('line',{x1:L,x2:W-R,y1:y(v),y2:y(v),style:'stroke:var(--grid);stroke-width:1'}));
        svg.appendChild(txt(L-8,y(v)+4,st.cmp?(Math.round(v*10)/10+'%').replace('.',','):money(v),'end'));}
      if(st.cmp)svg.appendChild(el('line',{x1:L,x2:W-R,y1:y(0),y2:y(0),style:'stroke:var(--chart-2);stroke-width:1'}));
      var ticks=Math.min(W<560?4:6,m);for(var q=0;q<ticks;q++){var i=Math.round(q*(m-1)/(ticks-1||1));
        svg.appendChild(txt(x(i),H-10,dm2(P.d[a+i]),q===0?'start':q===ticks-1?'end':'middle'));}
      S.forEach(function(s){var pts=[];s.v.forEach(function(v,i){if(v!=null)pts.push([x(i),y(v)]);});if(pts.length<2)return;
        var d=pts.map(function(p){return p[0].toFixed(1)+','+p[1].toFixed(1);}).join(' ');
        svg.appendChild(el('polyline',{points:d,fill:'none',style:'stroke:'+s.c+';stroke-width:2;stroke-linejoin:round;stroke-linecap:round;'+(s.dash?'stroke-dasharray:6 5':'')}));});
      var hair=el('line',{x1:0,x2:0,y1:T,y2:H-B,style:'stroke:var(--muted);stroke-width:1;stroke-dasharray:3 3;opacity:0'});svg.appendChild(hair);
      var hit=el('rect',{x:L,y:0,width:W-L-R,height:H,fill:'transparent'});svg.appendChild(hit);
      leg.innerHTML=S.map(function(s){return '<span><span class="ln'+(s.dash?' dash':'')+'" style="border-color:'+s.c+'"></span>'+s.n+'</span>';}).join('');
      cur={a:a,m:m,x:x,hair:hair,nav:nav,vni:vni,i:m-1};
      function show(i){var j=a+i,hx=x(i),rc=svg.getBoundingClientRect();cur.i=i;
        hair.setAttribute('x1',hx);hair.setAttribute('x2',hx);hair.style.opacity=1;
        var pnl=P.t[j]-P.c[j],pp=P.c[j]?pnl/P.c[j]*100:null,rows=[['Tổng tài sản',full(P.t[j]),''],['Tiền mặt',full(P.k[j]),''],
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

ASSET_RANGES = (("7", "7 ngày"), ("30", "1 tháng"), ("90", "3 tháng"), ("180", "6 tháng"), ("365", "1 năm"), ("0", "Tất cả"))


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
            f'<div class="big num">{money(total)}đ</div>'
            f'<div class="chips" style="margin:8px 0 12px">'
            f'<span class="chip {"chip-pos" if (dm or 0) >= 0 else "chip-neg"}" data-x="dm">Danh mục {pct(dm)}</span>'
            f'<span class="chip" data-x="vni">VN-Index {pct(vni)}</span><span class="muted small">trong kỳ</span></div>'
            '<div class="chart-bar"><div class="seg" role="group" aria-label="Khoảng thời gian">' + ranges + "</div>"
            '<button type="button" class="tgl" data-cmp aria-pressed="true">So sánh VN-Index</button></div>'
            '<div class="chart"><svg viewBox="0 0 680 260" role="img" tabindex="0" '
            'aria-label="Hiệu suất danh mục so với VN-Index; phím mũi tên để xem từng ngày"></svg>'
            '<div class="tip" role="status" aria-live="polite"></div></div>'
            '<div class="legend"></div>'
            '<div class="tiles" style="margin-top:12px">'
            + tile("Tổng tài sản", f"{money(total)}đ") + tile("Vốn gốc", f"{money(capital)}đ") + "</div>"
            f'<p class="muted small" style="margin:12px 0 0">{len(points)} điểm dữ liệu · cập nhật 15:30 các ngày giao dịch</p>'
            '<noscript><p class="muted small">Bật JavaScript để xem biểu đồ.</p></noscript></div>'
            f"{_ASSET_JS}")
