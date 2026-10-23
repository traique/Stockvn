"""Dashboard web (FastAPI) – thiết kế hiện đại, sáng/tối, tối ưu mobile. Không phụ thuộc CDN."""
import csv
import hashlib
import io
import json
import logging
import os
import socket
import threading
import time
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import uvicorn
from .config import cfg
from .version import VERSION
from . import ui
from . import version as ver
from .channels import NAMES as SWITCH_NAMES

log = logging.getLogger("dashboard")

APP_JS = r"""<script>
const $=id=>document.getElementById(id);
const put=(id,h)=>{const el=$(id);if(el)el.innerHTML=h};
const f=(n,d=2)=>n==null||isNaN(n)?'—':Number(n).toLocaleString('vi-VN',{minimumFractionDigits:d,maximumFractionDigits:d});
const px=(n)=>n==null?'—':Number(n).toPrecision(5).replace(/\.?0+$/,'');
const t=s=>s?new Date(s*1000).toLocaleString('vi-VN',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'—';
const ago=s=>{const m=Math.round((Date.now()/1000-s)/60);return m<1?'vừa xong':m<60?m+' phút':Math.round(m/60)+' giờ'};
const sideTag=s=>`<span class="side ${s}">${s==='long'?'LONG ▲':'SHORT ▼'}</span>`;
const sgn=n=>n>=0?'+':'−';
let D=null,tab='trades';
// ---------- chart (1 series, inline SVG, crosshair tooltip)
function drawChart(curve){
  const svg=$('svg'),tip=$('tip');if(!svg)return;
  const W=svg.clientWidth||600,H=220,P={l:8,r:8,t:14,b:18};svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  if(!curve||curve.length<2){svg.innerHTML=`<text x="${W/2}" y="${H/2}" text-anchor="middle" fill="var(--mut)" font-size="12">Chưa đủ dữ liệu – bot ghi vốn mỗi 5 phút</text>`;return}
  const xs=curve.map(p=>p[0]),ys=curve.map(p=>p[1]);
  const x0=xs[0],x1=xs[xs.length-1],lo=Math.min(...ys),hi=Math.max(...ys),pad=(hi-lo)||1;
  const X=x=>P.l+(x-x0)/((x1-x0)||1)*(W-P.l-P.r),Y=y=>P.t+(1-(y-(lo-pad*.1))/(pad*1.2))*(H-P.t-P.b);
  const start=D.start,yS=Y(start);
  let d='',a='';curve.forEach((p,i)=>{d+=(i?'L':'M')+X(p[0]).toFixed(1)+' '+Y(p[1]).toFixed(1)+' '});
  a=d+`L${X(x1).toFixed(1)} ${H-P.b} L${X(x0).toFixed(1)} ${H-P.b} Z`;
  const last=ys[ys.length-1],col=last>=start?'var(--up)':'var(--dn)';
  const ticks=[0,.5,1].map(k=>lo-pad*.1+pad*1.2*k);
  svg.innerHTML=`<defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity=".22"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>
  ${ticks.map(v=>`<line x1="${P.l}" x2="${W-P.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--line)" stroke-width="1"/><text x="${W-P.r}" y="${Y(v)-3}" text-anchor="end" fill="var(--mut)" font-size="10">${f(v,2)}</text>`).join('')}
  <line x1="${P.l}" x2="${W-P.r}" y1="${yS}" y2="${yS}" stroke="var(--gold)" stroke-dasharray="3 4" stroke-width="1"/><text x="${P.l}" y="${yS-3}" fill="var(--gold)" font-size="10">vốn ban đầu ${f(start,2)}</text>
  <path d="${a}" fill="url(#g)"/><path d="${d}" fill="none" stroke="${col}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
  <circle cx="${X(x1)}" cy="${Y(last)}" r="4" fill="${col}" stroke="var(--card)" stroke-width="2"/>
  <line id="cx" y1="${P.t}" y2="${H-P.b}" stroke="var(--mut)" stroke-width="1" opacity="0"/><circle id="cp" r="5" fill="${col}" stroke="var(--card)" stroke-width="2" opacity="0"/>
  <text x="${P.l}" y="${H-4}" fill="var(--mut)" font-size="10">${t(x0)}</text><text x="${W-P.r}" y="${H-4}" text-anchor="end" fill="var(--mut)" font-size="10">${t(x1)}</text>`;
  const cx=svg.querySelector('#cx'),cp=svg.querySelector('#cp');
  const move=e=>{const r=svg.getBoundingClientRect();const px_=((e.touches?e.touches[0].clientX:e.clientX)-r.left)*W/r.width;
    let i=0,best=1e9;curve.forEach((p,k)=>{const dd=Math.abs(X(p[0])-px_);if(dd<best){best=dd;i=k}});
    const p=curve[i];cx.setAttribute('x1',X(p[0]));cx.setAttribute('x2',X(p[0]));cx.setAttribute('opacity',1);cp.setAttribute('cx',X(p[0]));cp.setAttribute('cy',Y(p[1]));cp.setAttribute('opacity',1);
    tip.style.left=(X(p[0])*r.width/W+8)+'px';tip.style.top=(Y(p[1])*r.height/H)+'px';tip.style.opacity=1;tip.textContent=`${t(p[0])} · ${f(p[1],4)} USDT`};
  const out=()=>{cx.setAttribute('opacity',0);cp.setAttribute('opacity',0);tip.style.opacity=0};
  svg.onmousemove=move;svg.ontouchstart=svg.ontouchmove=e=>{move(e)};svg.onmouseleave=svg.ontouchend=out;
}


// ---------- render
function render(d){
  D=d;const s=d.stats||{},pnl=d.equity-d.start,pct=100*pnl/(d.start||1),cls=pnl>=0?'up':'dn';
  const run=d.halted?'Đã dừng (cầu dao)':d.paused?'Tạm dừng':'Đang chạy';
  const b=d.btc||{};
  const btc=b.btc_price?` · BTC ${Number(b.btc_price).toLocaleString('vi-VN')} <span class="${b.btc_chg_1h>=0?'up':'dn'}">${b.btc_chg_1h>=0?'▲ +':'▼ '}${f(b.btc_chg_1h,2)}%/1h</span> (${({1:'xu hướng tăng',[-1]:'xu hướng giảm',0:'đi ngang'})[b.btc_trend_1h]||'—'})`:'';
  put('sub',`<span class="dot ${d.halted?'warn':d.paused?'off':''}" aria-hidden="true"></span>${run} · <span class="pill gold">${d.mode==='paper'?'Chạy ảo':'Tiền thật'}</span> <span class="pill">${esc(d.instance)} · ${d.is_leader?'leader':'dự phòng'}</span>${btc} · cập nhật ${ago(d.ts)} trước`);
  const upnl=(d.open||[]).reduce((a,x)=>a+(x.upnl||0),0);
  put('strip',[
    ['Vốn hiện tại',`${f(d.equity,2)}<small>USDT</small>`,`<span class="${cls}">${pnl>=0?'▲':'▼'} ${f(Math.abs(pnl),2)} (${sgn(pnl)}${f(Math.abs(pct),1)}%)</span> so với ${f(d.start,2)}`],
    ['Lãi/lỗ đã chốt',`<span class="${(s.total_pnl||0)>=0?'up':'dn'}">${sgn(s.total_pnl||0)}${f(Math.abs(s.total_pnl||0),2)}</span><small>USDT</small>`,`${s.trades||0} lệnh đã đóng`],
    ['Tỉ lệ thắng',`${s.trades?s.winrate+'%':'—'}`,`${s.wins||0} thắng · ${s.losses||0} thua${s.trades&&s.trades<30?' · cần ≥30 lệnh để tin cậy':''}`],
    ['Lệnh đang mở',`${(d.open||[]).length}`,(d.open||[]).length?`<span class="${upnl>=0?'up':'dn'}">${sgn(upnl)}${f(Math.abs(upnl),3)} USDT chưa chốt</span>`:'đang chờ tín hiệu'],
  ].map(([l,v,x])=>`<div class="stat"><div class="l">${l}</div><div class="v">${v}</div><div class="s">${x}</div></div>`).join(''));
  const aiPct=Math.min(100,100*d.ai_calls_today/(d.ai_budget||1));
  put('tiles',[
    ['Profit factor',s.profit_factor??'—',s.profit_factor>=1.3?'tốt (≥ 1,3)':'mục tiêu ≥ 1,3'],
    ['Thắng / thua TB',`<span class="up">+${f(s.avg_win,3)}</span> / <span class="dn">${f(s.avg_loss,3)}</span>`,'USDT mỗi lệnh'],
    ['AI hôm nay',`${d.ai_calls_today}<span class="muted" style="font-size:13px"> / ${d.ai_budget}</span>`,`<div class="meter" role="img" aria-label="${d.ai_calls_today} trên ${d.ai_budget} lượt"><span style="width:${aiPct}%"></span></div>`],
    ['Model AI gần nhất',`<span style="font-size:14px;word-break:break-all">${esc((d.ai_model||'—').split('/').slice(-1)[0])}</span>`,esc((d.ai_model||'').split('/')[0]||'chưa gọi')],
  ].map(([k,v,x])=>`<div class="kpi"><div class="l">${k}</div><div class="v">${v}</div><div class="s">${x}</div></div>`).join(''));
  put('meta',`${d.shared_db?'DB dùng chung (Supabase)':'DB cục bộ (SQLite)'} · leader: <b>${esc(d.leader||'—')}</b> · ${(d.instances||[]).map(i=>esc(i.instance)+' '+(((i.info||'').match(/v[\d.]+/)||[''])[0])+' '+(Date.now()/1000-i.ts>120?'(mất liên lạc)':'●')).join(' · ')}`);
  renderAI(d.ai||{});
  renderLearn(d.learn);
  if($('svg')){drawChart(d.curve);put('crange',d.curve&&d.curve.length?`${d.curve.length} điểm · ghi mỗi 5 phút`:'');}
  if($('open')){
    put('openN',(d.open||[]).length?`${d.open.length} lệnh`:'');
    put('open',(d.open||[]).length?d.open.map(x=>`<div class="row"><div class="t">${sideTag(x.side)} ${x.symbol.split('/')[0]}</div>
      <div class="r ${x.upnl>=0?'up':'dn'}">${x.upnl==null?'—':sgn(x.upnl)+f(Math.abs(x.upnl),4)}<small>USDT${x.price&&x.margin?' · '+f(100*x.upnl/x.margin,1)+'% ký quỹ':''}</small></div>
      <div class="m">Vào ${px(x.entry)} · Giá ${px(x.price)} · SL ${px(x.sl)} · ${x.exit_mode==='hybrid'?(x.tp1_done?`đã chốt ${f(100*(1-x.notional/x.notional0),0)}% (${sgn(x.realized)}${f(Math.abs(x.realized),3)}), phần còn lại bám sóng`:`TP1 ${px(x.tp1)} rồi bám sóng`):x.exit_mode==='trail'?'bám sóng (trailing ATR)':`TP ${px(x.tp)}`} · ${f(x.notional,1)} USDT · mở ${ago(x.opened_at)} trước</div></div>`).join('')
      :'<div class="empty">Không có lệnh nào đang mở – bot đang chờ tín hiệu đủ điều kiện.</div>');
    const ls=d.last_scan;put('scan',ls?`<b>Lần quét cuối</b> ${ago(ls.ts)} trước · ${ls.scanned} coin · <b class="${ls.passed?'up':''}">${ls.passed} đạt</b>${ls.top&&ls.top.length?' ('+ls.top.map(esc).join(', ')+')':''}<br>Loại vì: ${Object.entries(ls.reasons||{}).map(([k,v])=>`${esc(k)} <b>${v}</b>`).join(' · ')||'—'}${ls.near&&ls.near.length?`<br>Suýt đạt: ${ls.near.map(esc).join(', ')}`:''}`:'Chưa quét lần nào.');
  }
  renderLog();
}
function renderAI(a){
  const sub=$('aiSub'),last=$('aiLast'),el=$('aiModels');if(!el)return;
  if(!a.enabled){sub.textContent='tắt';last.textContent='AI đang tắt (AI_ENABLED=false hoặc chưa cấu hình provider) – bot vào lệnh thuần kỹ thuật.';el.innerHTML='';return}
  sub.innerHTML=`${a.calls_today}/${a.budget} hôm nay · ngưỡng ${a.min_confidence}% · thứ tự: ${(a.order||[]).join(' → ')} · khi AI lỗi: ${a.fail_policy==='skip'?'bỏ qua':a.fail_policy==='score'?'vào theo điểm KT':'tự động'}`;
  if(a.last_result){const r=a.last_result,ok=r.decision===r.side.toUpperCase()&&r.confidence>=a.min_confidence;
    last.innerHTML=`<b>Quyết định gần nhất</b> · ${t(r.ts)} — ${sideTag(r.side)} ${r.symbol.split('/')[0]} (điểm ${r.score}) → <b class="${ok?'up':'dn'}">${r.decision} ${r.confidence}%</b> <span style="color:var(--mut)">${(r.model||'').split('/').slice(-1)[0]}</span><br>${r.reason||''}`}
  else last.innerHTML='Chưa có tín hiệu nào được gửi cho AI – bộ lọc kỹ thuật chưa tìm thấy ứng viên đủ điều kiện.';
  if(a.last_error)last.innerHTML+=`<br><span class="dn">⚠ Lần hỏi gần nhất không model nào trả lời: ${esc(a.last_error)}</span>`;
  const provs=(a.providers||[]).map(p=>`<span class="pill">${p.name}: ${p.models.length} model</span>`).join(' ');
  const rows=(a.models||[]);
  el.innerHTML=`<div class="row"><div class="t" style="gap:6px">${provs||'<span class="muted">Chưa cấu hình nhà cung cấp AI</span>'}</div></div>`+
   (rows.length?rows.map(m=>{const rate=m.calls?Math.round(100*m.ok/m.calls):0;
    return `<div class="row"><div class="t">${m.model.split('/').slice(-1)[0]} <span class="pill" style="font-size:10px">${m.model.split('/')[0]}</span>${m.cooldown?'<span class="pill" style="color:var(--dn)">nghỉ</span>':''}</div>
    <div class="r">${m.calls} lượt<small>${m.ok} ok · ${m.fail} lỗi · ${rate}%</small></div>
    <div class="m">${m.analyze} phân tích · ${m.review} tổng kết${m.avg_ms?` · ~${(m.avg_ms/1000).toFixed(1)}s/lượt`:''} · dùng lần cuối ${ago(m.last_ts)} trước${m.last_error?`<br><span class="dn">Lỗi cuối: ${m.last_error}</span>`:''}</div></div>`}).join('')
   :'<div class="empty">Chưa gọi model nào.</div>');
}
const SRC={taken:'Đã vào lệnh',ai_override:'Vào dù AI phản đối yếu',ai_rejected:'AI từ chối',ai_error:'AI lỗi/hết lượt',not_sent:'Không gửi AI',blocked:'Luật tự học chặn',below_score:'Suýt đạt'};
const OUT={TP:'TP',SL:'SL',TIMEOUT:'Hết giờ',PENDING:'Đang theo dõi',NODATA:'Không dữ liệu'};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{cache:'no-store'});return r.json()}
let evTimer=null;
async function renderEvents(){
  $('histN').textContent='';
  const lv=$('evLv').value,q=$('evQ').value.trim();
  const rows=await api(`api/events?limit=300&level=${encodeURIComponent(lv)}&q=${encodeURIComponent(q)}`);
  const el=$('log');
  el.innerHTML=rows.length?rows.map(e=>`<div class="ev"><span style="color:var(--mut)">${t(e.ts)}</span> <span class="lv ${e.level}">${e.level}</span><span style="color:var(--mut)">[${e.src}]</span> ${esc(e.msg)}</div>`).join(''):'<div class="empty">Chưa có nhật ký.</div>';
  $('histN').textContent=rows.length+' dòng gần nhất';
}
async function renderScans(){
  const rows=await api('api/scans?limit=100');const el=$('log');
  $('histN').textContent=rows.length+' lượt';
  el.innerHTML=rows.length?rows.map(s=>`<div class="row"><div class="t">${t(s.ts)}</div><div class="r ${s.passed?'up':''}">${s.passed} đạt<small>${s.scanned} coin</small></div>
    <div class="m">${Object.entries(s.reasons||{}).map(([k,v])=>`${k} <b>${v}</b>`).join(' · ')}${(s.top||[]).length?`<br>Đạt: ${s.top.join(', ')}`:''}${(s.near||[]).length?`<br>Suýt đạt: ${s.near.join(', ')}`:''}</div></div>`).join(''):'<div class="empty">Chưa có lịch sử quét.</div>';
}
if($('evLv')){$('evLv').onchange=()=>renderEvents();let qT=null;$('evQ').oninput=()=>{clearTimeout(qT);qT=setTimeout(renderEvents,400)};}

// ---------- Tự học
function statRows(obj,label){const e=Object.entries(obj||{});if(!e.length)return '';
  return `<table class="mini"><tr><th>${label}</th><th>Mẫu</th><th>Thắng</th><th>Kỳ vọng</th></tr>${e.map(([k,v])=>`<tr><td>${esc(v.name||k)}${v.blocked?' <span class="out SL">đang chặn</span>':''}${v.weight&&v.weight!==1?` <span class="pill" style="font-size:10px">×${v.weight}</span>`:''}</td><td>${v.n}</td><td>${v.n?v.winrate+'%':'—'}</td><td class="${v.exp_r>0?'up':v.exp_r<0?'dn':''}">${v.n?(v.exp_r>=0?'+':'')+f(v.exp_r,2)+'R':'—'}</td></tr>`).join('')}</table>`}
function renderLearn(L){
  const sub=$('lnSub'),b=$('lnBody');if(!b)return;
  if(!L){sub.textContent='';b.innerHTML='<div class="empty">Đang khởi động…</div>';return}
  const st=L.stats||{},p=L.params,tot=(st.total||{}).n||0,pend=st.pending||0;
  const MODE={auto:'tự áp dụng',suggest:'chờ duyệt',off:'tắt'};
  sub.innerHTML=`${MODE[L.mode]||L.mode} · ${tot} kết quả · ${pend} đang theo dõi`;
  const tech=st.tech||{},av=st.ai_veto||{};
  const pending=(L.log||[]).filter(r=>r.status==='pending');
  const KIND={block:'Chặn',unblock:'Bỏ chặn',trigger_w:'Trọng số',min_score:'Ngưỡng điểm',exit_mode:'Cách thoát lệnh',ai_veto_conf:'Ngưỡng phủ quyết AI',tp1_r:'Mức TP1',reset:'Khôi phục'};
  let h=`<div class="note">
    ${tot<L.min_samples?`Bot đang thu thập kết quả của mọi tín hiệu (kể cả tín hiệu không vào lệnh). Cần ít nhất <b>${L.min_samples}</b> kết quả cho mỗi nhóm trước khi tự điều chỉnh – hiện có <b>${tot}</b>.<br>`:''}
    Tín hiệu kỹ thuật: <b>${tech.n||0}</b> kết quả · thắng <b>${tech.winrate||0}%</b> · kỳ vọng <b class="${(tech.exp_r||0)>=0?'up':'dn'}">${(tech.exp_r||0)>=0?'+':''}${f(tech.exp_r||0,2)}R</b>/tín hiệu
    ${av.veto_accuracy!=null?`<br>AI chặn đúng <b>${av.veto_accuracy}%</b> (${av.rejected.n} lần chặn, những lệnh đó nếu vào sẽ có kỳ vọng ${f(av.rejected.exp_r,2)}R)`:''}
    ${av.veto_conf!=null?`<br>Ngưỡng phủ quyết AI: <b>${av.veto_conf}%</b> – AI phản đối dưới mức này thì tín hiệu điểm ≥ ${(L.params||{}).ai_override_min_score??6.5} vẫn vào${av.override&&av.override.n?` (đã vào ${av.override.n} lệnh như vậy, kỳ vọng ${f(av.override.exp_r,2)}R)`:''}`:''}
  </div>
  <div class="chips" style="padding:0 20px 14px">
    <span class="pill gold">Ngưỡng điểm ${p.min_score}${p.min_score!=p.min_score_default?` (mặc định ${p.min_score_default})`:''}</span>
    <span class="pill gold">Thoát: ${(L.exit_names||{})[(st.exit_compare||{}).current||p.exit_mode||'hybrid']||'—'}</span>
    ${(p.blocks||[]).map(k=>`<span class="pill" style="color:var(--dn)">Chặn: ${esc(L.rules[k]||k)}</span>`).join('')}
    ${Object.entries(p.trigger_w||{}).map(([k,v])=>`<span class="pill">${esc(L.trigger_names[k]||k)} ×${v}</span>`).join('')}
  </div>`;
  if(pending.length) h+=`<div class="list">${pending.map(r=>`<div class="row"><div class="t"><span class="pill gold">Chờ duyệt</span> ${KIND[r.kind]||r.kind}</div>
    <div class="r" style="display:flex;gap:6px"><button class="act gold" onclick="lnAct('apply',${r.ts})">Áp dụng</button><button class="act" onclick="lnAct('reject',${r.ts})">Bỏ qua</button></div>
    <div class="m">${esc(r.reason)}</div></div>`).join('')}</div>`;
  const ec=(st.exit_compare||{}),EN=L.exit_names||{};
  if(ec.n){const cur=ec.current;h+=`<details open><summary>So sánh cách thoát lệnh · ${ec.n} tín hiệu · đang dùng: ${EN[cur]||cur}${L.exit_configured==='auto'?' (tự chọn)':''}</summary>
    <table class="mini"><tr><th>Cách thoát</th><th>Thắng</th><th>Kỳ vọng</th><th>Tổng</th><th>Tốt nhất</th></tr>${Object.entries(ec.modes||{}).map(([m,v])=>`<tr><td>${EN[m]||m}${m===cur?' <span class="pill gold" style="font-size:10px">đang dùng</span>':''}</td><td>${v.winrate}%</td><td class="${v.exp_r>0?'up':v.exp_r<0?'dn':''}">${v.exp_r>=0?'+':''}${f(v.exp_r,2)}R</td><td>${v.sum_r>=0?'+':''}${f(v.sum_r,1)}R</td><td>+${f(v.best,1)}R</td></tr>`).join('')}</table>
    <div class="note small muted" style="padding-top:8px">Mỗi tín hiệu được mô phỏng song song theo cả 3 cách trên cùng dữ liệu nến 5 phút. ${L.exit_configured==='auto'?'Bot tự chuyển sang cách tốt hơn khi đủ ≥20 tín hiệu và chênh ≥0,25R.':'Đặt EXIT_MODE=auto để bot tự chọn.'}</div></details>`}
  const tc=(st.tp1_compare||{});
  if(tc.n){const cur=+tc.current;h+=`<details open><summary>Chọn mức TP1 theo lợi nhuận · ${tc.n} tín hiệu · đang dùng ${cur}R</summary>
    <table class="mini"><tr><th>TP1</th><th>Thắng</th><th>Kỳ vọng/lệnh</th><th>Tổng</th></tr>${Object.entries(tc.levels||{}).map(([k,v])=>`<tr><td>${k}R${+k===cur?' <span class="pill gold" style="font-size:10px">đang dùng</span>':''}</td><td>${v.winrate}%</td><td class="${v.exp_r>0?'up':v.exp_r<0?'dn':''}">${v.exp_r>=0?'+':''}${f(v.exp_r,2)}R</td><td>${v.sum_r>=0?'+':''}${f(v.sum_r,1)}R</td></tr>`).join('')}</table>
    <div class="note small muted" style="padding-top:8px">TP1 thấp → thắng nhiều hơn nhưng lãi mỗi lệnh nhỏ hơn. Bot chọn mức có <b>kỳ vọng (lợi nhuận) cao nhất</b>, không chỉ tỉ lệ thắng – đổi khi đủ ≥20 tín hiệu và chênh ≥0,1R.</div></details>`}
  const d=L.daily;
  if(d) h+=`<details open><summary>AI tổng kết ngày · ${t(d.ts)}</summary><div class="note">${esc(d.summary)}
    ${(d.observations||[]).length?`<ul style="margin:8px 0 0;padding-left:18px">${d.observations.map(o=>`<li>${esc(o)}</li>`).join('')}</ul>`:''}
    ${(d.suggestions||[]).map(s=>`<div style="margin-top:10px;display:flex;gap:8px;align-items:center;justify-content:space-between"><span>Gợi ý: <b>${s.param}=${esc(s.value)}</b> – ${esc(s.why)}</span><button class="act" onclick="lnSug('${s.param}','${esc(s.value)}')">Áp dụng</button></div>`).join('')}</div></details>`;
  h+=`<details><summary>Kết quả theo nguồn tín hiệu</summary>${statRows(st.by_source,'Nguồn')||'<div class="empty">Chưa có dữ liệu</div>'}</details>
  <details><summary>Theo luật lọc (ứng viên để chặn)</summary>${statRows(st.by_rule,'Luật')||'<div class="empty">Chưa có dữ liệu</div>'}</details>
  <details><summary>Theo trigger</summary>${statRows(st.by_trigger,'Trigger')||'<div class="empty">Chưa có dữ liệu</div>'}</details>
  <details><summary>Theo hướng · khung giờ · điểm</summary>${statRows(st.by_side,'Hướng')}${statRows(st.by_session,'Khung giờ')}${statRows(st.by_score,'Điểm')}</details>
  <details><summary>Lịch sử thay đổi (${(L.log||[]).length})</summary>${(L.log||[]).length?`<div class="list">${L.log.map(r=>`<div class="row"><div class="t">${KIND[r.kind]||r.kind} <span class="pill" style="font-size:10px">${{applied:'đã áp dụng',pending:'chờ duyệt',rejected:'đã bỏ qua'}[r.status]||r.status}</span></div><div class="r"><small>${t(r.ts)}</small></div><div class="m">${esc(r.reason)}</div></div>`).join('')}</div>`:'<div class="empty">Chưa có thay đổi nào.</div>'}</details>
  <div style="display:flex;gap:8px;padding:14px 20px 18px;border-top:1px solid var(--line);flex-wrap:wrap"><button class="act" onclick="lnAct('run')">Phân tích ngay</button><button class="act warn" onclick="if(confirm('Khôi phục toàn bộ tham số về mặc định?'))lnAct('reset')">Khôi phục mặc định</button></div>`;
  b.innerHTML=h;
}
async function lnAct(a,ts){await api('api/learn/'+a,{ts});load()}
async function lnSug(param,value){await api('api/learn/suggest',{param,value});load()}

function renderLog(){
  const el=$('log');if(!el||!D)return;$('evFilters').style.display=tab==='events'?'flex':'none';let rows=[];
  if(tab==='trades'){rows=D.trades.filter(x=>x.status==='closed');$('histN').textContent=rows.length?rows.length+' lệnh':'';
    el.innerHTML=rows.length?rows.map(x=>`<div class="row"><div class="t">${sideTag(x.side)} ${x.symbol.split('/')[0]} <span style="color:var(--mut);font-weight:500;font-size:12px">${t(x.closed_at)}</span></div>
      <div class="r ${x.pnl>=0?'up':'dn'}">${x.pnl>=0?'+':''}${f(x.pnl,4)}<small>${x.pnl>=0?'+':''}${f(x.pnl_pct,1)}% ký quỹ</small></div>
      <div class="m">${x.reason_close} · vào ${px(x.entry)} → ra ${px(x.exit)} · AI ${(x.ai_model||'—').split('/').slice(-1)[0]} ${x.ai_conf||''}%</div></div>`).join(''):'<div class="empty">Chưa có lệnh nào đóng.</div>'}
  else if(tab==='signals'){rows=D.signals;$('histN').textContent='';
    el.innerHTML=rows.length?rows.map(x=>{const o=x.outcome||'';const rr=x.outcome_r;let fe={};try{fe=JSON.parse(x.features||'{}')}catch(e){}
      return `<div class="row"><div class="t">${sideTag(x.side)} ${x.symbol.split('/')[0]} <span style="color:var(--mut);font-weight:500;font-size:12px">${t(x.ts)} · điểm ${x.score}</span></div>
      <div class="r">${o?`<span class="out ${o}">${OUT[o]||o}${rr!=null&&o!=='PENDING'?' '+(rr>=0?'+':'')+f(rr,2)+'R':''}</span>`:''}<small>${SRC[x.source]||(x.taken?'Đã vào':'Bỏ qua')}</small></div>
      <div class="m">${x.ai_decision?`AI ${x.ai_decision} ${x.ai_conf??''}% · `:''}${fe.rejected?fe.rejected+' · ':''}${x.ai_reason||''}</div></div>`}).join(''):'<div class="empty">Chưa có tín hiệu nào qua bộ lọc.</div>'}
  else if(tab==='events'){renderEvents();return}
  else if(tab==='scans'){renderScans();return}
  else{rows=D.reviews||[];$('histN').textContent='';
    el.innerHTML=rows.length?rows.map(x=>`<div class="row"><div class="t">${sideTag(x.side)} ${x.symbol.split('/')[0]} <span class="pill">${x.verdict||''}</span></div>
      <div class="r ${x.pnl>=0?'up':'dn'}">${x.pnl>=0?'+':''}${f(x.pnl,4)}</div><div class="m">${x.lesson||''}${x.tags?` <span style="color:var(--mut)">#${x.tags.split(',').join(' #')}</span>`:''}</div></div>`).join(''):'<div class="empty">AI sẽ tổng kết sau mỗi lệnh đóng.</div>'}
}

document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tab').forEach(x=>x.setAttribute('aria-selected','false'));b.setAttribute('aria-selected','true');tab=b.dataset.t;renderLog()});
async function load(){try{const r=await fetch('api/state',{cache:'no-store'});if(r.status===401){location.reload();return}render(await r.json())}
  catch(e){put('sub','<span class="dot warn" aria-hidden="true"></span>Không kết nối được bot – kiểm tra bot có đang chạy không.')}}
load();setInterval(load,30000);addEventListener('resize',()=>D&&$('svg')&&drawChart(D.curve));addEventListener('themechange',()=>D&&$('svg')&&drawChart(D.curve));
</script>"""

