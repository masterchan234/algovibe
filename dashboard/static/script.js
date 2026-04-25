/* AlgoVibe Dashboard — script.js (Pristine Version) */
'use strict';

let chart, activePair = null, activeTimeframe = 'LIVE', lastStats = {}, lastPrices = {}, lastRadarData = [], _notifEnabled = false;
let _logCache = new Set();
const socket = typeof io !== 'undefined' ? io() : null;

// ── Theme Management ─────────────────────────────────────────
function toggleTheme() {
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    const newTheme = isLight ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('algovibe_theme', newTheme);
    const btn = document.getElementById('theme-toggle');
    if (btn) btn.textContent = newTheme === 'light' ? '☀️' : '🌙';
}

// Initialize theme on load
(function () {
    const saved = localStorage.getItem('algovibe_theme');
    if (saved === 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
        window.addEventListener('DOMContentLoaded', () => {
            const btn = document.getElementById('theme-toggle');
            if (btn) btn.textContent = '☀️';
        });
    }
})();

// ── AlgoChart ──────────────────────────────────────────────
class AlgoChart {
    constructor(cId, tId) {
        this.canvas = document.getElementById(cId);
        this.ctx = this.canvas.getContext('2d');
        this.tip = document.getElementById(tId);
        this.data = []; this._ohlc = [];
        this.mode = 'area';
        this.hI = -1; this.hC = -1;
        new ResizeObserver(() => this.resize()).observe(this.canvas.parentElement);
        this.canvas.addEventListener('mousemove', e => this._move(e));
        this.canvas.addEventListener('mouseleave', () => { this.hI = this.hC = -1; this.tip.style.display = 'none'; this.draw(); });
    }
    resize() {
        const r = this.canvas.parentElement.getBoundingClientRect(), dpr = devicePixelRatio || 1;
        this.canvas.width = r.width * dpr; this.canvas.height = r.height * dpr;
        this.ctx.scale(dpr, dpr);
        this.canvas.style.width = r.width + 'px'; this.canvas.style.height = r.height + 'px';
        this.draw();
    }
    setData(d) {
        this.data = d;
        // Jika data sudah dalam format OHLC (dari exchange), gunakan langsung
        if (d.length && d[0].open !== undefined) {
            this._ohlc = d;
        } else {
            this._ohlc = this._ohlcBuild(d.map(x => x.value));
        }
        const empty = document.getElementById('chart-empty');
        if (empty) empty.style.display = d.length ? 'none' : 'flex';
        this.draw();
    }
    appendPoint(pair, price) {
        if (activePair !== pair || !this.data.length) return;
        const now = Math.floor(Date.now() / 1000);
        const last = this.data[this.data.length - 1];
        const val = parseFloat(price);
        
        if (activeTimeframe === 'LIVE') {
            if (last.value === val) return;
            this.data.push({ time: now, value: val });
            if (this.data.length > 100) this.data.shift();
            if (this.mode === 'candle') this._ohlc = this._ohlcBuild(this.data.map(x => x.value));
        } else {
            // Update candle terakhir jika timeframe OHLCV
            const tfMap = { '5m': 300, '15m': 900, '1h': 3600 };
            const seconds = tfMap[activeTimeframe] || 3600;
            
            // Jika candle baru (berdasarkan waktu)
            if (now - last.time >= seconds) {
                this.data.push({
                    time: Math.floor(now / seconds) * seconds,
                    open: val, high: val, low: val, close: val, value: val
                });
                if (this.data.length > 100) this.data.shift();
            } else {
                // Update candle berjalan
                last.close = val;
                last.value = val;
                if (val > last.high) last.high = val;
                if (val < last.low) last.low = val;
            }
            this._ohlc = this.data; // Gunakan data langsung sebagai OHLC
        }
        this.draw();
    }
    async loadData(pair) {
        try {
            let data = [];
            if (activeTimeframe === 'LIVE') {
                // Ticks dari Bot History
                const h = await (await fetch('/api/history/' + pair)).json();
                if (!Array.isArray(h) || !h.length) return;
                data = h.map((v, i) => ({
                    time: Math.floor(Date.now() / 1000) - (h.length - i) * 60,
                    value: parseFloat(v)
                }));
            } else {
                // Candles dari Exchange
                const ohlcv = await (await fetch(`/api/ohlcv/${pair}/${activeTimeframe}`)).json();
                if (!Array.isArray(ohlcv) || !ohlcv.length) return;
                data = ohlcv.map(c => ({
                    time: c[0] / 1000,
                    open: c[1], high: c[2], low: c[3], close: c[4], value: c[4]
                }));
            }
            this.setData(data);
        } catch (e) { console.error('LoadData error', e); }
    }
    setMode(m) {
        this.mode = m; this.hI = this.hC = -1; this.tip.style.display = 'none';
        document.querySelectorAll('.chart-toggle').forEach(b => b.classList.remove('active'));
        const btn = document.getElementById('chart-' + m + '-btn');
        if (btn) btn.classList.add('active');
        this.draw();
    }
    _ohlcBuild(prices, g = 6) {
        const out = [];
        for (let i = 0; i < prices.length; i += g) { const s = prices.slice(i, i + g); if (!s.length) break; out.push({ open: s[0], close: s[s.length - 1], high: Math.max(...s), low: Math.min(...s) }); }
        return out;
    }
    _sma(arr, n) { return arr.map((_, i) => { if (i < n - 1) return null; const s = arr.slice(i - n + 1, i + 1); return s.reduce((a, b) => a + b, 0) / n; }); }
    _fmt(v) { return v >= 1000 ? v.toLocaleString('id-ID') : v.toFixed(6); }
    _tipPos(x, W) { this.tip.style.display = 'block'; const tw = this.tip.getBoundingClientRect().width; this.tip.style.left = (x + 12 + tw > W ? x - tw - 12 : x + 12) + 'px'; this.tip.style.top = '8px'; }
    _move(e) {
        if (!this.data.length) return;
        const r = this.canvas.getBoundingClientRect(), x = e.clientX - r.left, W = r.width;
        if (this.mode === 'candle') {
            const o = this._ohlc; if (!o.length) return;
            let i = Math.max(0, Math.min(Math.floor(x / (W / o.length)), o.length - 1));
            if (this.hC !== i) {
                this.hC = i; this.draw(); const c = o[i];
                this.tip.innerHTML = `<span style="color:var(--text-secondary)">O</span> ${this._fmt(c.open)} <span style="color:var(--green)">H</span> ${this._fmt(c.high)}<br><span style="color:var(--red)">L</span> ${this._fmt(c.low)} <span style="color:var(--text-primary)">C</span> ${this._fmt(c.close)}`;
                this._tipPos(x, W);
            }
        } else {
            const step = W / Math.max(1, this.data.length - 1);
            let i = Math.max(0, Math.min(Math.round(x / step), this.data.length - 1));
            if (this.hI !== i) { this.hI = i; this.draw(); this.tip.textContent = 'Rp ' + this._fmt(this.data[i].value); this._tipPos(x, W); }
        }
    }
    draw() { if (this.mode === 'candle') this._drawCandle(); else this._drawArea(); }
    _drawArea() {
        const r = this.canvas.parentElement.getBoundingClientRect(), W = r.width, H = r.height, ctx = this.ctx;
        ctx.clearRect(0, 0, W, H); if (!this.data.length) return;
        const vals = this.data.map(d => d.value), minV = Math.min(...vals), maxV = Math.max(...vals), range = maxV - minV || 1;
        const pad = H * .1, dH = H - pad * 2, stepX = W / Math.max(1, this.data.length - 1);
        const pts = vals.map((v, i) => ({ x: i * stepX, y: H - pad - ((v - minV) / range) * dH }));
        ctx.beginPath(); ctx.moveTo(pts[0].x, pts[0].y); pts.forEach(p => ctx.lineTo(p.x, p.y));
        ctx.lineTo(W, H); ctx.lineTo(0, H); ctx.closePath();
        const g = ctx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, 'rgba(59,130,246,.2)'); g.addColorStop(1, 'rgba(59,130,246,0)');
        ctx.fillStyle = g; ctx.fill();
        ctx.beginPath(); pts.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
        ctx.strokeStyle = '#3b82f6'; ctx.lineWidth = 1.5; ctx.lineJoin = 'round'; ctx.stroke();
        
