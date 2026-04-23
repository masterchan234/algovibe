const API_BASE = window.location.origin + '/api';

async function fetchData() {
    try {
        // Stats
        const statsRes = await fetch(`${API_BASE}/stats`);
        const stats = await statsRes.json();
        
        document.getElementById('pnl').innerText = `Rp ${stats.total_pnl?.toLocaleString()}`;
        document.getElementById('trades').innerText = stats.daily_trades;
        document.getElementById('positions').innerText = stats.active_positions;
        document.getElementById('regime').innerText = stats.regime;

        // Radar Data for Heatmap
        const radarRes = await fetch(`${API_BASE}/radar`);
        const points = await radarRes.json();
        
        // Logs for Intel
        const logsRes = await fetch(`${API_BASE}/logs`);
        const logs = await logsRes.json();
        
        renderHeatmap(points);
        renderMomentum(stats.sectors || {});
        renderIntel(logs, stats);
        
        // Update Ticker
        const ticker = document.getElementById('ticker-line');
        if (stats.latest_whale) {
            ticker.innerText = `>> WHALE DETECTED: ${stats.latest_whale} // SYSTEM ALERT!`;
            ticker.style.color = '#ff0000';
        } else {
            ticker.innerText = `>> SCANNING GLOBAL LIQUIDITY // MARKET REGIME: ${stats.regime} // ALL SYSTEMS GO.`;
            ticker.style.color = '';
        }

    } catch (e) {
        console.error("Fetch Error:", e);
    }
}

function renderHeatmap(points) {
    const container = document.getElementById('heatmap');
    container.innerHTML = '';
    
    // Sort by score or just take top 100
    const top100 = [...points].sort((a,b) => b.score - a.score).slice(0, 100);
    
    top100.forEach(p => {
        const tile = document.createElement('div');
        tile.className = 'tile';
        if (p.change > 2) tile.classList.add('pump');
        if (p.change < -2) tile.classList.add('dump');
        
        // Prediction Visuals
        const isHighConf = p.prediction > 0.05;
        if (isHighConf && p.prediction > 0.8) tile.classList.add('high-confidence');
        
        let predIcon = '▬';
        let predColor = 'rgba(255,255,255,0.3)';
        let predText = 'SCANNING';
        
        if (p.prediction > 0) {
            predIcon = p.trend === 'UP' ? '▲' : '▼';
            predColor = p.trend === 'UP' ? '#00ff00' : '#ff0000';
            predText = `${Math.round(p.prediction * 100)}%`;
        }
        
        tile.innerHTML = `
            <span style="font-weight:bold">${p.pair.replace('idr','').toUpperCase()}</span>
            <span>${p.change > 0 ? '+' : ''}${p.change}%</span>
            <div class="prediction-indicator" style="color: ${predColor}">
                ${predIcon} ${predText}
            </div>
        `;
        container.appendChild(tile);
    });
}

function renderMomentum(sectors) {
    const container = document.getElementById('momentum-list');
    container.innerHTML = '';
    
    // Convert object to array and sort
    const sectorEntries = Object.entries(sectors);
    
    sectorEntries.forEach(([name, score]) => {
        const wrap = document.createElement('div');
        wrap.className = 'm-bar-wrap';
        
        // Map sentiment score (-5 to +5 typically) to 0-100%
        // Score 0 = 50%, Score +5 = 100%, Score -5 = 0%
        const normalizedVal = Math.min(100, Math.max(0, 50 + (score * 10)));
        
        wrap.innerHTML = `
            <div class="m-bar-fill" style="width: ${normalizedVal}%"></div>
            <div class="m-label">${name.toUpperCase()} [${score > 0 ? '+' : ''}${score.toFixed(1)}%]</div>
        `;
        container.appendChild(wrap);
    });
    
    if (sectorEntries.length === 0) {
        container.innerHTML = '<div style="font-size:10px; opacity:0.5;">Calculating sector sentiment...</div>';
    }
}

function renderIntel(logs, stats) {
    const container = document.getElementById('intel-log');
    container.innerHTML = '';
    
    let messages = [];
    
    // Add Regime Change
    messages.push(`> CURRENT REGIME: ${stats.regime}`);
    
    // Add Whale Alert
    if (stats.latest_whale) {
        messages.push(`> ALERT: Whale sighted on ${stats.latest_whale.toUpperCase()}`);
    }
    
    // Add Transaction Logs
    logs.forEach(log => {
        const time = log.created_at?.split(' ')[1] || '';
        messages.push(`> [${time}] ${log.side} ${log.pair} @ ${log.price} [${log.status}]`);
    });
    
    if (messages.length === 0) {
        messages.push("> Awaiting market signal...");
    }
    
    messages.forEach(msg => {
        const div = document.createElement('div');
        div.style.marginBottom = '5px';
        div.style.borderLeft = '2px solid var(--amber)';
        div.style.paddingLeft = '5px';
        div.innerText = msg;
        
        // Highlight critical alerts
        if (msg.includes('ALERT') || msg.includes('BUY')) {
            div.style.color = '#ffae00';
            div.style.fontWeight = 'bold';
            div.style.textShadow = '0 0 5px var(--amber)';
        }
        
        container.appendChild(div);
    });
}

// Initial
setInterval(fetchData, 3000);
fetchData();