SKEL = '<div class="skel"></div><div class="skel" style="width:70%"></div>'


def _home_body():
    tiles = ui.card("Hiệu suất & AI", '<div class="kpis" id="tiles">' + SKEL + "</div>",
                    sub="", sub_id="meta")
    equity = ui.card("Đường vốn", '<div class="chart"><svg id="svg" preserveAspectRatio="none" role="img" '
                     'aria-label="Biểu đồ vốn theo thời gian"></svg><div class="tip" id="tip"></div></div>',
                     sub_id="crange", flush=False)
    opens = ui.card("Lệnh đang mở", '<div class="list" id="open">' + SKEL + '</div>'
                    '<div id="scan" class="note" style="border-top:1px solid var(--line);padding-top:12px"></div>',
                    sub_id="openN", flush=True)
    logs = ui.card("Nhật ký",
                   '<div class="tabs" role="tablist" aria-label="Loại nhật ký">'
                   '<button class="tab" role="tab" aria-selected="true" data-t="trades">Lệnh đã đóng</button>'
                   '<button class="tab" role="tab" aria-selected="false" data-t="signals">Tín hiệu & AI</button>'
                   '<button class="tab" role="tab" aria-selected="false" data-t="reviews">Bài học AI</button>'
                   '<button class="tab" role="tab" aria-selected="false" data-t="events">Hoạt động</button>'
                   '<button class="tab" role="tab" aria-selected="false" data-t="scans">Lịch sử quét</button></div>'
                   '<div class="filters" id="evFilters" style="display:none">'
                   '<select id="evLv" aria-label="Lọc theo mức"><option value="">Tất cả mức</option><option>INFO</option>'
                   '<option>WARNING</option><option>ERROR</option></select>'
                   '<input id="evQ" type="search" aria-label="Tìm trong nhật ký" placeholder="Tìm trong nhật ký… (vd: STRK, AI từ chối)"></div>'
                   '<div class="list" id="log" aria-live="polite">' + SKEL + "</div>",
                   sub_id="histN", flush=True)
    return (ui.hero("Tổng quan", "Đang tải trạng thái bot…", eyebrow="Bảng điều khiển", lead_id="sub")
            + '<div class="strip" id="strip">' + "".join(
                f'<div class="stat"><div class="l">{x}</div><div class="v">—</div><div class="s">&nbsp;</div></div>'
                for x in ("Vốn hiện tại", "Lãi/lỗ đã chốt", "Tỉ lệ thắng", "Lệnh đang mở")) + "</div>"
            + f'<div class="grid g-wide">{equity}{opens}</div>'
            + f'<div class="mt">{tiles}</div><div class="mt">{logs}</div>')