        if (this.hI >= 0 && this.hI < pts.length) {
            const p = pts[this.hI];
            const isLight = document.documentElement.getAttribute('data-theme') === 'light';
            ctx.beginPath(); ctx.setLineDash([3, 4]); ctx.moveTo(p.x, 0); ctx.lineTo(p.x, H);
            ctx.strokeStyle = isLight ? 'rgba(0,0,0,.1)' : 'rgba(255,255,255,.1)'; ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([]);
            ctx.beginPath(); ctx.arc(p.x, p.y, 4.5, 0, Math.PI * 2); ctx.fillStyle = isLight ? '#ffffff' : '#08090b'; ctx.fill();
            ctx.strokeStyle = '#3b82f6'; ctx.lineWidth = 2; ctx.stroke();
        }
    }
    _drawCandle() {
        const r = this.canvas.parentElement.getBoundingClientRect(), W = r.width, H = r.height, ctx = this.ctx;
        ctx.clearRect(0, 0, W, H); const ohlc = this._ohlc; if (!ohlc.length) return;
        const allP = ohlc.flatMap(c => [c.high, c.low]), minV = Math.min(...allP), maxV = Math.max(...allP), range = maxV - minV || 1;
        const pad = H * .1, dH = H - pad * 2, toY = v => H - pad - ((v - minV) / range) * dH;
        const slotW = W / ohlc.length, bW = Math.max(2, slotW * .55);
        const isLight = document.documentElement.getAttribute('data-theme') === 'light';
        if (this.hC >= 0) { ctx.fillStyle = isLight ? 'rgba(0,0,0,.04)' : 'rgba(255,255,255,.03)'; ctx.fillRect(this.hC * slotW, 0, slotW, H); }
        ohlc.forEach((c, i) => {
            const cx = (i + .5) * slotW, bull = c.close >= c.open, col = bull ? '#10b981' : '#ef4444';
            ctx.strokeStyle = col; ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(cx, toY(c.high)); ctx.lineTo(cx, toY(c.low)); ctx.stroke();
            const bT = toY(Math.max(c.open, c.close)), bH = Math.max(1.5, toY(Math.min(c.open, c.close)) - bT);
            ctx.fillStyle = bull ? 'rgba(16,185,129,.75)' : 'rgba(239,68,68,.75)';
            ctx.fillRect(cx - bW / 2, bT, bW, bH); ctx.strokeStyle = col; ctx.lineWidth = .8; ctx.strokeRect(cx - bW / 2, bT, bW, bH);
        });
    }
}

