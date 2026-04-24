let chart;
let lineSeries;
let activePair = 'BTCCIDR';
let lastStats = {};

// Initialize Chart
function initChart() {
    const chartContainer = document.getElementById('chart-canvas');
    chart = LightweightCharts.createChart(chartContainer, {
        layout: {
            backgroundColor: '#0a0b0d',
            textColor: '#d1d4dc',
        },
        grid: {
            vertLines: { color: '#1e222d' },
            horzLines: { color: '#1e222d' },
        },
        crosshair: {
            mode: LightweightCharts.CrosshairMode.Normal,
        },
        rightPriceScale: {
            borderColor: '#2a2e39',
        },
        timeScale: {
            borderColor: '#2a2e39',
            timeVisible: true,
        },
    });

    lineSeries = chart.addLineSeries({
        color: '#2962ff',
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
        
        document.getElementById('daily-pnl').innerText = `Rp ${data.daily_pnl.toLocaleString()}`;
        document.getElementById('win-rate').innerText = `${data.win_rate}%`;
        document.getElementById('total-trades').innerText = data.total_trades_all_time;
        document.getElementById('market-regime').innerText = data.regime;
        
        const winRateEl = document.getElementById('win-rate');
        if (data.win_rate >= 60) winRateEl.style.color = '#00ff88';
        else if (data.win_rate < 40) winRateEl.style.color = '#ff3b69';
        
        // Add Intel Logs
        if (data.latest_whale) addLog(`WHALE DETECTED: ${data.latest_whale}`, 'whale');
        if (data.latest_fake_pump) addLog(`FAKE PUMP ALERT: ${data.latest_fake_pump}`, 'alert');
        
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
            
            item.innerHTML = `
                <div class="ticker-info">
                    <span class="ticker-pair">${t.pair.toUpperCase()}</span>
                    <span class="ticker-price">Rp ${t.price.toLocaleString()}</span>
                </div>
                <span class="ticker-change ${changeClass}">${t.change}%</span>
            `;
            list.appendChild(item);
        });
    } catch (e) {}
}

// Select Pair & Load Chart
async function selectPair(pair, trend, prob) {
    activePair = pair;
    document.getElementById('active-pair').innerText = pair.toUpperCase();
    document.getElementById('pair-trend').innerText = trend;
    document.getElementById('pair-prob').innerText = `${Math.round(prob * 100)}% PROB`;
    
    // Refresh chart
    try {
        const res = await fetch(`/api/history/${pair}`);
        const history = await res.json();
        
        const chartData = history.map((price, index) => ({
            time: Math.floor(Date.now() / 1000) - (history.length - index) * 5,
            value: parseFloat(price)
        }));
        
        lineSeries.setData(chartData);
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
            const pnlClass = t.pnl_idr > 0 ? 'up' : 'down';
            const time = new Date(t.created_at).toLocaleTimeString();
            
            tr.innerHTML = `
                <td>${time}</td>
                <td>${t.pair.toUpperCase()}</td>
                <td>${t.reason}</td>
                <td class="${pnlClass}">Rp ${t.pnl_idr.toLocaleString()}</td>
                <td>${t.reason.includes('TP') ? 'TARGET HIT' : 'RISK OUT'}</td>
            `;
            tbody.appendChild(tr);
        });
    } catch (e) {}
}

// Live WS Stream Simulation (from data)
function updateStream() {
    const stream = document.getElementById('ws-stream');
    const pairs = ['BTCIDR', 'ETHIDR', 'DOGEIDR', 'PEPEIDR', 'SOLIDR'];
    const pair = pairs[Math.floor(Math.random() * pairs.length)];
    const price = (Math.random() * 1000000).toFixed(2);
    
    const entry = document.createElement('div');
    entry.style.fontSize = '0.6rem';
    entry.innerText = `[WS_IN] ${new Date().toLocaleTimeString()} | ${pair} | UPDATE | Price: ${price} | Seq: ${Math.floor(Math.random() * 100000)}`;
    
    stream.prepend(entry);
    if (stream.childNodes.length > 50) stream.lastChild.remove();
}

function addLog(msg, type) {
    const container = document.getElementById('ai-logs');
    const div = document.createElement('div');
    div.className = 'log-entry';
    div.innerHTML = `
        <span class="log-time">[${new Date().toLocaleTimeString()}]</span>
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
setInterval(updateStream, 500);

// Default Selection
setTimeout(() => selectPair('btcidr', 'UP', 0.85), 1000);