def _ai_body():
    ai = ui.card("Bộ lọc AI", '<div id="aiLast" class="note" style="padding-top:2px">' + SKEL + '</div>'
                 '<div class="list" id="aiModels"></div>', sub_id="aiSub", flush=True)
    learn = ui.card("Tự học", '<div id="lnBody">' + SKEL + "</div>", sub_id="lnSub", flush=True)
    return (ui.hero("AI & Tự học", "Model AI nào đang trả lời, AI duyệt/chặn ra sao, và những gì bot tự rút ra "
                    "từ kết quả của mọi tín hiệu.", eyebrow="Phân tích", lead_id="sub")
            + f'<div class="grid g-wide">{learn}{ai}</div>')



_LOGIN_CSS = """
body{min-height:100vh;display:grid;place-items:center;padding:16px}
form{width:100%;max-width:380px;padding:28px 24px}
form h1{font-size:28px;margin:14px 0 6px}form p{color:var(--muted);margin:0 0 18px;font-size:13.5px}
label{display:block;font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.14em;margin:12px 0 6px}
input{width:100%;font:inherit;padding:12px 14px;border-radius:12px;border:1px solid var(--line-strong);background:var(--surface-2);color:var(--ink)}
input:focus{outline:none;border-color:var(--gold);box-shadow:0 0 0 4px var(--brand-soft)}
.err{background:var(--down-soft);color:var(--down);border-radius:12px;padding:10px 12px;font-size:13.5px;margin-bottom:6px}
form .btn{width:100%;margin-top:18px}
"""