// ── Global Logic & Rendering ─────────────────────────────────

function setChartMode(mode) {
    if (chart) chart.setMode(mode);
}

function setChartTimeframe(tf) {
    activeTimeframe = tf;
    // Update UI
    document.querySelectorAll('.chart-tf-btn').forEach(btn => {
        btn.classList.toggle('active', btn.textContent.toLowerCase() === tf.toLowerCase());
    });
    if (activePair && chart) chart.loadData(activePair);
}

async function selectPair(pair, trend, prob) {
    activePair = pair;
    const apEl = document.getElementById('active-pair');
    if (apEl) apEl.textContent = formatPair(pair);

    const emptyEl = document.getElementById('chart-empty');
    if (emptyEl) emptyEl.style.display = 'none';

    // Instant UI Reset for Reasoner
    const cEl = document.getElementById('llm-insight-text');
    if (cEl) {
        cEl.textContent = `Menganalisa data pasar ${pair.toUpperCase()}...`;
        cEl.style.opacity = '0.5';
    }
    const tEl = document.getElementById('llm-insight-type');
    if (tEl) tEl.textContent = 'SWITCHING FOCUS...';

    // Reset technical badges
    ['rsi-badge', 'vol-badge', 'imb-badge', 'trend-badge', 'spread-badge'].forEach(id => {
        const el = document.getElementById(id);
        if (el) { el.textContent = '--'; el.style.color = '#8b8fa8'; }
    });
    const sigEl = document.getElementById('tech-signal');
    if (sigEl) { sigEl.textContent = 'NEUTRAL'; sigEl.style.background = 'rgba(255,255,255,0.05)'; sigEl.style.color = '#8b8fa8'; }

    // Notify server to stream orderbook for this pair
    if (socket) socket.emit('watch_pair', { pair: pair });

    // Auto-extract and fallback logic from lastRadarData
    let t = trend || 'NEUTRAL';
    let p = prob || 0;
    if (lastRadarData && lastRadarData.length > 0) {
        const radarMatch = lastRadarData.find(x => x.pair === pair);
        if (radarMatch) {
            p = radarMatch.prediction || p;
            let effectiveTrend = radarMatch.trend;
            if (!effectiveTrend || effectiveTrend === 'BACKFILLED' || effectiveTrend === 'N/A') {
                const verd = radarMatch.tech_verdict || 'HOLD';
                if (verd === 'BUY') effectiveTrend = 'BULLISH';
                else if (verd === 'SELL') effectiveTrend = 'BEARISH';
                else effectiveTrend = 'RANGING';
            }
            t = effectiveTrend;
        }
    }

    const trendEl = document.getElementById('pair-trend');
    if (trendEl) {
        let dTrend = t;
        if (t === 'BACKFILLED' || t === 'N/A') dTrend = '⏳ ANALISA';
        else if (t === 'BULLISH') dTrend = '🚀 NAIK';
        else if (t === 'BEARISH') dTrend = '🩸 TURUN';
        else if (t === 'RANGING') dTrend = '⚖️ STABIL';
        else if (t === 'CHOPPY') dTrend = '🌪️ FLUKTUATIF';

        trendEl.textContent = dTrend;
        trendEl.className = 'chart-subtitle ' + (t === 'BULLISH' ? 'up' : (t === 'BEARISH' ? 'down' : ''));
    }

    const probWrap = document.getElementById('ai-prob-wrap');
    if (probWrap) {
        probWrap.style.display = p > 0 ? 'flex' : 'none';
        const fill = document.getElementById('ai-prob-fill');
        const valEl = document.getElementById('ai-prob-val');
        if (fill) fill.style.width = (p * 100) + '%';
        if (valEl) valEl.textContent = Math.round(p * 100) + '%';
    }

    refreshWatchlist();
    if (chart) chart.loadData(pair);
}

function renderOrderBook(data) {
    try {
        const bList = document.getElementById('ob-bids'), aList = document.getElementById('ob-asks');
        if (!bList || !aList || !data) return;

        const maxVol = Math.max(
            ...(data.bids || []).map(b => b.vol),
            ...(data.asks || []).map(a => a.vol),
            1
        );

        const renderRow = (item, isBid, max) => {
            if (!item || item.price === undefined || item.vol === undefined) return '';
            const pct = Math.min((item.vol / max) * 100, 100);
            const color = isBid ? 'var(--green)' : 'var(--red)';
            const bg = isBid ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)';
            const align = isBid ? 'right:0;' : 'left:0;';
            const flex = isBid ? 'flex-start' : 'flex-end';
            return `<div class="ob-row" style="position:relative; height:18px; display:flex; align-items:center; justify-content:${flex}; padding:0 6px; overflow:hidden;">
                <div style="position:absolute; ${align} top:0; bottom:0; width:${pct}%; background:${bg}; transition:width 0.3s;"></div>
                <span style="color:${color}; font-weight:700; z-index:1; font-size:10px;">${formatNumber(item.price)}</span>
                <span style="position:absolute; ${isBid ? 'right' : 'left'}:6px; font-size:8px; color:var(--text-muted); opacity:0.6; z-index:1;">${(item.vol / 1e6).toFixed(1)}M</span>
            </div>`;
        };

        bList.innerHTML = (data.bids || []).map(b => renderRow(b, true, maxVol)).join('');
        aList.innerHTML = (data.asks || []).map(a => renderRow(a, false, maxVol)).join('');

    } catch (e) { console.warn('renderOrderBook error', e); }
}

