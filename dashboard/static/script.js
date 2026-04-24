let chart;
let activePair = 'BTCCIDR';
let lastStats = {};
let lastPrices = {};

// Custom Canvas Area Chart Implementation
class CustomAreaChart {
    constructor(canvasId, tooltipId) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.tooltip = document.getElementById(tooltipId);
        this.data = [];
        this.hoverIndex = -1;
        
        // Handle resizing
        this.resizeObserver = new ResizeObserver(() => this.resize());
        this.resizeObserver.observe(this.canvas.parentElement);
        
        // Interactivity
        this.canvas.addEventListener('mousemove', (e) => this.handleMouseMove(e));
        this.canvas.addEventListener('mouseleave', () => {
            this.hoverIndex = -1;
            this.tooltip.style.display = 'none';
            this.draw();
        });
    }

    resize() {
        const rect = this.canvas.parentElement.getBoundingClientRect();
        // High DPI support
        const dpr = window.devicePixelRatio || 1;
        this.canvas.width = rect.width * dpr;
        this.canvas.height = rect.height * dpr;
        this.ctx.scale(dpr, dpr);
        this.canvas.style.width = `${rect.width}px`;
        this.canvas.style.height = `${rect.height}px`;
        this.draw();
    }

    setData(data) {
        this.data = data;
        this.draw();
    }

    handleMouseMove(e) {
        if (!this.data.length) return;
        
        const rect = this.canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        
        const width = rect.width;
        const step = width / Math.max(1, this.data.length - 1);
        
        // Find nearest data point
        let index = Math.round(x / step);
        index = Math.max(0, Math.min(index, this.data.length - 1));
        
        if (this.hoverIndex !== index) {
            this.hoverIndex = index;
            this.draw(); // Redraw crosshair
            
            // Update Tooltip
            const point = this.data[index];
            const px = index * step;
            this.tooltip.innerText = `$${point.value.toLocaleString()}`;
            this.tooltip.style.display = 'block';
            
            // Position tooltip
            const tooltipRect = this.tooltip.getBoundingClientRect();
            let tooltipX = px + 10;
            if (tooltipX + tooltipRect.width > width) {
                tooltipX = px - tooltipRect.width - 10;
            }
            this.tooltip.style.left = `${tooltipX}px`;
            this.tooltip.style.top = '10px';
        }
    }

    draw() {
        const rect = this.canvas.parentElement.getBoundingClientRect();
        const width = rect.width;
        const height = rect.height;
        
        this.ctx.clearRect(0, 0, width, height);
        
        if (!this.data.length) return;

        const values = this.data.map(d => d.value);
        const minVal = Math.min(...values);
        const maxVal = Math.max(...values);
        const range = maxVal - minVal || 1;
        
        const padding = height * 0.1; // 10% padding top/bottom
        const drawHeight = height - (padding * 2);
        const stepX = width / Math.max(1, this.data.length - 1);

        // Path generation
        this.ctx.beginPath();
        this.ctx.moveTo(0, height);
        
        let points = [];
        this.data.forEach((d, i) => {
            const x = i * stepX;
            const normalizedY = (d.value - minVal) / range;
            const y = height - padding - (normalizedY * drawHeight);
            points.push({x, y});
            
            if (i === 0) this.ctx.lineTo(x, y);
            else this.ctx.lineTo(x, y);
        });

        this.ctx.lineTo(width, height);
        this.ctx.closePath();

        // Gradient Fill
        const gradient = this.ctx.createLinearGradient(0, 0, 0, height);
        gradient.addColorStop(0, 'rgba(59, 130, 246, 0.25)'); // #3b82f6 with opacity
        gradient.addColorStop(1, 'rgba(59, 130, 246, 0.0)');
        this.ctx.fillStyle = gradient;
        this.ctx.fill();

        // Draw Line
        this.ctx.beginPath();
        points.forEach((p, i) => {
            if (i === 0) this.ctx.moveTo(p.x, p.y);
            else this.ctx.lineTo(p.x, p.y);
        });
        this.ctx.strokeStyle = '#3b82f6';
        this.ctx.lineWidth = 2;
        this.ctx.lineJoin = 'round';
        this.ctx.stroke();

        // Draw Crosshair if hovering
        if (this.hoverIndex >= 0 && this.hoverIndex < points.length) {
            const p = points[this.hoverIndex];
            
            // Vertical line
            this.ctx.beginPath();
            this.ctx.setLineDash([4, 4]);
            this.ctx.moveTo(p.x, 0);
            this.ctx.lineTo(p.x, height);
            this.ctx.strokeStyle = 'rgba(255,255,255,0.15)';
            this.ctx.lineWidth = 1;
            this.ctx.stroke();
            this.ctx.setLineDash([]);
            
            // Dot
            this.ctx.beginPath();
            this.ctx.arc(p.x, p.y, 4, 0, Math.PI * 2);
            this.ctx.fillStyle = '#09090b';
            this.ctx.fill();
            this.ctx.lineWidth = 2;
            this.ctx.strokeStyle = '#3b82f6';
            this.ctx.stroke();
        }
    }
}


