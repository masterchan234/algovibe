/* AlgoVibe Dashboard — script.js (Pristine Version) */
'use strict';

let chart, activePair = null, activeTimeframe = 'LIVE', lastStats = {}, lastPrices = {}, lastRadarData = [], _notifEnabled = false;
let marketSearchQuery = '';
let _logCache = new Set();
const socket = typeof io !== 'undefined' ? io() : null;

// ── Indicator Visibility State ─────────────────────────────
const indState = { ma7: true, ma25: true, bb: true };

function toggleIndicator(key) {
    indState[key] = !indState[key];
    // Update checkmark UI
    const chk = document.getElementById('ind-chk-' + key);
    if (chk) chk.classList.toggle('hidden', !indState[key]);
    // Dim row
    if (chk) chk.closest('.ind-menu-item').classList.toggle('inactive', !indState[key]);
    // Redraw chart
    if (chart) chart.draw();
}

function toggleIndMenu() {
    const menu = document.getElementById('ind-menu');
    if (!menu) return;
    const isOpen = menu.style.display !== 'none';
    menu.style.display = isOpen ? 'none' : 'block';
    const btn = document.getElementById('ind-menu-btn');
    if (btn) btn.classList.toggle('active', !isOpen);
}

// Close dropdown when clicking outside
document.addEventListener('click', e => {
    const wrap = e.target.closest('.ind-dropdown-wrap');
    if (!wrap) {
        const menu = document.getElementById('ind-menu');
        const btn = document.getElementById('ind-menu-btn');
        if (menu) menu.style.display = 'none';
        if (btn) btn.classList.remove('active');
    }
});

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
        this.axisCanvas = document.getElementById('price-axis-canvas');
        this.axisCtx = this.axisCanvas ? this.axisCanvas.getContext('2d') : null;
        this.tip = document.getElementById(tId);
        this.data = []; this._ohlc = [];
        this.mode = 'area';
        this.hI = -1; this.hC = -1;
        this._lastMin = 0; this._lastMax = 0;
        new ResizeObserver(() => this.resize()).observe(this.canvas.parentElement);
        this.canvas.addEventListener('mousemove', e => this._move(e));
        this.canvas.addEventListener('mouseleave', () => {
            this.hI = this.hC = -1;
            this.hY = -1;
            this.tip.style.display = 'none';
            this.draw();
        });
    }
    resize() {
        const dpr = devicePixelRatio || 1;
        // Main chart canvas
        const r = this.canvas.getBoundingClientRect();
        this.canvas.width = r.width * dpr; this.canvas.height = r.height * dpr;
        this.ctx.scale(dpr, dpr);
        this.canvas.style.width = r.width + 'px'; this.canvas.style.height = r.height + 'px';
        // Axis canvas
        if (this.axisCanvas) {
            const ra = this.axisCanvas.getBoundingClientRect();
            this.axisCanvas.width = ra.width * dpr; this.axisCanvas.height = ra.height * dpr;
            this.axisCtx.scale(dpr, dpr);
            this.axisCanvas.style.width = ra.width + 'px'; this.axisCanvas.style.height = ra.height + 'px';
        }
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
    _stddev(arr, sma, n) {
        return arr.map((_, i) => {
            if (i < n - 1) return null;
            const s = arr.slice(i - n + 1, i + 1);
            const m = sma[i];
            return Math.sqrt(s.reduce((a, v) => a + (v - m) ** 2, 0) / n);
        });
    }
    // Gambar MA + Bollinger Bands overlay
    _drawOverlays(vals, toY, padX, stepX, W, H, pad, isLight) {
        const ctx = this.ctx;
        const n = vals.length;
        if (n < 7) return;

        // Clip semua drawing ke dalam area chart — BB tidak meluber ke luar
        ctx.save();
        ctx.beginPath();
        ctx.rect(0, pad, W, H - pad * 2);
        ctx.clip();

        const drawLine = (smaArr, color, lw = 1.2) => {
            ctx.beginPath();
            ctx.strokeStyle = color; ctx.lineWidth = lw; ctx.lineJoin = 'round';
            let started = false;
            smaArr.forEach((v, i) => {
                if (v === null) return;
                const x = padX + i * stepX, y = toY(v);
                if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
            });
            ctx.stroke();
        };

        // ─ MA7 ─
        if (indState.ma7 && n >= 7) {
            drawLine(this._sma(vals, 7), 'rgba(245,158,11,0.85)', 1.2);
        }

        // ─ MA25 ─
        if (indState.ma25 && n >= 25) {
            drawLine(this._sma(vals, 25), 'rgba(168,85,247,0.85)', 1.2);
        }

        // ─ Bollinger Bands (MA20 ± 2σ) ─
        if (indState.bb && n >= 20) {
            const ma20 = this._sma(vals, 20);
            const sigma = this._stddev(vals, ma20, 20);
            const upper = ma20.map((v, i) => v !== null ? v + 2 * sigma[i] : null);
            const lower = ma20.map((v, i) => v !== null ? v - 2 * sigma[i] : null);
            const ptUp = [], ptLo = [];
            upper.forEach((v, i) => { if (v !== null) ptUp.push({ x: padX + i * stepX, y: toY(v) }); });
            lower.forEach((v, i) => { if (v !== null) ptLo.push({ x: padX + i * stepX, y: toY(v) }); });
            if (ptUp.length > 1) {
                ctx.beginPath();
                ctx.moveTo(ptUp[0].x, ptUp[0].y);
                ptUp.forEach(p => ctx.lineTo(p.x, p.y));
                [...ptLo].reverse().forEach(p => ctx.lineTo(p.x, p.y));
                ctx.closePath();
                ctx.fillStyle = isLight ? 'rgba(6,182,212,0.05)' : 'rgba(6,182,212,0.07)';
                ctx.fill();
            }
            drawLine(upper, 'rgba(6,182,212,0.5)', 0.8);
            drawLine(lower, 'rgba(6,182,212,0.5)', 0.8);
            drawLine(ma20, 'rgba(6,182,212,0.35)', 0.8);
        }

        ctx.restore(); // lepas clip

        // ─ Legend di luar clip (tetap terlihat di pojok kiri atas) ─
        ctx.font = 'bold 9px JetBrains Mono, monospace';
        const legends = [
            ...(indState.ma7 && n >= 7 ? [{ label: 'MA7', color: 'rgba(245,158,11,0.9)' }] : []),
            ...(indState.ma25 && n >= 25 ? [{ label: 'MA25', color: 'rgba(168,85,247,0.9)' }] : []),
            ...(indState.bb && n >= 20 ? [{ label: 'BB20', color: 'rgba(6,182,212,0.9)' }] : []),
        ];
        legends.forEach((l, i) => {
            const lx = padX + 6 + i * 44;
            ctx.fillStyle = l.color;
            ctx.fillRect(lx, 8, 14, 2);
            ctx.fillText(l.label, lx + 16, 12);
        });
    }
    _fmt(v) { return fmtIDR(v); }
    _fmtShort(v) {
        if (v >= 1e12) return (v / 1e12).toFixed(2) + 'T';
        if (v >= 1e9) return (v / 1e9).toFixed(1) + 'M';
        if (v >= 1e6) return (v / 1e6).toFixed(1) + 'jt';
        if (v >= 1e3) return (v / 1e3).toFixed(1) + 'rb';
        if (v >= 1) return v.toLocaleString('id-ID', { maximumFractionDigits: 0 });
        return v.toFixed(6);
    }
    _tipPos(x, W) { this.tip.style.display = 'block'; const tw = this.tip.getBoundingClientRect().width; this.tip.style.left = (x + 12 + tw > W ? x - tw - 12 : x + 12) + 'px'; this.tip.style.top = '8px'; }
    _drawPriceAxis(minV, maxV, pad, isLight) {
        if (!this.axisCanvas || !this.axisCtx) return;
        const ctx = this.axisCtx;
        const W = this.axisCanvas.getBoundingClientRect().width || 72;
        const H = this.axisCanvas.getBoundingClientRect().height || this.canvas.getBoundingClientRect().height;
        ctx.clearRect(0, 0, W, H);

        const range = maxV - minV || 1;
        const dH = H - pad * 2;
        const toY = v => H - pad - ((v - minV) / range) * dH;

        const mainCtx = this.ctx;
        const mW = this.canvas.getBoundingClientRect().width;
        const dimColor = isLight ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.04)';
        const tickColor = isLight ? 'rgba(0,0,0,0.15)' : 'rgba(255,255,255,0.12)';
        const lblColor = isLight ? '#94a3b8' : '#3a3d52';

        // ── Nice-step grid lines (TradingView style) ──
        // Hitung step yang rounded ke angka bulat sesuai magnitude range
        const niceStep = (() => {
            const rawStep = range / 6;
            const mag = Math.pow(10, Math.floor(Math.log10(rawStep)));
            const norm = rawStep / mag;
            const nice = norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10;
            return nice * mag;
        })();
        const firstTick = Math.ceil(minV / niceStep) * niceStep;

        ctx.font = '10px JetBrains Mono, monospace';
        ctx.textAlign = 'left';

        for (let v = firstTick; v <= maxV + niceStep * 0.01; v += niceStep) {
            const y = toY(v);
            // skip jika terlalu dekat ke tepi (akan bentrok dengan H/L badge)
            if (y < 14 || y > H - 14) continue;
            mainCtx.beginPath();
            mainCtx.setLineDash([3, 6]);
            mainCtx.moveTo(0, y); mainCtx.lineTo(mW, y);
            mainCtx.strokeStyle = dimColor; mainCtx.lineWidth = 1;
            mainCtx.stroke(); mainCtx.setLineDash([]);
            ctx.beginPath();
            ctx.moveTo(0, y); ctx.lineTo(6, y);
            ctx.strokeStyle = tickColor; ctx.lineWidth = 1; ctx.stroke();
            ctx.fillStyle = lblColor;
            ctx.fillText(this._fmtShort(v), 10, y + 3.5);
        }

        // ── HIGH pin — exact maxV (green) ──
        const highY = toY(maxV);
        mainCtx.beginPath(); mainCtx.setLineDash([2, 5]);
        mainCtx.moveTo(0, highY); mainCtx.lineTo(mW, highY);
        mainCtx.strokeStyle = 'rgba(16,185,129,0.35)'; mainCtx.lineWidth = 1;
        mainCtx.stroke(); mainCtx.setLineDash([]);
        const hBoxH = 15, hBoxY = highY - hBoxH / 2;
        ctx.fillStyle = 'rgba(16,185,129,0.2)'; ctx.strokeStyle = 'rgba(16,185,129,0.5)'; ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.roundRect ? ctx.roundRect(0, hBoxY, W - 2, hBoxH, 2) : ctx.rect(0, hBoxY, W - 2, hBoxH);
        ctx.fill(); ctx.stroke();
        ctx.fillStyle = '#10b981'; ctx.font = 'bold 9px JetBrains Mono, monospace'; ctx.textAlign = 'left';
        ctx.fillText('H ' + this._fmtShort(maxV), 4, highY + 3.5);

        // ── LOW pin — exact minV (red) ──
        const lowY = toY(minV);
        mainCtx.beginPath(); mainCtx.setLineDash([2, 5]);
        mainCtx.moveTo(0, lowY); mainCtx.lineTo(mW, lowY);
        mainCtx.strokeStyle = 'rgba(239,68,68,0.35)'; mainCtx.lineWidth = 1;
        mainCtx.stroke(); mainCtx.setLineDash([]);
        const lBoxH = 15, lBoxY = lowY - lBoxH / 2;
        ctx.fillStyle = 'rgba(239,68,68,0.18)'; ctx.strokeStyle = 'rgba(239,68,68,0.5)'; ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.roundRect ? ctx.roundRect(0, lBoxY, W - 2, lBoxH, 2) : ctx.rect(0, lBoxY, W - 2, lBoxH);
        ctx.fill(); ctx.stroke();
        ctx.fillStyle = '#ef4444'; ctx.font = 'bold 9px JetBrains Mono, monospace'; ctx.textAlign = 'left';
        ctx.fillText('L ' + this._fmtShort(minV), 4, lowY + 3.5);

        // ── Last price highlight box ──
        const lastVal = this.mode === 'candle' && this._ohlc.length
            ? this._ohlc[this._ohlc.length - 1].close
            : (this.data.length ? this.data[this.data.length - 1].value : null);

        if (lastVal !== null) {
            const lastY = toY(lastVal);
            const boxH = 16, boxY = lastY - boxH / 2;
            const isUp = this.data.length < 2 || lastVal >= this.data[0].value;
            const boxColor = isUp ? '#10b981' : '#ef4444';
            ctx.fillStyle = boxColor;
            ctx.beginPath();
            ctx.roundRect ? ctx.roundRect(0, boxY, W - 2, boxH, 3) : ctx.rect(0, boxY, W - 2, boxH);
            ctx.fill();
            ctx.fillStyle = '#ffffff'; ctx.font = 'bold 10px JetBrains Mono, monospace'; ctx.textAlign = 'center';
            ctx.fillText(this._fmtShort(lastVal), W / 2 - 1, lastY + 3.5);
            mainCtx.beginPath(); mainCtx.setLineDash([4, 4]);
            mainCtx.moveTo(0, lastY); mainCtx.lineTo(mW, lastY);
            mainCtx.strokeStyle = boxColor; mainCtx.lineWidth = 1; mainCtx.globalAlpha = 0.5;
            mainCtx.stroke(); mainCtx.setLineDash([]); mainCtx.globalAlpha = 1;
        }

        // ── CROSSHAIR Price Label ──
        if (this.hY !== undefined && this.hY !== -1) {
            const y = this.hY;
            const price = this._priceAtY(y);
            const boxH = 18, boxY = y - boxH / 2;
            ctx.fillStyle = isLight ? '#1e293b' : '#e2e8f0';
            ctx.beginPath();
            ctx.roundRect ? ctx.roundRect(0, boxY, W, boxH, 2) : ctx.rect(0, boxY, W, boxH);
            ctx.fill();
            ctx.fillStyle = isLight ? '#ffffff' : '#0f172a';
            ctx.font = 'bold 10px JetBrains Mono, monospace'; ctx.textAlign = 'center';
            ctx.fillText(this._fmtShort(price), W / 2, y + 3.5);
        }
    }
    _priceAtY(y) {
        if (!this._lastMin && !this._lastMax) return 0;
        const r = this.canvas.getBoundingClientRect(), H = r.height;
        const pad = H * 0.1, dH = H - pad * 2;
        const pct = 1 - (y - pad) / dH;
        return this._lastMin + pct * (this._lastMax - this._lastMin);
    }
    _move(e) {
        if (!this.data.length) return;
        const r = this.canvas.getBoundingClientRect();
        const x = e.clientX - r.left, y = e.clientY - r.top, W = r.width;
        this.hY = y;

        if (this.mode === 'candle') {
            const o = this._ohlc; if (!o.length) return;
            const padX = 12;
            const slotW = (W - padX * 2) / o.length;
            let i = Math.max(0, Math.min(Math.floor((x - padX) / slotW), o.length - 1));
            if (this.hC !== i) {
                this.hC = i; this.draw(); const c = o[i];
                this.tip.innerHTML = `<span style="color:var(--text-secondary)">O</span> ${this._fmt(c.open)} <span style="color:var(--green)">H</span> ${this._fmt(c.high)}<br><span style="color:var(--red)">L</span> ${this._fmt(c.low)} <span style="color:var(--text-primary)">C</span> ${this._fmt(c.close)}`;
                this._tipPos(x, W);
            }
        } else {
            const padX = 12;
            const step = (W - padX * 2) / Math.max(1, this.data.length - 1);
            const xi = Math.max(0, Math.min(Math.round((x - padX) / step), this.data.length - 1));
            if (this.hI !== xi) { this.hI = xi; this.draw(); this.tip.textContent = fmtIDR(this.data[xi].value); this._tipPos(x, W); }
        }
        this.draw(); // Redraw for horizontal crosshair
    }
    draw() { if (this.mode === 'candle') this._drawCandle(); else this._drawArea(); }
    _drawArea() {
        const r = this.canvas.getBoundingClientRect(), W = r.width, H = r.height, ctx = this.ctx;
        ctx.clearRect(0, 0, W, H); if (!this.data.length) return;
        const padX = 12;
        const vals = this.data.map(d => d.value), minV = Math.min(...vals), maxV = Math.max(...vals), range = maxV - minV || 1;
        this._lastMin = minV; this._lastMax = maxV;
        const pad = H * .1, dH = H - pad * 2, stepX = (W - padX * 2) / Math.max(1, this.data.length - 1);
        const isLight = document.documentElement.getAttribute('data-theme') === 'light';
        const toY = v => H - pad - ((v - minV) / range) * dH;
        const pts = vals.map((v, i) => ({ x: padX + i * stepX, y: toY(v) }));

        // Horizontal Crosshair Line
        if (this.hY !== undefined && this.hY !== -1) {
            ctx.beginPath(); ctx.setLineDash([4, 6]); ctx.moveTo(0, this.hY); ctx.lineTo(W, this.hY);
            ctx.strokeStyle = isLight ? 'rgba(0,0,0,0.08)' : 'rgba(255,255,255,0.06)'; ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([]);
        }

        // Gradient fill
        ctx.beginPath(); ctx.moveTo(pts[0].x, pts[0].y); pts.forEach(p => ctx.lineTo(p.x, p.y));
        ctx.lineTo(W, H); ctx.lineTo(0, H); ctx.closePath();
        const g = ctx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, 'rgba(59,130,246,.15)'); g.addColorStop(1, 'rgba(59,130,246,0)');
        ctx.fillStyle = g; ctx.fill();
        // MA + BB overlays (di bawah line utama)
        this._drawOverlays(vals, toY, padX, stepX, W, H, pad, isLight);
        // Main line (di atas overlay)
        ctx.beginPath(); pts.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
        ctx.strokeStyle = '#3b82f6'; ctx.lineWidth = 1.5; ctx.lineJoin = 'round'; ctx.stroke();
        // Price axis
        this._drawPriceAxis(minV, maxV, pad, isLight);
        // Vertical Crosshair dot
        if (this.hI >= 0 && this.hI < pts.length) {
            const p = pts[this.hI];
            ctx.beginPath(); ctx.setLineDash([3, 4]); ctx.moveTo(p.x, 0); ctx.lineTo(p.x, H);
            ctx.strokeStyle = isLight ? 'rgba(0,0,0,.1)' : 'rgba(255,255,255,.1)'; ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([]);
            ctx.beginPath(); ctx.arc(p.x, p.y, 4.5, 0, Math.PI * 2); ctx.fillStyle = isLight ? '#ffffff' : '#08090b'; ctx.fill();
            ctx.strokeStyle = '#3b82f6'; ctx.lineWidth = 2; ctx.stroke();
        }
    }
    _drawCandle() {
        const r = this.canvas.getBoundingClientRect(), W = r.width, H = r.height, ctx = this.ctx;
        ctx.clearRect(0, 0, W, H); const ohlc = this._ohlc; if (!ohlc.length) return;
        const padX = 12;
        const allP = ohlc.flatMap(c => [c.high, c.low]), minV = Math.min(...allP), maxV = Math.max(...allP), range = maxV - minV || 1;
        this._lastMin = minV; this._lastMax = maxV;
        const pad = H * .1, dH = H - pad * 2;
        const toY = v => H - pad - ((v - minV) / range) * dH;
        const drawW = W - padX * 2;
        const slotW = drawW / ohlc.length, bW = Math.max(2, slotW * .55);
        const isLight = document.documentElement.getAttribute('data-theme') === 'light';

        // Horizontal Crosshair Line
        if (this.hY !== undefined && this.hY !== -1) {
            ctx.beginPath(); ctx.setLineDash([4, 6]); ctx.moveTo(0, this.hY); ctx.lineTo(W, this.hY);
            ctx.strokeStyle = isLight ? 'rgba(0,0,0,0.08)' : 'rgba(255,255,255,0.06)'; ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([]);
        }

        // Hover highlight + Vertical Crosshair
        if (this.hC >= 0) {
            const cx = padX + (this.hC + .5) * slotW;
            ctx.fillStyle = isLight ? 'rgba(0,0,0,.04)' : 'rgba(255,255,255,.03)';
            ctx.fillRect(padX + this.hC * slotW, 0, slotW, H);
            ctx.beginPath(); ctx.setLineDash([3, 4]); ctx.moveTo(cx, 0); ctx.lineTo(cx, H);
            ctx.strokeStyle = isLight ? 'rgba(0,0,0,.08)' : 'rgba(255,255,255,.06)'; ctx.stroke(); ctx.setLineDash([]);
        }
        // MA + BB overlays (close prices, di bawah candle)
        const closes = ohlc.map(c => c.close);
        const stepX = drawW / Math.max(1, ohlc.length - 1);
        this._drawOverlays(closes, toY, padX, stepX, W, H, pad, isLight);
        // Candles
        ohlc.forEach((c, i) => {
            const cx = padX + (i + .5) * slotW, bull = c.close >= c.open, col = bull ? '#10b981' : '#ef4444';
            ctx.strokeStyle = col; ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(cx, toY(c.high)); ctx.lineTo(cx, toY(c.low)); ctx.stroke();
            const bT = toY(Math.max(c.open, c.close)), bH = Math.max(1.5, toY(Math.min(c.open, c.close)) - bT);
            ctx.fillStyle = bull ? 'rgba(16,185,129,.75)' : 'rgba(239,68,68,.75)';
            ctx.fillRect(cx - bW / 2, bT, bW, bH); ctx.strokeStyle = col; ctx.lineWidth = .8; ctx.strokeRect(cx - bW / 2, bT, bW, bH);
        });
        // Price axis
        this._drawPriceAxis(minV, maxV, pad, isLight);
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

    // Reset Market Activity List (Instant UI)
    const mtList = document.getElementById('market-trades-list');
    if (mtList) mtList.innerHTML = '<div class="empty-state">Menunggu data market baru...</div>';

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

        // Update Chart Realtime jika data untuk pair aktif masuk
        if (data && data.length) {
            lastRadarData = data;
            const activeData = data.find(d => d.pair === activePair);
            if (activeData && chart) {
                chart.appendPoint(activePair, activeData.price);
            }
        }

        if (!data || !data.length) return;

        // ── ticker-list (Radar Watchlist) ──
        const list = document.getElementById('ticker-list');
        const cnt = document.getElementById('watchlist-count');
        if (cnt) cnt.textContent = data.length;

        if (list) {
            const isSearching = marketSearchQuery.length > 0;
            list.style.display = isSearching ? 'block' : 'none';

            if (isSearching) {
                list.innerHTML = '';
                const query = marketSearchQuery.toLowerCase();
                const filtered = data.filter(t => t.pair.toLowerCase().includes(query))
                    .sort((a, b) => a.pair.localeCompare(b.pair));

                if (filtered.length === 0) {
                    list.innerHTML = '<div class="empty-state">Koin tidak ditemukan</div>';
                } else {
                    filtered.forEach(t => {
                        const isUp = t.change >= 0, diff = (isUp ? '+' : '') + t.change.toFixed(2) + '%';
                        const priceHtml = fmtIDR(t.price);

                        const conf = t.tech_confidence || 0;
                        const verd = t.tech_verdict || 'HOLD';
                        let badgeHtml = '';
                        if (conf >= 60) {
                            const bCls = verd === 'BUY' ? 'sig-buy' : (verd === 'SELL' ? 'sig-sell' : 'sig-hold');
                            const bIcon = verd === 'BUY' ? '📈' : (verd === 'SELL' ? '📉' : '⏸');
                            badgeHtml = `<span class="sig-badge ${bCls}" title="${t.tech_reason || ''}">${bIcon} ${verd} ${conf}%</span>`;
                        }

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
                            <div class="ticker-main"><b>${formatPair(t.pair)}</b> <span>${priceHtml}</span></div>
                            <div class="ticker-side">
                                <span class="${isUp ? 'up' : 'down'}">${diff}</span>
                                ${badgeHtml}
                            </div>`;

                        const lastP = lastPrices[t.pair];
                        if (lastP && lastP !== t.price) { flashEl(item, t.price > lastP ? 'up' : 'down'); }
                        lastPrices[t.pair] = t.price;
                        list.appendChild(item);
                    });
                }
            }
        }

        // ── Market Pulse (Gainers / Losers) ──
        const gList = document.getElementById('gainers-list'), lList = document.getElementById('losers-list');
        const tf = 'change_24h'; // Selalu gunakan data realtime 24h
        if (gList && lList) {
            gList.innerHTML = ''; lList.innerHTML = '';
            // Senior Engineer Fix: Filter out zero-change coins from Pulse lists
            const gainers = data.filter(t => (t[tf] || 0) > 0.01).sort((a, b) => (b[tf] || 0) - (a[tf] || 0)).slice(0, 10);
            const losers = data.filter(t => (t[tf] || 0) < -0.01).sort((a, b) => (a[tf] || 0) - (b[tf] || 0)).slice(0, 10);
            const renderItem = (t, container, isUp) => {
                const item = document.createElement('div'); item.className = 'pulse-item ' + (isUp ? 'up' : 'down');
                item.onclick = () => selectPair(t.pair, t.trend, t.prediction);
                item.innerHTML = `<span>${formatPair(t.pair)}</span><b>${((t[tf] || 0) >= 0 ? '+' : '') + (t[tf] || 0).toFixed(1)}%</b>`;
                container.appendChild(item);
            };
            gainers.forEach(t => renderItem(t, gList, true));
            losers.forEach(t => renderItem(t, lList, false));
        }

        // Momentum Radar (Filter Signal-Only)
        const tList = document.getElementById('trending-list');
        if (tList) {
            tList.innerHTML = '';
            
            // Senior UX Fix: Filter koin yang benar-benar aktif (Score > 30)
            const radar = data.filter(t => (t.score || 0) > 30).sort((a, b) => (b.score || 0) - (a.score || 0));
            
            if (radar.length === 0) {
                tList.innerHTML = '<div style="padding:20px; text-align:center; color:var(--text-dim); font-size:0.8rem;">Menunggu Momentum Pasar...</div>';
            }

            radar.forEach(t => {
                const s = t.score || 0;
                let label = '🟡 Accumulating';
                let color = '#eccc68'; 

                if (s > 80) { label = '🔴 Hyper-Drive';  color = '#ff4757'; }
                else if (s > 50) { label = '🟠 Momentum';    color = '#ffa502'; }

                // LOGIKA SIGNAL EXPIRY (120 Detik)
                let sig = t.signal || 'HOLD';
                let sigColor = 'var(--text-dim)';
                
                // Cek kesegaran sinyal
                const now = new Date();
                const lastUpdate = t.updated_at ? new Date(t.updated_at + 'Z') : null; // Force UTC
                const diffSec = lastUpdate ? (now - lastUpdate) / 1000 : 999;
                
                if (diffSec > 120) {
                    sig = 'WAIT';
                    sigColor = 'var(--text-dim)';
                } else {
                    if (sig === 'BUY') sigColor = '#2ecc71';
                    if (sig === 'SELL') sigColor = '#e74c3c';
                }

                const chg = t.change || 0;
                const chgClass = chg >= 0 ? 'up' : 'down';
                const chgSign = chg >= 0 ? '+' : '';

                const item = document.createElement('div'); 
                item.className = 'trending-item';
                item.style.padding = '10px 12px';
                item.style.borderBottom = '1px solid var(--border-soft)';
                item.onclick = () => selectPair(t.pair, t.trend, t.prediction);
                
                item.innerHTML = `
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                        <span style="font-weight:600; color:var(--text-bright); font-size:0.9rem;">${formatPair(t.pair)}</span>
                        <span style="font-weight:bold; color:${sigColor}; font-size:0.8rem; border:1px solid ${sigColor}44; padding:2px 6px; border-radius:4px; min-width:45px; text-align:center;">${sig}</span>
                    </div>
                    <div style="display:flex; justify-content:space-between; align-items:center; font-size:0.75rem;">
                        <span style="color:var(--text-dim);"><span class="${chgClass}">${chgSign}${chg.toFixed(2)}%</span> | Vol: ${fmtShortIDR(t.vol_idr || 0)}</span>
                        <span style="color:${color}; font-weight:600;">${label}</span>
                    </div>
                `;
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
        const volCrypto = formatCrypto(t.amount_coin);
        const volIDR = fmtIDR(t.amount_idr);

        // DNA Rendering (AI & Whale context)
        let dnaHtml = '<div style="display:flex; flex-direction:column; gap:3px; align-items:center;">';
        if (t.ai_prob && t.ai_prob > 0.7) {
            dnaHtml += `<span title="High AI Prob: ${Math.round(t.ai_prob * 100)}%" style="font-size:12px; filter:drop-shadow(0 0 2px var(--blue))">🧠</span>`;
        }
        if (t.whale_boost && t.whale_boost > 0) {
            dnaHtml += `<span title="Whale Activity Detected" style="font-size:12px; filter:drop-shadow(0 0 2px var(--yellow))">🐋</span>`;
        }
        if (!t.ai_prob && !t.whale_boost) {
            dnaHtml += `<span style="font-size:10px; opacity:0.2">--</span>`;
        }
        dnaHtml += '</div>';

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
                <td>${dnaHtml}</td>
                <td>
                    <div style="line-height:1.2">
                        <b>${formatPair(t.pair)}</b><br>
                        <span class="muted" style="font-size:9px">Beli: ${fmtIDR(t.entry_price)}</span><br>
                        <span class="muted" style="font-size:9px">Live: ${fmtIDR(t.exit_price || t.last_price)}</span>
                    </div>
                </td>
                <td>
                    <div style="line-height:1.2">
                        <span style="font-weight:600; font-size:10px">${volCrypto}</span><br>
                        <span class="muted" style="font-size:9.5px">${volIDR}</span>
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
                <td>${dnaHtml}</td>
                <td>
                    <div style="line-height:1.2">
                        <b>${formatPair(t.pair)}</b><br>
                        <span class="muted" style="font-size:9px">Beli: ${fmtIDR(t.entry_price)}</span><br>
                        <span class="muted" style="font-size:9px">Jual: ${fmtIDR(t.exit_price || t.last_price)}</span>
                    </div>
                </td>
                <td>
                    <div style="line-height:1.2">
                        <span style="font-weight:600; font-size:10px">${volCrypto}</span><br>
                        <span class="muted" style="font-size:9.5px">${volIDR}</span>
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
    const container = document.getElementById('ai-logs');
    if (!container) return;

    // Filter log baru saja agar tidak duplikat di UI
    const newItems = data.filter(log => {
        const key = log.created_at + log.side + log.pair + log.price;
        if (_logCache.has(key)) return false;
        _logCache.add(key);
        return true;
    }).reverse(); // Reverse agar urutan prepend benar (terlama dulu diproses ke prepend)

    if (newItems.length === 0) return;

    const fragment = document.createDocumentFragment();

    newItems.forEach(log => {
        const entry = document.createElement('div');
        const level = (log.level || 'INFO').toUpperCase();
        entry.className = `log-entry level-${level.toLowerCase()}`;

        const timeStr = new Date(log.created_at).toLocaleTimeString('id-ID', {
            hour: '2-digit', minute: '2-digit', second: '2-digit'
        });

        // Mapping Ikon Dinamis
        let icon = '🔹';
        if (log.side.includes('AI_ORACLE')) icon = '🧠';
        else if (log.side.includes('PREDICTION')) icon = '🎯';
        else if (log.side.includes('WHALE')) icon = '🐋';
        else if (log.side.includes('REGIME')) icon = '📡';
        else if (log.side.includes('PUMP')) icon = '⚠️';
        else if (log.side.includes('ORACLE')) icon = '🔮';

        entry.innerHTML = `
            <div class="log-header">
                <span class="log-time">${timeStr}</span>
                <span class="log-icon">${icon}</span>
                <span class="log-side">${log.side}</span>
                <span class="log-pair">${formatPair(log.pair)}</span>
            </div>
            <div class="log-msg">${log.price}</div>
        `;

        fragment.appendChild(entry);
    });

    container.prepend(fragment);

    // Limit log display (Senior JS: maintain DOM performance)
    while (container.children.length > 40) {
        container.lastChild.remove();
    }
}

// ── Helpers ──────────────────────────────────────────────────
function fmtIDR(n, s = false) {
    if (n === undefined || n === null || isNaN(n)) return 'Rp 0';
    const a = Math.abs(n);
    // Senior Logic: Use maximumFractionDigits: 0 for large numbers, 2 for smaller ones
    const v = new Intl.NumberFormat('id-ID', {
        style: 'currency',
        currency: 'IDR',
        maximumFractionDigits: a < 100 ? 2 : 0
    }).format(a);
    const prefix = s ? (n >= 0 ? '+' : '−') : (n < 0 ? '−' : '');
    return prefix + v;
}
function formatNumber(v, d = 0) {
    if (v === undefined || v === null || isNaN(v)) return '--';
    // Digunakan untuk harga koin yang mungkin sangat kecil
    if (v < 1) return v.toFixed(6);
    if (v < 100) return v.toFixed(2);
    return Math.round(v).toLocaleString('id-ID');
}
function formatCrypto(v) {
    if (v === undefined || v === null || isNaN(v)) return '0';
    return v < 1 ? v.toFixed(6) : v.toFixed(2);
}
function fmtShortIDR(v) {
    if (v === undefined || v === null || isNaN(v)) return 'Rp 0';
    if (v >= 1e12) return 'Rp ' + (v / 1e12).toFixed(2) + 'T';
    if (v >= 1e9) return 'Rp ' + (v / 1e9).toFixed(1) + 'M';
    if (v >= 1e6) return 'Rp ' + (v / 1e6).toFixed(1) + 'Jt';
    if (v >= 1e3) return 'Rp ' + (v / 1e3).toFixed(1) + 'Rb';
    return fmtIDR(v);
}
function formatPair(pair) {
    if (!pair) return '';
    return pair.toLowerCase().replace('_idr', '').replace('idr', '').toUpperCase();
}
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

// ── Mobile Navigation Logic ──
function switchMobileTab(tab, el) {
    // Haptic Feedback for Premium Feel
    if (window.navigator && window.navigator.vibrate) {
        window.navigator.vibrate(10);
    }

    // UI Update
    document.querySelectorAll('.mobile-nav-item').forEach(item => item.classList.remove('active'));
    el.classList.add('active');

    // Body Class Update
    document.body.classList.remove('tab-market', 'tab-chart', 'tab-trades');
    document.body.classList.add('tab-' + tab);

    // Trigger Chart Resize if switching to chart
    if (tab === 'chart' && chart) {
        setTimeout(() => chart.resize(), 100);
    }
    
    closeAllSidebars();
}

function toggleSidebar(side) {
    const selector = side === 'left' ? '.left-sidebar' : '.right-sidebar';
    const el = document.querySelector(selector);
    const overlay = document.getElementById('sidebar-overlay');
    
    if (el) {
        const isOpen = el.classList.toggle('show');
        if (overlay) overlay.classList.toggle('show', isOpen);
    }
}

function closeAllSidebars() {
    document.querySelectorAll('.left-sidebar, .right-sidebar').forEach(s => s.classList.remove('show'));
    const overlay = document.getElementById('sidebar-overlay');
    if (overlay) overlay.classList.remove('show');
}

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
    
    // Default Mobile State
    document.body.classList.add('tab-chart');

    // Senior Search Implementation
    const searchInput = document.getElementById('market-search');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            marketSearchQuery = e.target.value.trim();
            refreshWatchlist();
        });
        // UI Polish: Neon purple glow on focus
        searchInput.addEventListener('focus', () => {
            searchInput.style.borderColor = 'var(--purple)';
            searchInput.style.boxShadow = '0 0 8px rgba(168, 85, 247, 0.2)';
        });
        searchInput.addEventListener('blur', () => {
            searchInput.style.borderColor = 'var(--border)';
            searchInput.style.boxShadow = 'none';
        });
    }

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

    if (!data || !data.pair || !activePair || data.pair.toLowerCase() !== activePair.toLowerCase()) return;
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

        // Senior JS Polish: Dynamic formatting for huge/tiny numbers
        const pVal = parseFloat(t.price);
        const price = fmtIDR(pVal);

        const amountVal = parseFloat(t.amount);
        const amount = formatCrypto(amountVal);

        row.innerHTML = `
            <span class="trade-time">${time}</span>
            <span class="trade-price">${price}</span>
            <span class="trade-amount">${amount}</span>
        `;
        list.appendChild(row);
    });
}