def _login_html():
    return ('<!doctype html><html lang="vi"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Đăng nhập · OKX Altcoin Bot</title>' + ui.THEME_BOOT + ui.FONTS
            + '<style>' + ui.STYLE + _LOGIN_CSS + '</style></head><body>'
            '<form class="card" method="post" action="login"><span class="mark" aria-hidden="true">OKX</span>'
            '<h1>OKX Altcoin Bot</h1><p>Đăng nhập để xem bảng điều khiển · %VER%</p>%ERR%'
            '<label for="u">Tên đăng nhập</label><input id="u" name="u" autocomplete="username" required>'
            '<label for="p">Mật khẩu</label><input id="p" name="p" type="password" autocomplete="current-password" required>'
            '<button class="btn btn-primary">Đăng nhập</button></form></body></html>')


def _settings_body(bot, msg="", ok=True):
    sw = bot.switches.all() if getattr(bot, "switches", None) else {}
    tg_on = bool(getattr(bot, "tg", None) and bot.tg.enabled)
    z = getattr(bot, "zoom", None)
    z_on = bool(z and (z.chatbot_on or z.webhook_on))
    configured = {"telegram_notify": tg_on, "telegram_cmd": tg_on, "zoom_notify": z_on,
                  "zoom_cmd": bool(z and z.chatbot_on)}
    rows = []
    for key, (chan, item, desc) in SWITCH_NAMES.items():
        badge = "" if configured.get(key) else ' <span class="badge b-none">chưa cấu hình</span>'
        rows.append(f'<div class="setting"><div><div class="n">{ui.e(chan)} · {ui.e(item)}{badge}</div>'
                    f'<div class="d">{ui.e(desc)}</div></div>'
                    f'{ui.switch_form(key, sw.get(key, True), f"{chan} – {item}")}</div>')
    channels = ui.card("Thông báo & tin nhắn", "".join(rows), flush=True,
                       sub="Tắt để tạm im một kênh; cấu hình (token, khoá app) vẫn giữ nguyên. Lưu cả khi khởi động lại.")

    # Zoom
    zb = []
    if not z or not (z.chatbot_on or z.webhook_on):
        zb.append('<p class="muted" style="margin:0">Zoom chưa được cấu hình (thiếu ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET / '
                  'ZOOM_BOT_JID). Xem trang <a href="zoom">Cấu hình Zoom</a>.</p>')
    else:
        p = z.pairing() if z.chatbot_on else {}
        good = bot.store.get("zoom_notify_ok") or {}
        last_t = bot.store.get("zoom_notify_target") or {}
        items = [
            ("Kiểu kết nối", "Incoming Webhook" if z.webhook_on else "Chatbot"),
            ("Đã ghép đôi", ui.e((p.get("name") or "") + " " + (p.get("jid") or "")).strip() or "chưa"),
            ("Địa chỉ trả lời gần nhất", ui.e(last_t.get("to_jid") or "chưa có – nhắn bot 1 lệnh trên Zoom")),
            ("Cách gửi thông báo đang dùng",
             f'<span class="badge b-ok">{ui.e(good.get("name"))}</span>' if good.get("name")
             else '<span class="badge b-warn">chưa gửi thành công lần nào</span>'),
        ]
        ev_robot = bot.store.get("zoom_robot_jid") or ""
        if ev_robot:
            match = ev_robot == cfg.ZOOM_BOT_JID
            items.append(("Bot JID Zoom gửi tới", ui.e(ev_robot) + (' <span class="badge b-ok">khớp ZOOM_BOT_JID</span>' if match
                          else ' <span class="badge b-err">khác ZOOM_BOT_JID</span>')))
            if not match:
                items.append(("ZOOM_BOT_JID trên panel", ui.e(cfg.ZOOM_BOT_JID or "—")))
        else:
            items.append(("Bot JID Zoom gửi tới", "chưa biết – nhắn bot 1 lệnh trên Zoom"))
        ev_acc = bot.store.get("zoom_account_id") or ""
        if ev_acc:
            ok_acc = not cfg.ZOOM_ACCOUNT_ID or ev_acc == cfg.ZOOM_ACCOUNT_ID
            items.append(("account_id Zoom gửi tới", ui.e(ev_acc) + (
                ' <span class="badge b-ok">đang dùng</span>' if ok_acc
                else ' <span class="badge b-err">khác ZOOM_ACCOUNT_ID</span>')))
            if not ok_acc:
                items.append(("ZOOM_ACCOUNT_ID trên panel", ui.e(cfg.ZOOM_ACCOUNT_ID) + " (nên xoá biến này)"))
        else:
            items.append(("account_id Zoom gửi tới", "chưa biết – nhắn bot 1 lệnh trên Zoom"))
        if cfg.ZOOM_TO_JID:
            items.append(("ZOOM_TO_JID", ui.e(cfg.ZOOM_TO_JID)))
        zb.append('<table class="mini" style="margin:0 -20px;width:calc(100% + 40px)"><tbody>' + "".join(
            f"<tr><td>{k}</td><td style='text-align:right;white-space:normal'>{v}</td></tr>" for k, v in items)
            + "</tbody></table>")
        zb.append('<form method="post" action="caidat/zoomtest" style="margin-top:16px;display:flex;gap:10px;flex-wrap:wrap">'
                  '<button class="btn btn-primary" type="submit">Gửi thử thông báo Zoom</button>'
                  '<a class="btn btn-ghost" href="zoom">Cấu hình Zoom chi tiết</a></form>'
                  '<p class="small muted" style="margin:10px 0 0">Bot thử lần lượt các cách gửi (địa chỉ lần trả lời thành công, '
                  'JID đã ghép đôi với account_id Zoom gửi kèm tin nhắn…) và nhớ cách nào Zoom nhận. Kết quả hiện ở đầu trang.</p>')
    zoom = ui.card("Zoom", "".join(zb), sub="Kiểm tra và gửi thử thông báo")

    # thông số đang dùng
    from . import strategy, exits
    L = strategy.LEARNED
    params = [
        ("Điểm tối thiểu", cfg.MIN_SCORE, L.get("min_score")),
        ("TP1 (R)", cfg.TP1_R, L.get("tp1_r")),
        ("Ngưỡng AI được chặn (%)", "65 (ảo) / 0 (thật)", L.get("ai_veto_conf")),
        ("SL tối thiểu / tối đa (ATR)", f"{cfg.ATR_SL_MULT:g} / {cfg.MAX_SL_ATR:g}", None),
        ("Rủi ro mỗi lệnh", f"{cfg.RISK_PER_TRADE * 100:.0f}%", None),
        ("Đòn bẩy · lệnh tối đa", f"x{cfg.LEVERAGE} · {cfg.MAX_OPEN_POSITIONS}", None),
        ("Số coin quét", cfg.TOP_N_COINS, None),
        ("AI duyệt khi chất lượng ≥", cfg.AI_MIN_CONFIDENCE, None),
        ("Luật đang chặn", ", ".join(sorted(strategy.active_blocks())) or "—", None),
    ]
    prow = [f"<tr><td>{ui.e(n)}</td><td class='num'>{ui.e(str(d))}</td>"
            f"<td class='num'>{'<b>' + ui.e(str(v)) + '</b>' if v not in (None, '') else '<span class=muted>—</span>'}</td></tr>"
            for n, d, v in params]
    params_card = ui.card("Thông số đang dùng", ui.table([("Thông số", ""), ("Mặc định", "num"), ("Tự học đang áp dụng", "num")],
                                                         prow, "—"),
                          sub="Cột “Tự học” (nếu có) là giá trị bot đang dùng thật", flush=True)

    dls = [("trades", "Lệnh giao dịch"), ("signals", "Tín hiệu + kết quả"), ("scans", "Lịch sử quét"),
           ("events", "Nhật ký hoạt động"), ("equity", "Đường vốn"), ("reviews", "Bài học AI"),
           ("learn_log", "Lịch sử tự học")]
    dl = '<div class="dls">' + "".join(f'<a class="dl" href="api/export/{k}.csv">{ui.e(n)}<small>{k}.csv</small></a>'
                                      for k, n in dls) +          '<a class="dl" href="api/export/all.json">Sao lưu toàn bộ<small>all.json</small></a></div>'
    data = ui.card("Tải dữ liệu", dl, sub="CSV mở bằng Excel / Google Sheets")
    flash = ui.alert(msg, "ok" if ok else "err") if msg else ""
    return (ui.hero("Cài đặt", "Bật/tắt thông báo và trả lời lệnh từng kênh, kiểm tra Zoom, xem thông số và tải dữ liệu.",
                    eyebrow="Quản lý")
            + flash + f'<div class="grid g-wide">{channels}{zoom}</div>'
            + f'<div class="grid g-wide mt">{params_card}{data}</div>')


