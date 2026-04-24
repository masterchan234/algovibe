/* AlgoVibe Dashboard — script.js */
'use strict';

let chart, activePair = null, lastStats = {}, lastPrices = {}, lastRadarData = [], _notifEnabled = false;
let _logCache = new Set();

// ── AlgoChart ──────────────────────────────────────────────
class AlgoChart {
    constructor(cId, tId) {
        this.canvas = document.getElementById(cId);
        this.ctx    = this.canvas.getContext('2d');
        this.tip    = document.getElementById(tId);
        this.data   = []; this._ohlc = [];
        this.mode   = 'area';
        this.hI = -1; this.hC = -1;
        new ResizeObserver(() => this.resize()).observe(this.canvas.parentElement);
        this.canvas.addEventListener('mousemove', e => this._move(e));
        this.canvas.addEventListener('mouseleave', () => { this.hI = this.hC = -1; this.tip.style.display='none'; this.draw(); });
    }
    resize() {
        const r = this.canvas.parentElement.getBoundingClientRect(), dpr = devicePixelRatio||1;
        this.canvas.width = r.width*dpr; this.canvas.height = r.height*dpr;
        this.ctx.scale(dpr,dpr);
        this.canvas.style.width = r.width+'px'; this.canvas.style.height = r.height+'px';
        this.draw();
    }
    setData(d) {
        this.data = d; this._ohlc = this._ohlcBuild(d.map(x=>x.value));
        document.getElementById('chart-empty').style.display = d.length?'none':'flex';
        this.draw();
    }
    setMode(m) {
        this.mode = m; this.hI = this.hC = -1; this.tip.style.display='none';
        document.querySelectorAll('.chart-toggle').forEach(b=>b.classList.remove('active'));
        document.getElementById('chart-'+m+'-btn')?.classList.add('active');
        this.draw();
    }
    _ohlcBuild(prices, g=6) {
        const out=[];
        for(let i=0;i<prices.length;i+=g){const s=prices.slice(i,i+g);if(!s.length)break;out.push({open:s[0],close:s[s.length-1],high:Math.max(...s),low:Math.min(...s)});}
        return out;
    }
    _sma(arr,n){return arr.map((_,i)=>{if(i<n-1)return null;const s=arr.slice(i-n+1,i+1);return s.reduce((a,b)=>a+b,0)/n;});}
    _fmt(v){return v>=1000?v.toLocaleString('id-ID'):v.toFixed(6);}
    _tipPos(x,W){this.tip.style.display='block';const tw=this.tip.getBoundingClientRect().width;this.tip.style.left=(x+12+tw>W?x-tw-12:x+12)+'px';this.tip.style.top='8px';}
    _move(e) {
        if(!this.data.length)return;
        const r=this.canvas.getBoundingClientRect(), x=e.clientX-r.left, W=r.width;
        if(this.mode==='candle'){
            const o=this._ohlc; if(!o.length)return;
            let i=Math.max(0,Math.min(Math.floor(x/(W/o.length)),o.length-1));
            if(this.hC!==i){this.hC=i;this.draw();const c=o[i];
                this.tip.innerHTML=`<span style="color:#8b8fa8">O</span> ${this._fmt(c.open)} <span style="color:#10b981">H</span> ${this._fmt(c.high)}<br><span style="color:#ef4444">L</span> ${this._fmt(c.low)} <span style="color:#e8e9f0">C</span> ${this._fmt(c.close)}`;
                this._tipPos(x,W);}
        } else {
            const step=W/Math.max(1,this.data.length-1);
            let i=Math.max(0,Math.min(Math.round(x/step),this.data.length-1));
            if(this.hI!==i){this.hI=i;this.draw();this.tip.textContent='Rp '+this._fmt(this.data[i].value);this._tipPos(x,W);}
        }
    }
    draw(){if(this.mode==='candle')this._drawCandle();else this._drawArea();}
    _drawArea() {
        const r=this.canvas.parentElement.getBoundingClientRect(), W=r.width, H=r.height, ctx=this.ctx;
        ctx.clearRect(0,0,W,H); if(!this.data.length)return;
        const vals=this.data.map(d=>d.value), minV=Math.min(...vals), maxV=Math.max(...vals), range=maxV-minV||1;
        const pad=H*.1, dH=H-pad*2, stepX=W/Math.max(1,this.data.length-1);
        const pts=vals.map((v,i)=>({x:i*stepX,y:H-pad-((v-minV)/range)*dH}));
        ctx.beginPath(); ctx.moveTo(pts[0].x,pts[0].y); pts.forEach(p=>ctx.lineTo(p.x,p.y));
        ctx.lineTo(W,H); ctx.lineTo(0,H); ctx.closePath();
        const g=ctx.createLinearGradient(0,0,0,H); g.addColorStop(0,'rgba(59,130,246,.2)'); g.addColorStop(1,'rgba(59,130,246,0)');
        ctx.fillStyle=g; ctx.fill();
        ctx.beginPath(); pts.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));
        ctx.strokeStyle='#3b82f6'; ctx.lineWidth=1.5; ctx.lineJoin='round'; ctx.stroke();
        const sma=this._sma(vals,10); let first=true;
        ctx.beginPath();
        sma.forEach((s,i)=>{if(s===null)return;const x=i*stepX,y=H-pad-((s-minV)/range)*dH;first?(ctx.moveTo(x,y),first=false):ctx.lineTo(x,y);});
        ctx.strokeStyle='#f59e0b'; ctx.lineWidth=1; ctx.setLineDash([4,3]); ctx.stroke(); ctx.setLineDash([]);
        if(this.hI>=0&&this.hI<pts.length){const p=pts[this.hI];
            ctx.beginPath(); ctx.setLineDash([3,4]); ctx.moveTo(p.x,0); ctx.lineTo(p.x,H);
            ctx.strokeStyle='rgba(255,255,255,.1)'; ctx.lineWidth=1; ctx.stroke(); ctx.setLineDash([]);
            ctx.beginPath(); ctx.arc(p.x,p.y,4.5,0,Math.PI*2); ctx.fillStyle='#08090b'; ctx.fill();
            ctx.strokeStyle='#3b82f6'; ctx.lineWidth=2; ctx.stroke();}
    }
    _drawCandle() {
        const r=this.canvas.parentElement.getBoundingClientRect(), W=r.width, H=r.height, ctx=this.ctx;
        ctx.clearRect(0,0,W,H); const ohlc=this._ohlc; if(!ohlc.length)return;
        const allP=ohlc.flatMap(c=>[c.high,c.low]), minV=Math.min(...allP), maxV=Math.max(...allP), range=maxV-minV||1;
        const pad=H*.1, dH=H-pad*2, toY=v=>H-pad-((v-minV)/range)*dH;
        const slotW=W/ohlc.length, bW=Math.max(2,slotW*.55);
        if(this.hC>=0){ctx.fillStyle='rgba(255,255,255,.03)';ctx.fillRect(this.hC*slotW,0,slotW,H);}
        ohlc.forEach((c,i)=>{
            const cx=(i+.5)*slotW, bull=c.close>=c.open, col=bull?'#10b981':'#ef4444';
            ctx.strokeStyle=col; ctx.lineWidth=1;
            ctx.beginPath(); ctx.moveTo(cx,toY(c.high)); ctx.lineTo(cx,toY(c.low)); ctx.stroke();
            const bT=toY(Math.max(c.open,c.close)), bH=Math.max(1.5,toY(Math.min(c.open,c.close))-bT);
            ctx.fillStyle=bull?'rgba(16,185,129,.75)':'rgba(239,68,68,.75)';
            ctx.fillRect(cx-bW/2,bT,bW,bH); ctx.strokeStyle=col; ctx.lineWidth=.8; ctx.strokeRect(cx-bW/2,bT,bW,bH);
        });
        const closes=ohlc.map(c=>c.close), sma=this._sma(closes,Math.min(10,Math.floor(ohlc.length/2)));
        let first=true; ctx.beginPath();
        sma.forEach((s,i)=>{if(s===null)return;const x=(i+.5)*slotW,y=toY(s);first?(ctx.moveTo(x,y),first=false):ctx.lineTo(x,y);});
        ctx.strokeStyle='#f59e0b'; ctx.lineWidth=1.5; ctx.setLineDash([4,3]); ctx.stroke(); ctx.setLineDash([]);
    }
}