function renderStats(data) {
    try {
        // ── Bot Status Badge — update saat data pertama diterima ──
        const statusBadge = document.getElementById('bot-status-badge');
        const statusText = document.getElementById('bot-status-text');
        if (statusBadge && statusText && data.bot_running) {
            statusBadge.className = 'bot-status-badge status-live';
            statusText.textContent = data.backfill_status === 'DRY_RUN' ? 'DRY RUN' : 'LIVE';
        }

        const pnlEl = document.getElementById('daily-pnl');
        if (pnlEl) {
            const np = fmtIDR(data.daily_pnl, true);
            if (pnlEl.textContent !== np) {
                pnlEl.textContent = np;
                pnlEl.className = 'value ' + (data.daily_pnl >= 0 ? 'up' : 'down');
                flashEl(pnlEl, data.daily_pnl >= 0 ? 'up' : 'down');
            }
        }
        const wr = document.getElementById('win-rate');
        if (wr) {
            wr.textContent = data.win_rate.toFixed(1) + '%';
            wr.className = 'value ' + (data.win_rate >= 50 ? 'up' : 'down');
        }
        const unr = document.getElementById('unrealized-pnl');
        if (unr) {
            const uv = data.unrealized_pnl || 0;
            unr.textContent = fmtIDR(uv, true);
            unr.className = 'value ' + (uv >= 0 ? 'up' : 'down');
        }
        setRegimeBadge(data.regime);
        if (data.sectors) renderSectors(data.sectors);

        // Update Market Temp (Sentiment Index)
        if (data.market_sentiment_index !== undefined) {
            const tempVal = document.getElementById('sentiment-value');
            const tempIcon = document.getElementById('sentiment-icon');
            if (tempVal && tempIcon) {
                const idx = data.market_sentiment_index;

                let icon = '😐';
                let label = 'Netral';
                let color = 'var(--yellow)';

                if (idx <= 25) { icon = '😱'; label = 'Panik Ekstrem'; color = 'var(--red)'; }
                else if (idx < 45) { icon = '😨'; label = 'Takut'; color = 'var(--red)'; }
                else if (idx <= 55) { icon = '😐'; label = 'Netral'; color = 'var(--yellow)'; }
                else if (idx < 75) { icon = '😏'; label = 'Optimis'; color = 'var(--green)'; }
                else { icon = '🤑'; label = 'Euforia'; color = 'var(--green)'; }

                tempVal.textContent = `${idx}% (${label})`;
                tempVal.style.color = color;
                tempIcon.textContent = icon;
            }
        }

        // Alerts
        if (data.latest_whale && data.latest_whale !== lastStats.latest_whale) {
            showToast('🐋 WHALE: ' + data.latest_whale, 'whale');
            fireNotif('🐋 Whale Detected', data.latest_whale);
        }

        // ── Technical Pulse Rendering ──
        if (data.technicals) {
            const rsiEl = document.getElementById('rsi-badge');
            const volEl = document.getElementById('vol-badge');
            const imbEl = document.getElementById('imb-badge');
            const trnEl = document.getElementById('trend-badge');
            const sprEl = document.getElementById('spread-badge');
            const sigEl = document.getElementById('tech-signal');

            // ── Live Price Strip (Reasoner Header) ──
            const pairLbl = document.getElementById('reasoner-pair');
            const priceEl = document.getElementById('reasoner-price');
            const changeEl = document.getElementById('reasoner-change');
            if (pairLbl && activePair) {
                pairLbl.textContent = formatPair(activePair) + '/IDR';
            }
            if (priceEl && data.technicals.last_price) {
                const p = data.technicals.last_price;
                priceEl.textContent = 'Rp ' + (p >= 1000 ? Math.round(p).toLocaleString('id-ID') : p.toFixed(6));
                // Flash animasi saat harga berubah
                if (priceEl._lastVal && priceEl._lastVal !== p) {
                    priceEl.style.color = p > priceEl._lastVal ? 'var(--green)' : 'var(--red)';
                    setTimeout(() => { priceEl.style.color = ''; }, 600);

                    // Update chart real-time
                    if (chart && chart.data && chart.data.length > 0) {
                        chart.data.push({
                            time: Math.floor(Date.now() / 1000),
                            value: p
                        });
                        if (chart.data.length > 60) chart.data.shift();
                        chart._ohlc = chart._ohlcBuild(chart.data.map(x => x.value));
                        chart.draw();
                    }
                }
                priceEl._lastVal = p;
            }
            if (changeEl && lastRadarData.length && activePair) {
                const radarItem = lastRadarData.find(r => r.pair === activePair);
                if (radarItem) {
                    const chg = radarItem.change || 0;
                    const sign = chg >= 0 ? '▲' : '▼';
                    changeEl.textContent = `${sign} ${Math.abs(chg).toFixed(2)}%`;
                    changeEl.style.color = chg >= 0 ? '#10b981' : '#ef4444';
                }
            }

            if (rsiEl) {
                rsiEl.textContent = `RSI: ${data.technicals.rsi}`;
                rsiEl.style.color = data.technicals.rsi < 35 ? 'var(--green)' : (data.technicals.rsi > 65 ? 'var(--red)' : 'var(--text-secondary)');
            }
            if (volEl) volEl.textContent = `VOL: ${data.technicals.volatility}%`;

            if (imbEl) {
                const bp = data.technicals.buy_pressure;
                imbEl.textContent = `IMB: ${bp}%`;
                imbEl.style.color = bp > 60 ? 'var(--green)' : (bp < 40 ? 'var(--red)' : 'var(--text-secondary)');
            }

            if (trnEl) {
                const sl = data.technicals.slope;
                trnEl.textContent = `TREND: ${sl > 0 ? '↗' : (sl < 0 ? '↘' : '→')}`;
                trnEl.style.color = sl > 0.002 ? 'var(--green)' : (sl < -0.002 ? 'var(--red)' : 'var(--text-secondary)');
            }

            if (sprEl) {
                sprEl.textContent = `SPR: ${data.technicals.spread.toFixed(3)}%`;
                sprEl.style.color = data.technicals.spread > 0.1 ? 'var(--yellow)' : 'var(--text-secondary)';
            }

            if (sigEl) {
                sigEl.textContent = data.technicals.signal;
                let col = 'var(--text-secondary)', bg = 'rgba(139,143,168,0.1)';
                if (data.technicals.signal.includes('BULLISH') || data.technicals.signal.includes('OVERSOLD')) {
                    col = 'var(--green)'; bg = 'rgba(16,185,129,0.1)';
                } else if (data.technicals.signal.includes('BEARISH') || data.technicals.signal.includes('OVERBOUGHT')) {
                    col = 'var(--red)'; bg = 'rgba(239,68,68,0.1)';
                }
                sigEl.style.color = col;
                sigEl.style.background = bg;
            }
        }

        // ── LiteRT-LM Reasoner Rendering ──
        if (data.active_reasoning) {
            const tEl = document.getElementById('llm-insight-type');
            const cEl = document.getElementById('llm-insight-text');
            if (tEl) {
                tEl.textContent = `ANALYSIS: ${activePair ? activePair.toUpperCase() : 'GLOBAL'}`;
                tEl.className = 'insight-type ' + (data.active_reasoning.includes('BUY') ? 'buy' : (data.active_reasoning.includes('SELL') ? 'sell' : ''));
            }
            if (cEl && cEl.textContent !== data.active_reasoning) {
                cEl.textContent = data.active_reasoning;
                // Add a subtle fade effect
                cEl.style.opacity = '0';
                setTimeout(() => { cEl.style.opacity = '1'; }, 50);
            }
        }

        if (data.backfill_status) {
            const bb = document.getElementById('backfill-badge');
            if (bb) {
                if (data.backfill_status === 'SYNCING') {
                    bb.style.display = 'block'; bb.textContent = '📥 SYNCING...'; bb.style.background = 'var(--yellow)';
                } else if (data.history_since && data.history_since !== '--/-- --:--') {
                    bb.style.display = 'block'; bb.textContent = '🕒 Data Since: ' + data.history_since; bb.style.background = 'var(--green)';
                } else { bb.style.display = 'none'; }
            }
        }
        lastStats = data;
    } catch (e) { console.warn('renderStats error', e); }
}