// Helper for flashing numbers via text color
function flashText(el, direction) {
    if (!el) return;
    const originalColor = el.style.color;
    el.style.color = direction === 'up' ? '#10b981' : '#ef4444';
    setTimeout(() => {
        el.style.color = originalColor || '';
    }, 600);
}

// Initialize System
function initSystem() {
    chart = new CustomAreaChart('custom-chart', 'chart-tooltip');
}

// Fetch Stats
async function updateStats() {
    try {
        const res = await fetch('/api/stats');
        const data = await res.json();
        
        const pnlEl = document.getElementById('daily-pnl');
        const formattedPnl = new Intl.NumberFormat('id-ID', { style: 'currency', currency: 'IDR' }).format(data.daily_pnl);
        
        if (pnlEl.innerText !== formattedPnl) {
            const dir = data.daily_pnl > (lastStats.daily_pnl || 0) ? 'up' : 'down';
            pnlEl.innerText = formattedPnl;
            flashText(pnlEl, dir);
        }
        
        document.getElementById('win-rate').innerText = `${data.win_rate.toFixed(1)}%`;
        document.getElementById('total-trades').innerText = data.total_trades_all_time;
        document.getElementById('market-regime').innerText = data.regime || "ANALYZING";
        
        const winRateEl = document.getElementById('win-rate');
        if (data.win_rate >= 50) {
            winRateEl.className = 'value up';
        } else {
            winRateEl.className = 'value down';
        }
        
        // Add Intel Logs if changed
        if (data.latest_whale && data.latest_whale !== lastStats.latest_whale) {
            addLog(`WHALE DETECTED: ${data.latest_whale}`);
        }
        if (data.latest_fake_pump && data.latest_fake_pump !== lastStats.latest_fake_pump) {
            addLog(`FAKE PUMP ALERT: ${data.latest_fake_pump}`);
        }
        
        lastStats = data;
    } catch (e) {
        console.error("Stats Error:", e);
    }
}

// Fetch Watchlist
async function updateWatchlist() {
    try {
        const res = await fetch('/api/radar');
        const data = await res.json();
        const list = document.getElementById('ticker-list');
        list.innerHTML = '';

        data.sort((a,b) => b.change - a.change);

        data.forEach(t => {
            const item = document.createElement('div');
            item.className = `ticker-item ${t.pair === activePair ? 'active' : ''}`;
            item.onclick = () => selectPair(t.pair, t.trend, t.prediction);
            
            const changeClass = t.change >= 0 ? 'up' : 'down';
            const priceStr = t.price < 100 ? t.price.toFixed(4) : t.price.toLocaleString();
            
            item.innerHTML = `
                <div class="ticker-info">
                    <span class="ticker-pair">${t.pair.toUpperCase()}</span>
                    <span class="ticker-price flash-text">${priceStr}</span>
                </div>
                <span class="ticker-change ${changeClass}">${t.change > 0 ? '+' : ''}${t.change.toFixed(2)}%</span>
            `;
            list.appendChild(item);
            
            // Flash text if price changed
            if (lastPrices[t.pair] && lastPrices[t.pair] !== t.price) {
                const dir = t.price > lastPrices[t.pair] ? 'up' : 'down';
                const priceEl = item.querySelector('.ticker-price');
                flashText(priceEl, dir);
            }
            lastPrices[t.pair] = t.price;
        });
    } catch (e) {}
}