def _version_body(bot):
    import platform
    ok = ver.is_release()
    check = ('<span class="badge b-ok">đúng bản phát hành</span>' if ok
             else '<span class="badge b-err">khác bản phát hành</span>')
    stats = ui.strip([
        ("Phiên bản", f"v{ui.e(ver.VERSION)}", f"phát hành {ui.e(ver.ddmmyyyy(ver.RELEASED))}"),
        ("Bản dựng", f'<code style="font-size:20px">{ui.e(ver.build())}</code>', check),
        ("Đang chạy từ", ui.e(ver.started_text()), ui.e(ver.uptime())),
        ("Python", ui.e(platform.python_version()), ui.e(platform.system())),
    ])
    warn = "" if ok else ui.alert(
        f"Code đang chạy (bản dựng {ver.build()}) khác bản phát hành v{ver.VERSION} ({ver.RELEASE_BUILD}): có thể upload "
        "thiếu file, lẫn file của bản cũ, hoặc code đã bị sửa. Hãy tải lại đủ gói zip rồi khởi động lại.", "err")
    others = [i for i in (bot.store.instances() if getattr(bot, "store", None) else [])
              if i.get("instance") != getattr(bot, "instance", None)]
    inst = ""
    if others:
        inst = ui.card("Máy chạy song song", "".join(
            f'<div class="setting"><div class="n">{ui.e(i["instance"])}</div><div class="d">{ui.e(i.get("info") or "")}</div></div>'
            for i in others), flush=True)
    history = "".join(
        f'<h3>v{ui.e(v)} · {ui.e(ver.ddmmyyyy(day))}'
        + (' <span class="badge b-gold" style="letter-spacing:.04em;text-transform:none">đang chạy</span>' if v == ver.VERSION else "")
        + '</h3><ul class="notes">' + "".join(f"<li>{ui.e(n)}</li>" for n in notes) + "</ul>"
        for v, day, notes in ver.NOTES)
    how = ('<ol class="steps"><li>Tải gói zip của bản mới lên Botkeep, ghi đè file cũ; <b>giữ nguyên thư mục '
           '<code>data/</code></b> để không mất dữ liệu.</li><li>Khởi động lại bot.</li>'
           '<li>Kiểm tra: nhãn phiên bản trên đầu trang và lệnh <code>phienban</code> trong Telegram/Zoom hiện số mới; '
           'bot tự nhắn “Bot đã cập nhật”.</li><li>Bản dựng phải báo <b>đúng bản phát hành</b>; nếu báo khác là thiếu file '
           'hoặc lẫn file cũ.</li></ol>')
    return (ui.hero("Phiên bản", "Bản đang chạy, mã bản dựng để kiểm tra đã tải lên đúng và đủ, và những gì thay đổi "
                    "qua từng phiên bản.", eyebrow="Quản lý")
            + stats + warn
            + '<div class="grid g-wide">' + ui.card("Lịch sử phiên bản", history, sub="mới nhất ở trên")
            + '<div style="display:grid;gap:18px">' + ui.card("Cập nhật bot", how) + inst + "</div></div>")