/* ─── Smooth Ticker (RAF-driven, glitch-free) ─────────────────── */
let _tickerX = 0;
let _tickerRAF = null;
let _tickerLastPairs = '';
const TICKER_SPEED = 0.4; // px per frame — sesuaikan untuk kecepatan

function _tickerLoop() {
    const el = document.getElementById('ticker-content');
    if (!el) return;
    const halfW = el.scrollWidth / 2;
    _tickerX -= TICKER_SPEED;
    if (Math.abs(_tickerX) >= halfW) _tickerX = 0; // seamless loop
    el.style.transform = `translateX(${_tickerX}px)`;
    _tickerRAF = requestAnimationFrame(_tickerLoop);
}

function updateTicker(data) {
    if (!Array.isArray(data) || !data.length) return;

    // Hanya rebuild HTML jika daftar koin berubah (mencegah reset posisi)
    const pairKey = data.map(d => d.pair).sort().join(',');
    if (pairKey === _tickerLastPairs) return;
    _tickerLastPairs = pairKey;

    const tickerContent = document.getElementById('ticker-content');
    if (!tickerContent) return;

    const sorted = [...data].sort((a, b) => Math.abs(b.change || 0) - Math.abs(a.change || 0)).slice(0, 20);
    let html = '';
    sorted.forEach(item => {
        const chg = item.change || 0;
        const sign = chg >= 0 ? '▲' : '▼';
        const cls = chg >= 0 ? 'bps-up' : 'bps-down';
        const label = `${sign} ${Math.abs(chg).toFixed(2)}%`;
        const name = formatPair(item.pair);
        html += `<div class="ticker-item"><span class="ticker-pair">${name}</span><span class="ticker-price">${formatNumber(item.price)}</span><span class="ticker-bps ${cls}">${label}</span><span class="ticker-sep">·</span></div>`;
    });

    // Duplikat 2x untuk seamless loop
    tickerContent.innerHTML = html + html;

    // Mulai animasi RAF jika belum berjalan
    if (!_tickerRAF) _tickerLoop();
}