// Select Pair & Load Chart
async function selectPair(pair, trend, prob) {
    activePair = pair;
    document.getElementById('active-pair').innerText = pair.toUpperCase();
    
    let probText = prob ? `${(prob*100).toFixed(0)}% Confidence` : 'Analyzing Data...';
    document.getElementById('pair-details').innerText = `Trend: ${trend} | ${probText}`;
    
    // Refresh chart
    try {
        const res = await fetch(`/api/history/${pair}`);
        const history = await res.json();
        
        const chartData = history.map((price, index) => ({
            time: Math.floor(Date.now() / 1000) - (history.length - index) * 60,
            value: parseFloat(price)
        }));
        
        chart.setData(chartData);
    } catch (e) {}
    
    updateWatchlist();
}

// Fetch Recent Trades
async function updateTradeHistory() {
    try {
        const res = await fetch('/api/trades/recent');
        const data = await res.json();
        const tbody = document.getElementById('trade-history-body');
        tbody.innerHTML = '';

        data.forEach(t => {
            const tr = document.createElement('tr');
            const isWin = t.pnl_idr > 0;
            const pnlColorClass = isWin ? 'up' : 'down';
            const time = new Date(t.created_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'});
            const formattedPnl = new Intl.NumberFormat('id-ID', { style: 'currency', currency: 'IDR', maximumFractionDigits: 0 }).format(t.pnl_idr);
            
            tr.innerHTML = `
                <td style="color: var(--text-muted)">${time}</td>
                <td class="col-pair">${t.pair.toUpperCase()}</td>
                <td>${t.reason}</td>
                <td class="${pnlColorClass}">
                    ${isWin ? '+' : ''}${formattedPnl}
                </td>
                <td style="color: var(--text-muted)">${t.reason.includes('TP') ? 'COMPLETED' : 'CLOSED'}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {}
}

// Live WS Stream Simulation
function updateStream() {
    const stream = document.getElementById('ws-stream');
    const pairs = ['BTC/IDR', 'ETH/IDR', 'SOL/IDR', 'XRP/IDR'];
    const pair = pairs[Math.floor(Math.random() * pairs.length)];
    const price = (Math.random() * 1000000).toLocaleString('en-US', {minimumFractionDigits: 2});
    const size = (Math.random() * 5).toFixed(2);
    
    const entry = document.createElement('div');
    entry.className = 'stream-entry';
    entry.innerHTML = `<span style="color:var(--text-muted)">${new Date().toISOString().split('T')[1].slice(0,8)}</span> <span style="color:var(--text-primary)">${pair}</span> <span style="color:var(--accent-green)">${price}</span> <span style="color:var(--text-muted)">[${size}]</span>`;
    
    stream.prepend(entry);
    if (stream.childNodes.length > 50) stream.lastChild.remove();
}

function addLog(msg) {
    const container = document.getElementById('ai-logs');
    const div = document.createElement('div');
    div.className = 'log-entry';
    div.innerHTML = `
        <span class="log-time">${new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}</span>
        <span class="log-msg">${msg}</span>
    `;
    container.prepend(div);
    if (container.childNodes.length > 30) container.lastChild.remove();
}

// Main Loop
initSystem();
updateStats();
updateWatchlist();
updateTradeHistory();

setInterval(updateStats, 5000);
setInterval(updateWatchlist, 3000);
setInterval(updateTradeHistory, 10000);
setInterval(updateStream, 300);

// Default Selection
setTimeout(() => selectPair('btcidr', 'BULLISH', 0.85), 1000);