// ── Helpers ────────────────────────────────────────────────
function fmtIDR(n,signed=false){const abs=Math.abs(n),s=new Intl.NumberFormat('id-ID',{style:'currency',currency:'IDR',maximumFractionDigits:0}).format(abs);return signed?(n>=0?'+':'\u2212')+s:n<0?'\u2212'+s:s;}
function fmtDur(sec){if(!sec||sec<0)return'\u2014';const h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60),s=sec%60;return h>0?`${h}h ${m}m`:`${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;}
function flashEl(el,dir){if(!el)return;el.classList.remove('flash-up','flash-down');void el.offsetWidth;el.classList.add(dir==='up'?'flash-up':'flash-down');}
function setRegimeBadge(r){const b=document.getElementById('market-regime-badge');if(!b)return;const m={'BULLISH':'regime-bullish','BEARISH':'regime-bearish','CHOPPY':'regime-choppy','RANGING':'regime-ranging'};b.className='regime-badge '+(m[(r||'').toUpperCase()]||'regime-choppy');b.textContent=(r||'ANALYZING').toUpperCase();}

// ── Toast ──────────────────────────────────────────────────
let _toastEl;
function _getToastEl(){if(!_toastEl){_toastEl=document.createElement('div');_toastEl.className='toast-container';document.body.appendChild(_toastEl);}return _toastEl;}
function showToast(msg,type='info'){const c=_getToastEl(),t=document.createElement('div');t.className=`toast toast-${type}`;t.textContent=msg;c.prepend(t);setTimeout(()=>t.remove(),5000);}

// ── Notifications ──────────────────────────────────────────
async function requestNotificationPermission(){
    if(!('Notification'in window))return;
    const p=await Notification.requestPermission();
    _notifEnabled=p==='granted';
    const btn=document.getElementById('notif-btn');
    if(btn){btn.textContent=_notifEnabled?'🔔':'🔕';btn.classList.toggle('active',_notifEnabled);}
}
function fireNotif(title,body){
    if(_notifEnabled&&Notification.permission==='granted')try{new Notification(title,{body,icon:'/favicon.ico',tag:title,renotify:true});}catch(e){}
}
// Init notif button state
window.addEventListener('DOMContentLoaded',()=>{
    if('Notification'in window&&Notification.permission==='granted'){_notifEnabled=true;const b=document.getElementById('notif-btn');if(b){b.textContent='🔔';b.classList.add('active');}}
});

// ── Chart Mode Toggle ──────────────────────────────────────
function setChartMode(m){if(chart)chart.setMode(m);}

// ── Init ───────────────────────────────────────────────────
function initSystem(){chart=new AlgoChart('custom-chart','chart-tooltip');}

// ── Bot Status ─────────────────────────────────────────────
async function updateBotStatus(){
    try{const d=await(await fetch('/api/bot-status')).json();
        const badge=document.getElementById('bot-status-badge'),text=document.getElementById('bot-status-text'),dry=document.getElementById('dry-run-badge');
        if(dry)dry.style.display=d.dry_run?'inline-flex':'none';
        if(badge&&text){badge.className='bot-status-badge '+(d.is_running?'status-live':'status-offline');text.textContent=d.is_running?'LIVE':'OFFLINE';}
    }catch(e){const b=document.getElementById('bot-status-badge');if(b)b.className='bot-status-badge status-offline';}
}

// ── Stats ──────────────────────────────────────────────────
async function updateStats(){
    try{const data=await(await fetch('/api/stats')).json();
        const pnlEl=document.getElementById('daily-pnl');
        const np=fmtIDR(data.daily_pnl,true);
        if(pnlEl.textContent!==np){pnlEl.textContent=np;pnlEl.className='value '+(data.daily_pnl>=0?'up':'down');flashEl(pnlEl,data.daily_pnl>=0?'up':'down');}
        const wr=document.getElementById('win-rate');wr.textContent=data.win_rate.toFixed(1)+'%';wr.className='value '+(data.win_rate>=50?'up':'down');
        document.getElementById('daily-wl').innerHTML=`<span class="up">${data.daily_wins||0}</span>/<span class="down">${data.daily_losses||0}</span>`;
        const unr=document.getElementById('unrealized-pnl');const uv=data.unrealized_pnl||0;unr.textContent=fmtIDR(uv,true);unr.className='value '+(uv>=0?'up':'down');
        document.getElementById('total-trades').textContent=data.total_trades_all_time||0;
        setRegimeBadge(data.regime);
        if(data.sectors&&Object.keys(data.sectors).length)renderSectors(data.sectors);
        if(data.latest_whale&&data.latest_whale!==lastStats.latest_whale){showToast('🐋 WHALE: '+data.latest_whale,'whale');fireNotif('🐋 Whale Detected',data.latest_whale);prependLog({side:'🐋 WHALE',pair:'ALERT',price:data.latest_whale,status:'DETECTED',level:'alert',created_at:new Date().toISOString()});}
        if(data.latest_fake_pump&&data.latest_fake_pump!==lastStats.latest_fake_pump){showToast('⚠️ FAKE PUMP: '+data.latest_fake_pump,'pump');fireNotif('⚠️ Fake Pump Alert',data.latest_fake_pump);prependLog({side:'⚠️ FAKE PUMP',pair:'ALERT',price:data.latest_fake_pump,status:'DETECTED',level:'warn',created_at:new Date().toISOString()});}
        lastStats=data;
    }catch(e){console.warn('stats error',e);}
}

// ── Sectors ────────────────────────────────────────────────
function renderSectors(sectors){
    const c=document.getElementById('sector-list');c.innerHTML='';
    const entries=Object.entries(sectors);if(!entries.length){c.innerHTML='<div class="empty-state">Belum ada data</div>';return;}
    const maxS=Math.max(...entries.map(([,v])=>Math.abs(v)),1);
    entries.sort((a,b)=>b[1]-a[1]).forEach(([sec,score])=>{
        const pct=Math.min(100,Math.abs(score)/maxS*100),col=score>=0?'#10b981':'#ef4444',lbl=(score>=0?'+':'')+score.toFixed(1);
        const div=document.createElement('div');div.className='sector-item';
        div.innerHTML=`<div class="sector-header"><span class="sector-name">${sec}</span><span class="sector-score" style="color:${col}">${lbl}</span></div><div class="sector-track"><div class="sector-fill" style="width:${pct}%;background:${col}"></div></div>`;
        c.appendChild(div);
    });
}

// ── Positions ──────────────────────────────────────────────
async function updatePositions(){
    try{const data=await(await fetch('/api/positions')).json();
        const list=document.getElementById('positions-list'),cnt=document.getElementById('positions-count');
        cnt.textContent=data.length;
        if(!data.length){list.innerHTML='<div class="empty-state">Tidak ada posisi aktif</div>';return;}
        list.innerHTML='';
        data.forEach(pos=>{
            const isP=pos.pnl_idr>=0,pct=(isP?'+':'')+pos.pnl_pct.toFixed(2)+'%',pnl=fmtIDR(pos.pnl_idr,true);
            const ep=pos.entry_price>=1000?pos.entry_price.toLocaleString('id-ID'):pos.entry_price.toFixed(6);
            const lp=pos.last_price>=1000?pos.last_price.toLocaleString('id-ID'):pos.last_price.toFixed(6);
            const card=document.createElement('div');card.className='position-card '+(isP?'pnl-pos':'pnl-neg');
            card.innerHTML=`<div class="pos-header"><span class="pos-pair">${pos.pair.toUpperCase()}</span><span class="pos-pnl-pct ${isP?'up':'down'}">${pct}</span></div><div class="pos-row"><span class="pos-label">Entry</span><span class="pos-val">Rp ${ep}</span></div><div class="pos-row"><span class="pos-label">Last</span><span class="pos-val">Rp ${lp}</span></div><div class="pos-row"><span class="pos-label">Durasi</span><span class="pos-duration">${fmtDur(pos.duration_sec)}</span></div><div class="pos-pnl-idr ${isP?'up':'down'}">${pnl}</div>`;
            list.appendChild(card);
        });
    }catch(e){console.warn('positions error',e);}
}

// ── Watchlist ──────────────────────────────────────────────
async function updateWatchlist(){
    try{const data=await(await fetch('/api/radar')).json();
        if(!Array.isArray(data)||!data.length)return;
        lastRadarData=data;
        const list=document.getElementById('ticker-list'),cnt=document.getElementById('watchlist-count');
        cnt.textContent=data.length;
        data.sort((a,b)=>Math.abs(b.change)-Math.abs(a.change));
        list.innerHTML='';
        data.forEach(t=>{
            const isUp=t.change>=0,ps=t.price<100?t.price.toFixed(6):t.price.toLocaleString('id-ID'),prob=t.prediction?`AI ${(t.prediction*100).toFixed(0)}%`:'';
            const item=document.createElement('div');item.className=`ticker-item ${t.pair===activePair?'active':''}`;item.dataset.pair=t.pair;
            item.onclick=()=>selectPair(t.pair,t.trend,t.prediction);
            item.innerHTML=`<div class="ticker-info"><span class="ticker-pair">${t.pair.toUpperCase()}</span><span class="ticker-price">${ps}</span></div><div class="ticker-meta"><span class="ticker-change ${isUp?'up':'down'}">${isUp?'+':''}${t.change.toFixed(2)}%</span>${prob?`<span class="ticker-prob">${prob}</span>`:''}</div>`;
            if(lastPrices[t.pair]!==undefined&&lastPrices[t.pair]!==t.price)flashEl(item.querySelector('.ticker-price'),t.price>lastPrices[t.pair]?'up':'down');
            lastPrices[t.pair]=t.price;
            list.appendChild(item);
        });
    }catch(e){console.warn('watchlist error',e);}
}

// ── Select Pair ────────────────────────────────────────────
async function selectPair(pair,trend,prob){
    activePair=pair;
    document.getElementById('active-pair').textContent=pair.toUpperCase();
    document.getElementById('pair-trend').textContent=trend||'\u2014';
    const wrap=document.getElementById('ai-prob-wrap'),fill=document.getElementById('ai-prob-fill'),valEl=document.getElementById('ai-prob-val');
    if(prob&&prob>0){wrap.style.display='flex';const p=Math.round(prob*100);fill.style.width=p+'%';valEl.textContent=p+'%';}else wrap.style.display='none';
    document.querySelectorAll('.ticker-item').forEach(el=>el.classList.toggle('active',el.dataset.pair===pair));
    try{const h=await(await fetch('/api/history/'+pair)).json();
        if(!Array.isArray(h)||!h.length)return;
        chart.setData(h.map((v,i)=>({time:Math.floor(Date.now()/1000)-(h.length-i)*60,value:parseFloat(v)})));
    }catch(e){}
}

// ── Trade History ──────────────────────────────────────────
async function updateTradeHistory(){
    try{const data=await(await fetch('/api/trades/recent')).json();
        const tbody=document.getElementById('trade-history-body'),cnt=document.getElementById('ledger-count');
        cnt.textContent=data.length;tbody.innerHTML='';
        data.forEach(t=>{
            const isO=t.status==='OPEN',isW=t.pnl_idr>0,pnl=isO?'\u2014':fmtIDR(t.pnl_idr,true),pCls=isO?'':(isW?'up':'down');
            const time=new Date(t.created_at).toLocaleTimeString('id-ID',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
            const tr=document.createElement('tr');
            tr.innerHTML=`<td class="col-time">${time}</td><td class="col-pair">${t.pair.toUpperCase()}</td><td class="muted">${t.reason||'\u2014'}</td><td class="${pCls}">${pnl}</td><td>${isO?'<span class="col-open">OPEN</span>':'<span class="muted">CLOSED</span>'}</td>`;
            tbody.appendChild(tr);
        });
    }catch(e){console.warn('trade history error',e);}
}

// ── Logs ───────────────────────────────────────────────────
function prependLog(log){
    const c=document.getElementById('ai-logs');
    c.querySelector('.log-placeholder')?.remove();
    const cmap={'PREDICTION':'#3b82f6','REGIME':'#f59e0b','WHALE':'#ec4899','FAKE PUMP':'#ef4444','ANALYSIS':'#a855f7'};
    let col='#8b8fa8';for(const[k,v]of Object.entries(cmap))if(log.side.includes(k)){col=v;break;}
    const time=new Date(log.created_at).toLocaleTimeString('id-ID',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    const div=document.createElement('div');div.className=`log-entry level-${(log.level||'info').toLowerCase()}`;
    div.innerHTML=`<span class="log-time">${time}</span><span class="log-side" style="color:${col}">${log.side}</span> <span class="log-pair">${log.pair}</span> <span class="log-detail">${log.price} \u00b7 ${log.status}</span>`;
    c.prepend(div);while(c.children.length>30)c.lastChild.remove();
}
async function updateLogs(){
    try{const data=await(await fetch('/api/logs')).json();
        const c=document.getElementById('ai-logs');
        if(_logCache.size===0){c.innerHTML='';[...data].reverse().forEach(prependLog);data.forEach(l=>_logCache.add(l.created_at+l.side+l.pair));}
        else{data.filter(l=>!_logCache.has(l.created_at+l.side+l.pair)).forEach(l=>{prependLog(l);_logCache.add(l.created_at+l.side+l.pair);});if(_logCache.size>200)_logCache=new Set([..._logCache].slice(-100));}
    }catch(e){}
}

// ── AI Weights ─────────────────────────────────────────────
async function updateWeights(){
    try{const data=await(await fetch('/api/weights')).json();
        const keys={'ai_prob':'ai-prob','whale_boost':'whale-boost','sector_sentiment':'sector','imbalance':'imbalance'};
        const maxW=Math.max(...Object.values(data),.01);
        for(const[feat,id]of Object.entries(keys)){const v=data[feat]??1,pct=Math.min(100,v/maxW*100);
            document.getElementById('w-'+id)?.style.setProperty('width',pct+'%');
            const ve=document.getElementById('wv-'+id);if(ve)ve.textContent=v.toFixed(2);}
    }catch(e){}
}

// ── Auto-select ────────────────────────────────────────────
async function autoSelect(){
    try{const d=await(await fetch('/api/radar')).json();
        if(Array.isArray(d)&&d.length){const t=[...d].sort((a,b)=>Math.abs(b.change)-Math.abs(a.change))[0];selectPair(t.pair,t.trend,t.prediction);}
    }catch(e){}
}

// ── Boot ───────────────────────────────────────────────────
initSystem();
updateBotStatus();updateStats();updateWatchlist();updateTradeHistory();updatePositions();updateLogs();updateWeights();
setTimeout(autoSelect,1200);
setInterval(updateBotStatus,15000);setInterval(updateStats,5000);setInterval(updateWatchlist,4000);
setInterval(updateTradeHistory,10000);setInterval(updatePositions,6000);setInterval(updateLogs,5000);setInterval(updateWeights,30000);