def build_app(bot):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)   # /docs dành cho trang hướng dẫn Zoom

    def _token():
        return hashlib.sha256(f"{cfg.DASHBOARD_USER}:{cfg.DASHBOARD_PASS}:okx-bot".encode()).hexdigest()[:32]

    def _authed(request):
        if not (cfg.DASHBOARD_USER and cfg.DASHBOARD_PASS):
            return True
        return request.cookies.get("okxbot") == _token()

    LOGIN = _login_html()

    @app.post("/login")
    async def login(request: Request):
        from urllib.parse import parse_qs
        body = (await request.body()).decode("utf-8", "ignore")
        form = {k: v[0] for k, v in parse_qs(body).items()}
        if form.get("u") == cfg.DASHBOARD_USER and form.get("p") == cfg.DASHBOARD_PASS:
            resp = RedirectResponse(url="./", status_code=303)
            resp.set_cookie("okxbot", _token(), max_age=30 * 86400, httponly=True, samesite="lax")
            return resp
        return _login_page('<div class="err" role="alert">Sai tên đăng nhập hoặc mật khẩu.</div>')

    @app.get("/logout")
    def logout():
        resp = RedirectResponse(url="./", status_code=303)
        resp.delete_cookie("okxbot")
        return resp

    def _login_page(err=""):
        return HTMLResponse(LOGIN.replace("%VER%", "v" + VERSION).replace("%ERR%", err), status_code=200)

    def _mode():
        return cfg.MODE

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        if not _authed(request):
            return _login_page()
        return HTMLResponse(ui.page("Tổng quan", _home_body(), active="home", mode=_mode(), scripts=APP_JS))

    @app.get("/ai", response_class=HTMLResponse)
    def ai_page(request: Request):
        if not _authed(request):
            return _login_page()
        return HTMLResponse(ui.page("AI & Tự học", _ai_body(), active="ai", mode=_mode(), scripts=APP_JS))

    @app.get("/caidat", response_class=HTMLResponse)
    def settings_page(request: Request, msg: str = "", ok: int = 1):
        if not _authed(request):
            return _login_page()
        return HTMLResponse(ui.page("Cài đặt", _settings_body(bot, msg[:300], bool(ok)), active="settings", mode=_mode()))

    @app.post("/caidat/kenh/{key}")
    async def settings_switch(request: Request, key: str):
        if not _authed(request):
            return Response(status_code=401)
        from urllib.parse import parse_qs, quote
        body = (await request.body()).decode("utf-8", "ignore")
        on = (parse_qs(body).get("on") or ["1"])[0] == "1"
        if key not in SWITCH_NAMES or not getattr(bot, "switches", None):
            return RedirectResponse(url="../../caidat?ok=0&msg=" + quote("Công tắc không hợp lệ"), status_code=303)
        bot.switches.set(key, on)
        chan, item, _ = SWITCH_NAMES[key]
        log.info("Web: %s %s – %s", "BẬT" if on else "TẮT", chan, item)
        return RedirectResponse(url="../../caidat?msg=" + quote(f"Đã {'bật' if on else 'tắt'} {chan} – {item}."),
                                status_code=303)

    @app.post("/caidat/zoomtest")
    def settings_zoomtest(request: Request):
        if not _authed(request):
            return Response(status_code=401)
        from urllib.parse import quote
        z = getattr(bot, "zoom", None)
        if z is None:
            ok, tried = False, ["bot đang khởi động"]
        else:
            ok, tried = z.test_send()
        msg = ("Zoom đã nhận thông báo thử. " if ok else "Zoom chưa nhận thông báo thử. ") + "Đã thử: " + "; ".join(tried)
        return RedirectResponse(url="../caidat?ok=" + ("1" if ok else "0") + "&msg=" + quote(msg[:300]), status_code=303)

    @app.get("/phienban", response_class=HTMLResponse)
    def version_page(request: Request):
        if not _authed(request):
            return _login_page()
        return HTMLResponse(ui.page("Phiên bản", _version_body(bot), active="version", mode=_mode()))

    @app.get("/api/state")
    def state(request: Request):
        if not _authed(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse(bot.snapshot())

    def _guard(request):
        return None if _authed(request) else JSONResponse({"error": "unauthorized"}, status_code=401)

    @app.get("/api/events")
    def events(request: Request, limit: int = 300, level: str = "", q: str = ""):
        return _guard(request) or JSONResponse(bot.store.events(min(limit, 2000), level or None, q or None))

    @app.get("/api/scans")
    def scans(request: Request, limit: int = 100):
        return _guard(request) or JSONResponse(bot.store.scans(min(limit, 1000)))

    @app.get("/api/export/{fname}")
    def export(request: Request, fname: str):
        g = _guard(request)
        if g:
            return g
        name, _, fmt = fname.partition(".")
        stamp = time.strftime("%Y%m%d-%H%M")
        if name == "all":
            data = {k: bot.store.export(k) for k in bot.store.EXPORTS}
            data["learned"] = bot.store.get("learned")
            return Response(json.dumps(data, ensure_ascii=False, default=str), media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="okxbot-all-{stamp}.json"'})
        if name not in bot.store.EXPORTS:
            return JSONResponse({"error": "không có bảng này"}, status_code=404)
        rows = bot.store.export(name)
        if fmt == "json":
            return Response(json.dumps(rows, ensure_ascii=False, default=str), media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="{name}-{stamp}.json"'})
        buf = io.StringIO()
        cols = list(rows[0].keys()) if rows else []
        if cols:
            for c in ("ts", "opened_at", "closed_at", "outcome_ts"):       # thêm cột giờ dễ đọc
                if c in cols:
                    cols.insert(cols.index(c) + 1, c + "_time")
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            r = dict(r)
            for c in ("ts", "opened_at", "closed_at", "outcome_ts"):
                if c in r:
                    r[c + "_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r[c])) if r[c] else ""
            w.writerow(r)
        return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}-{stamp}.csv"'})

    @app.post("/api/learn/{action}")
    async def learn_action(request: Request, action: str):
        g = _guard(request)
        if g:
            return g
        if not bot.learner:
            return JSONResponse({"error": "bot đang khởi động"}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            body = {}
        L = bot.learner
        if action == "apply":
            n = L.apply_pending(body.get("ts"))
        elif action == "reject":
            n = L.reject_pending(body.get("ts"))
        elif action == "reset":
            L.reset(); n = 1
        elif action == "run":
            L.resolve_pending(); n = len(L.learn() if L.mode != "off" else L.compute_stats() and [])
        elif action == "suggest":
            n = int(L.apply_suggestion(body.get("param"), body.get("value")))
        else:
            return JSONResponse({"error": "hành động không hợp lệ"}, status_code=400)
        return JSONResponse({"ok": True, "n": n})

    async def _zoom(request: Request):
        """Endpoint cho Zoom chatbot (Bot endpoint URL + Event notification endpoint)."""
        z = getattr(bot, "zoom", None)
        raw = await request.body()
        try:
            data = json.loads(raw or b"{}")
        except Exception:
            log.warning("Zoom: body không phải JSON (%d byte)", len(raw))
            return Response(status_code=400)
        if not isinstance(data, dict):
            return Response(status_code=400)
        if z is None or not z.chatbot_on:
            return Response(status_code=404)
        ev_name = data.get("event")
        if ev_name == "endpoint.url_validation":
            plain = str((data.get("payload") or {}).get("plainToken", ""))
            if not plain or not cfg.ZOOM_SECRET_TOKEN:
                z.record(ev_name, False, "thiếu plainToken hoặc ZOOM_SECRET_TOKEN")
                return Response(status_code=400)
            z.record(ev_name, True, "đã trả lời Validate")
            log.info("Zoom: Validate endpoint OK")
            return JSONResponse(z.url_validation(plain))
        ok, why = z.verify_detail(raw, request.headers)
        z.record(ev_name, ok, why, data)
        log.info("Zoom request: %s – %s", ev_name, why)
        if not ok:
            return Response(status_code=403)
        try:
            if data.get("event") in ("app_deauthorized", "bot_installed"):
                z.lifecycle(data.get("event"), data.get("payload") or {})
                return Response(status_code=200)
            z.handle(data)
        except Exception as e:
            log.error("Zoom xử lý sự kiện lỗi: %s", e)
        return Response(status_code=200)

    app.add_api_route("/zoom/bot", _zoom, methods=["POST"])
    app.add_api_route("/webhook/zoom", _zoom, methods=["POST"])   # cùng đường dẫn với repo lananh

    from . import pages
    pages.register(app, bot, _authed)

    @app.get("/health")
    def health():
        info = ver.info()
        info.pop("notes", None)
        return {"ok": True, **info, "ts": time.time(), "instance": bot.instance, "leader": bot.is_leader}

    return app


def start_dashboard(bot):
    app = build_app(bot)
    port = cfg.DASHBOARD_PORT
    src = next((k for k in ("PORT", "SERVER_PORT", "APP_PORT", "DASHBOARD_PORT") if os.getenv(k)), "mặc định")
    log.info("Dashboard: cổng %d (từ biến %s) | đăng nhập: %s", port, src,
             "bật" if cfg.DASHBOARD_USER and cfg.DASHBOARD_PASS else "tắt")

    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")   # IPv4 – cổng public của Botkeep/Render forward IPv4
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))

    def _run():
        try:
            server.run()
        except SystemExit:
            log.error("Dashboard KHÔNG mở được cổng %d (cổng bận hoặc không được phép)", port)
        except Exception as e:
            log.error("Dashboard lỗi: %s", e)

    def _selfcheck():
        """Sau 3s tự kết nối vào cổng của chính mình để chắc chắn web đang nghe."""
        time.sleep(3)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3):
                pass
            log.info("Dashboard sẵn sàng: đang nghe %s:%d (tự kiểm tra OK)", host, port)
        except Exception as e:
            log.error("Dashboard tự kiểm tra THẤT BẠI trên 127.0.0.1:%d: %s", port, e)

    threading.Thread(target=_selfcheck, daemon=True, name="dashboard-check").start()
    threading.Thread(target=_run, daemon=True, name="dashboard").start()