function renderWatchlist(data) {
    try {
        updateTicker(data);
        const list = document.getElementById('ticker-list'), cnt = document.getElementById('watchlist-count');
        if (cnt) cnt.textContent = (data && data.length) ? data.length : 0;
        
        // Update Chart Realtime jika data untuk pair aktif masuk
        if (data && data.length) {
            const activeData = data.find(d => d.pair === activePair);
            if (activeData && chart) {
                chart.appendPoint(activePair, activeData.price);
            }
        }
        
        if (!list) return;
        if (!data || !data.length) { list.innerHTML = '<div class="empty-state">Menunggu data...</div>'; return; }
        list.innerHTML = ''; lastRadarData = data;

        const sorted = [...data].sort((a, b) => a.pair.localeCompare(b.pair));
        sorted.forEach(t => {
            const isUp = t.change >= 0, diff = (isUp ? '+' : '') + t.change.toFixed(2) + '%';
            const price = t.price >= 1000 ? t.price.toLocaleString('id-ID') : t.price.toFixed(6);

            // Signal badge (hanya tampil jika confidence >= 60)
            const conf = t.tech_confidence || 0;
            const verd = t.tech_verdict || 'HOLD';
            let badgeHtml = '';
            if (conf >= 60) {
                const bCls = verd === 'BUY' ? 'sig-buy' : (verd === 'SELL' ? 'sig-sell' : 'sig-hold');
                const bIcon = verd === 'BUY' ? '📈' : (verd === 'SELL' ? '📉' : '⏸');
                badgeHtml = `<span class="sig-badge ${bCls}" title="${t.tech_reason || ''}">${bIcon} ${verd} ${conf}%</span>`;
            }

            // Fallback to LiteRT-LM Reasoner logic if ML is still Backfilling
            let effectiveTrend = t.trend;
            if (!effectiveTrend || effectiveTrend === 'BACKFILLED' || effectiveTrend === 'N/A') {
                if (verd === 'BUY') effectiveTrend = 'BULLISH';
                else if (verd === 'SELL') effectiveTrend = 'BEARISH';
                else effectiveTrend = 'RANGING';
            }

            const item = document.createElement('div');
            item.className = 'ticker-item ' + (activePair === t.pair ? 'active' : '');
            item.onclick = () => selectPair(t.pair, effectiveTrend, t.prediction);
            item.innerHTML = `
                <div class="ticker-main"><b>${formatPair(t.pair)}</b> <span>Rp ${price}</span></div>
                <div class="ticker-side">
                    <span class="${isUp ? 'up' : 'down'}">${diff}</span>
                    ${badgeHtml}
                </div>`;
            const lastP = lastPrices[t.pair];
            if (lastP && lastP !== t.price) { flashEl(item, t.price > lastP ? 'up' : 'down'); }
            lastPrices[t.pair] = t.price;
            list.appendChild(item);
        });

        // Market Pulse
        const gList = document.getElementById('gainers-list'), lList = document.getElementById('losers-list');
        const tf = 'change_24h'; // Selalu gunakan data realtime 24h
        if (gList && lList) {
            gList.innerHTML = ''; lList.innerHTML = '';
            const gainers = [...data].sort((a, b) => (b[tf] || 0) - (a[tf] || 0)).slice(0, 10);
            const losers = [...data].sort((a, b) => (a[tf] || 0) - (b[tf] || 0)).slice(0, 10);
            const renderItem = (t, container, isUp) => {
                const item = document.createElement('div'); item.className = 'pulse-item ' + (isUp ? 'up' : 'down');
                item.onclick = () => selectPair(t.pair, t.trend, t.prediction);
                item.innerHTML = `<span>${formatPair(t.pair)}</span><b>${((t[tf] || 0) >= 0 ? '+' : '') + (t[tf] || 0).toFixed(1)}%</b>`;
                container.appendChild(item);
            };
            gainers.forEach(t => renderItem(t, gList, true));
            losers.forEach(t => renderItem(t, lList, false));
        }

        // Trending
        const tList = document.getElementById('trending-list');
        if (tList) {
            tList.innerHTML = '';
            const trending = [...data].sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 10);
            trending.forEach(t => {
                const item = document.createElement('div'); item.className = 'trending-item';
                item.onclick = () => selectPair(t.pair, t.trend, t.prediction);
                item.innerHTML = `<div class="trend-info"><span class="trend-pair">${formatPair(t.pair)}</span><span class="trend-score">Volatilitas: ${t.volatility.toFixed(1)}%</span></div><div class="trend-badge">${((t.score || 0) / 1e6).toFixed(0)}M</div>`;
                tList.appendChild(item);
            });
        }
    } catch (e) { console.warn('renderWatchlist error', e); }
}

