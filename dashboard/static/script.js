let chart;
let areaSeries;
let activePair = 'BTCCIDR';
let lastStats = {};
let lastPrices = {};

// Helper for flashing numbers via text color
function flashText(el, direction) {
    if (!el) return;
    const originalColor = el.style.color;
    el.style.color = direction === 'up' ? '#10b981' : '#ef4444';
    setTimeout(() => {
        el.style.color = originalColor;
    }, 600);
}

// Initialize Chart
function initChart() {
    const chartContainer = document.getElementById('chart-canvas');
    chart = LightweightCharts.createChart(chartContainer, {
        layout: {
            backgroundColor: 'transparent',
            textColor: '#71717a',
            fontSize: 11,
            fontFamily: "'Inter', sans-serif"
        },
        grid: {
            vertLines: { color: 'rgba(255, 255, 255, 0.03)' },
            horzLines: { color: 'rgba(255, 255, 255, 0.03)' },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
            vertLine: {
                color: '#3b82f6',
                width: 1,
                style: 3,
                labelBackgroundColor: '#3b82f6',
            },
            horzLine: {
                color: '#3b82f6',
                width: 1,
                style: 3,
                labelBackgroundColor: '#3b82f6',
            },
        },
        rightPriceScale: {
            borderColor: '#27272a',
        },
        timeScale: {
            borderColor: '#27272a',
            timeVisible: true,
        },
    });

    areaSeries = chart.addAreaSeries({
        lineColor: '#3b82f6',
        topColor: 'rgba(59, 130, 246, 0.2)',
        bottomColor: 'rgba(59, 130, 246, 0.0)',
        lineWidth: 2,
    });

    window.addEventListener('resize', () => {
        chart.resize(chartContainer.clientWidth, chartContainer.clientHeight);
    });
}

// Fetch Stats
async function updateStats() {
    try {
        const res = await fetch('/api/stats');
        const data = await res.json();
        
        const pnlEl = document.getElementById('daily-pnl');
        const newPnl = `$${(data.daily_pnl / 15000).toFixed(2)}`; // Converting IDR to rough USD for a "professional" look if desired, or keep IDR. Let's keep original data structure but format nicely.
        
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
                    <span class="ticker-price class="flash-text">${priceStr}</span>
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
        
        areaSeries.setData(chartData);
        chart.timeScale().fitContent();
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
initChart();
updateStats();
updateWatchlist();
updateTradeHistory();

setInterval(updateStats, 5000);
setInterval(updateWatchlist, 3000);
setInterval(updateTradeHistory, 10000);
setInterval(updateStream, 300);

// Default Selection
setTimeout(() => selectPair('btcidr', 'BULLISH', 0.85), 1000);