function renderSectors(sectors) {
    const c = document.getElementById('sector-list'); if (!c) return;
    c.innerHTML = ''; const entries = Object.entries(sectors);
    if (!entries.length) { c.innerHTML = '<div class="empty-state">Belum ada data</div>'; return; }
    const maxS = Math.max(...entries.map(([, v]) => Math.abs(v)), 1);
    entries.sort((a, b) => b[1] - a[1]).forEach(([sec, score]) => {
        const pct = Math.min(100, Math.abs(score) / maxS * 100), col = score >= 0 ? '#10b981' : '#ef4444', lbl = (score >= 0 ? '+' : '') + score.toFixed(1);
        const div = document.createElement('div'); div.className = 'sector-item';
        div.innerHTML = `<div class="sector-header"><span class="sector-name">${sec}</span><span class="sector-score" style="color:${col}">${lbl}</span></div><div class="sector-track"><div class="sector-fill" style="width:${pct}%;background:${col}"></div></div>`;
        c.appendChild(div);
    });
}

function renderTradeHistory(data) {
    const tbody = document.getElementById('trade-history-body'), cnt = document.getElementById('ledger-count');
    if (!tbody || !cnt) return;
    cnt.textContent = data.length;
    tbody.innerHTML = '';

    data.slice(0, 50).forEach(t => {
        const time = t.created_at
            ? new Date(t.created_at).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' })
            : '--:--';
        const tr = document.createElement('tr');

        let pnlCls = t.pnl_idr >= 0 ? 'up' : 'down';
        let pnlIdr = fmtIDR(t.pnl_idr, true);
        let pnlPct = t.pnl_pct !== undefined ? ` (${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(2)}%)` : '';

        // Formatting volume
        const volCrypto = t.amount_crypto ? t.amount_crypto.toFixed(t.amount_crypto < 1 ? 6 : 2) : '0';
        const volIDR = t.amount_idr ? Math.round(t.amount_idr).toLocaleString('id-ID') : '0';

        tr.style.cursor = 'pointer';
        tr.onclick = () => selectPair(t.pair);

        if (t.status === 'OPEN') {
            let durStr = '--';
            if (t.created_at) {
                const s = Math.floor((Date.now() - new Date(t.created_at).getTime()) / 1000);
                durStr = s > 3600 ? Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'm' : (s > 60 ? Math.floor(s / 60) + 'm' : s + 's');
            }

            tr.style.background = 'rgba(245, 158, 11, 0.04)';
            tr.innerHTML = `
                <td><span style="font-weight:600">${time}</span></td>
                <td>
                    <div style="line-height:1.2">
                        <b>${formatPair(t.pair)}</b><br>
                        <span class="muted" style="font-size:9px">Beli: ${formatNumber(t.entry_price)}</span><br>
                        <span class="muted" style="font-size:9px">Live: ${formatNumber(t.last_price)}</span>
                    </div>
                </td>
                <td>
                    <div style="line-height:1.2">
                        <span style="font-weight:600; font-size:10px">${volCrypto}</span><br>
                        <span class="muted" style="font-size:9.5px">Rp ${volIDR}</span>
                    </div>
                </td>
                <td>
                    <div style="line-height:1.2">
                        <span style="color:var(--yellow);font-weight:700;font-size:10px">📌 OPEN POSITION</span><br>
                        <span class="muted" style="font-size:9.5px">⏳ ${durStr}</span>
                    </div>
                </td>
                <td>
                    <div style="line-height:1.3">
                        <b class="${pnlCls}">${pnlIdr}</b><br>
                        <span class="${pnlCls}" style="font-size:9.5px">${pnlPct}</span>
                    </div>
                </td>`;
        } else {
            let durStr = t.duration ? (t.duration > 3600 ? Math.floor(t.duration / 3600) + 'h ' + Math.floor((t.duration % 3600) / 60) + 'm' : (t.duration > 60 ? Math.floor(t.duration / 60) + 'm' : t.duration + 's')) : '--';

            let reason = t.reason || 'AI';
            let rIcon = '🤖';
            if (reason.includes('TP')) rIcon = '🎯';
            else if (reason.includes('SL')) rIcon = '🛑';
            else if (reason.includes('Force')) rIcon = '⚠️';

            tr.innerHTML = `
                <td><span style="font-weight:600;color:var(--text-secondary)">${time}</span></td>
                <td>
                    <div style="line-height:1.2">
                        <b>${formatPair(t.pair)}</b><br>
                        <span class="muted" style="font-size:9px">Beli: ${formatNumber(t.entry_price)}</span><br>
                        <span class="muted" style="font-size:9px">Jual: ${formatNumber(t.last_price)}</span>
                    </div>
                </td>
                <td>
                    <div style="line-height:1.2">
                        <span style="font-weight:600; font-size:10px">${volCrypto}</span><br>
                        <span class="muted" style="font-size:9.5px">Rp ${volIDR}</span>
                    </div>
                </td>
                <td>
                    <div style="line-height:1.2">
                        <span style="font-weight:600; font-size:10px">${rIcon} ${reason}</span><br>
                        <span class="muted" style="font-size:9.5px">⏳ ${durStr}</span>
                    </div>
                </td>
                <td>
                    <div style="line-height:1.3">
                        <b class="${pnlCls}">${pnlIdr}</b><br>
                        <span class="${pnlCls}" style="font-size:9.5px">${pnlPct}</span>
                    </div>
                </td>`;
        }
        tbody.appendChild(tr);
    });
}

function renderLogs(data) {
    const c = document.getElementById('ai-logs'); if (!c) return;
    const items = data.filter(l => !_logCache.has(l.created_at + l.side + l.pair)).slice(0, 20);
    items.forEach(log => {
        _logCache.add(log.created_at + log.side + log.pair);
        const div = document.createElement('div'); div.className = 'log-entry';
        const time = new Date(log.created_at).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit' });
        div.innerHTML = `<span class="log-time">${time}</span> <span class="log-side">${log.side}</span> <span class="log-pair">${formatPair(log.pair)}</span> <span class="log-detail">${log.price}</span>`;
        c.prepend(div); while (c.children.length > 30) c.lastChild.remove();
    });
}

// ── Helpers ──────────────────────────────────────────────────
function formatNumber(v, d = 0) {
    if (v === undefined || v === null || isNaN(v)) return '--';
    return v >= 1000 ? Math.round(v).toLocaleString('id-ID') : v.toFixed(d > 0 ? d : (v < 1 ? 6 : 2));
}
function formatPair(pair) {
    if (!pair) return '';
    return pair.toLowerCase().replace('_idr', '').replace('idr', '').toUpperCase();
}
function fmtIDR(n, s = false) { const a = Math.abs(n), v = new Intl.NumberFormat('id-ID', { style: 'currency', currency: 'IDR', maximumFractionDigits: 0 }).format(a); return s ? (n >= 0 ? '+' : '\u2212') + v : n < 0 ? '\u2212' + v : v; }
function flashEl(el, d) { if (!el) return; el.classList.remove('flash-up', 'flash-down'); void el.offsetWidth; el.classList.add(d === 'up' ? 'flash-up' : 'flash-down'); }
function showToast(m, t = 'info') { /* Simple alert for now or implement toast UI */ console.log(`[TOAST ${t}] ${m}`); }

function setRegimeBadge(r) {
    const b = document.getElementById('market-regime-badge');
    const t = document.getElementById('market-regime-text');
    if (!b || !t) return;

    const r_upper = (r || '').toUpperCase();
    const m = { 'BULLISH': 'regime-bullish', 'BEARISH': 'regime-bearish', 'CHOPPY': 'regime-choppy', 'RANGING': 'regime-ranging' };
    b.className = 'regime-badge ' + (m[r_upper] || 'regime-choppy');

    let text = 'ANALYZING';
    if (r_upper === 'BULLISH') text = '🚀 TREN NAIK';
    else if (r_upper === 'BEARISH') text = '🩸 TREN TURUN';
    else if (r_upper === 'RANGING') text = '⚖️ STABIL / RANGING';
    else if (r_upper === 'CHOPPY') text = '🌪️ FLUKTUATIF / CHOPPY';

    t.textContent = text;
}
function fireNotif(t, b) { if (_notifEnabled && Notification.permission === 'granted') new Notification(t, { body: b }); }
function toggleSection(el) { if (el) el.classList.toggle('collapsed'); }
function refreshWatchlist() { if (lastRadarData.length) renderWatchlist(lastRadarData); }

// ── Initialization ───────────────────────────────────────────
function initSystem() {
    chart = new AlgoChart('custom-chart', 'chart-tooltip');
    if (socket) {
        socket.on('connect', () => console.log('✅ Dashboard Socket Connected!'));
        socket.on('stats_update', renderStats);
        socket.on('radar_update', renderWatchlist);
        socket.on('orderbook_update', renderOrderBook);
        socket.on('market_trades_update', renderMarketTrades);
        socket.on('positions_update', d => console.log('Positions update received', d));
        socket.on('trades_update', renderTradeHistory);
        socket.on('logs_update', renderLogs);
    }
}

window.addEventListener('DOMContentLoaded', () => {
    initSystem();
    setTimeout(async () => {
        try {
            const d = await (await fetch('/api/radar')).json();
            if (d.length) selectPair(d[0].pair, d[0].trend, d[0].prediction);
        } catch (e) { }
    }, 1500);
});

function renderMarketTrades(data) {
    const tsEl = document.getElementById('trades-last-update');
    if (tsEl) tsEl.textContent = 'UPDATED: ' + new Date().toLocaleTimeString();
    
    if (!data || data.pair.toLowerCase() !== activePair.toLowerCase()) return;
    const list = document.getElementById('market-trades-list');
    if (!list) return;
    
    // Clear list if we have no trades yet to avoid showing stale data or placeholder
    if (!data.trades || !data.trades.length) {
        // If it's still empty, keep the placeholder or show a "No Recent Trades" message
        if (list.children.length === 0 || list.querySelector('.empty-state')) {
            list.innerHTML = '<div class="empty-state">Menunggu aktivitas market...</div>';
        }
        return;
    }
    
    list.innerHTML = '';
    data.trades.slice(0, 30).forEach(t => {
        const row = document.createElement('div');
        row.className = `trade-row side-${t.side}`;
        const time = new Date(t.time).toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        const price = parseFloat(t.price).toLocaleString('id-ID');
        const amountVal = parseFloat(t.amount);
        const amount = amountVal < 1 ? amountVal.toFixed(8) : amountVal.toFixed(4);
        row.innerHTML = `
            <span class="trade-time">${time}</span>
            <span class="trade-price">${price}</span>
            <span class="trade-amount">${amount}</span>
        `;
        list.appendChild(row);
    });
}
