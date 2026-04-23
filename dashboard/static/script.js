// --- Configuration ---
const API_BASE = window.location.origin + '/api';

// --- Radar Engine 2.0 ---
const canvas = document.getElementById('radarCanvas');
const ctx = canvas.getContext('2d');
let points = [];
let particles = [];

function resize() {
    canvas.width = canvas.parentElement.clientWidth;
    canvas.height = canvas.parentElement.clientHeight;
}
window.addEventListener('resize', resize);
resize();

class Particle {
    constructor(x, y, color) {
        this.x = x; this.y = y;
        this.size = Math.random() * 3 + 1;
        this.speedX = Math.random() * 3 - 1.5;
        this.speedY = Math.random() * 3 - 1.5;
        this.color = color;
        this.life = 1.0;
    }
    update() {
        this.x += this.speedX;
        this.y += this.speedY;
        this.life -= 0.02;
    }
    draw() {
        ctx.fillStyle = this.color.replace(')', `, ${this.life})`).replace('rgb', 'rgba');
        ctx.beginPath();
        ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
        ctx.fill();
    }
}

function drawRadar() {
    const centerX = canvas.width / 2;
    const centerY = canvas.height / 2;
    const maxRadius = Math.min(centerX, centerY) * 0.9;

    // Fading background for trails
    ctx.fillStyle = 'rgba(5, 10, 15, 0.2)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Draw Background Grid
    ctx.strokeStyle = 'rgba(0, 242, 255, 0.1)';
    ctx.lineWidth = 1;
    for (let i = 1; i <= 4; i++) {
        ctx.beginPath();
        ctx.arc(centerX, centerY, (maxRadius / 4) * i, 0, Math.PI * 2);
        ctx.stroke();
    }

    // Grid Lines (Sectors)
    ctx.beginPath();
    ctx.moveTo(centerX - maxRadius, centerY); ctx.lineTo(centerX + maxRadius, centerY);
    ctx.moveTo(centerX, centerY - maxRadius); ctx.lineTo(centerX, centerY + maxRadius);
    ctx.stroke();

    // Draw Sweeping Beam
    const sweepAngle = (Date.now() / 2000) % (Math.PI * 2);
    const gradient = ctx.createRadialGradient(centerX, centerY, 0, centerX, centerY, maxRadius);
    gradient.addColorStop(0, 'rgba(0, 242, 255, 0)');
    gradient.addColorStop(1, 'rgba(0, 242, 255, 0.05)');
    ctx.beginPath();
    ctx.moveTo(centerX, centerY);
    ctx.arc(centerX, centerY, maxRadius, sweepAngle - 0.4, sweepAngle);
    ctx.lineTo(centerX, centerY);
    ctx.fillStyle = gradient;
    ctx.fill();

    points.forEach((p, index) => {
        // Deterministic Angle based on pair name hash
        let hash = 0;
        for (let i = 0; i < p.pair.length; i++) hash = p.pair.charCodeAt(i) + ((hash << 5) - hash);
        const angle = (hash % 360) * (Math.PI / 180);
        
        // Radius mapping
        let radius;
        if (p.score >= 80) radius = (100 - p.score) * (maxRadius / 100) * 1.5;
        else if (p.score >= 40) radius = (maxRadius * 0.3) + (80 - p.score) * (maxRadius / 100) * 2;
        else radius = (maxRadius * 0.6) + (40 - p.score) * (maxRadius / 100) * 1.5;

        const x = centerX + Math.cos(angle) * radius;
        const y = centerY + Math.sin(angle) * radius;

        const isElite = p.score >= 80;
        const isShadow = p.score < 30;

        // Draw Point
        ctx.shadowBlur = isElite ? 15 : 0;
        ctx.shadowColor = '#00f2ff';
        ctx.fillStyle = isElite ? '#00f2ff' : (isShadow ? 'rgba(0, 242, 255, 0.2)' : 'rgba(0, 242, 255, 0.6)');
        
        ctx.beginPath();
        ctx.arc(x, y, isElite ? 4 : (isShadow ? 1 : 2.5), 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;

        // Label for Elite or if score is decent
        if (isElite || p.score > 60) {
            ctx.fillStyle = isElite ? '#00f2ff' : 'rgba(255,255,255,0.7)';
            ctx.font = isElite ? 'bold 11px Orbitron' : '9px Orbitron';
            ctx.fillText(p.pair.toUpperCase(), x + 8, y + 4);
        }
    });

    // Update & Draw Particles
    particles = particles.filter(p => p.life > 0);
    particles.forEach(p => { p.update(); p.draw(); });

    requestAnimationFrame(drawRadar);
}

// --- Charts ---
let weightsChart;
function initCharts() {
    const ctxW = document.getElementById('weightsChart').getContext('2d');
    weightsChart = new Chart(ctxW, {
        type: 'radar',
        data: {
            labels: ['AI PROB', 'WHALE', 'SECTOR', 'IMBALANCE'],
            datasets: [{
                data: [1, 1, 1, 1],
                backgroundColor: 'rgba(0, 242, 255, 0.2)',
                borderColor: '#00f2ff',
                borderWidth: 2,
                pointBackgroundColor: '#ff00ff'
            }]
        },
        options: {
            scales: {
                r: {
                    grid: { color: 'rgba(255, 255, 255, 0.1)' },
                    angleLines: { color: 'rgba(255, 255, 255, 0.1)' },
                    ticks: { display: false },
                    suggestedMin: 0, suggestedMax: 2
                }
            },
            plugins: { legend: { display: false } }
        }
    });
}

// --- Terminal Typewriter ---
const terminal = document.getElementById('neural-terminal');
function addTerminalLine(text) {
    const line = document.createElement('div');
    line.className = 'term-line';
    line.innerText = `> ${text}`;
    terminal.appendChild(line);
    terminal.scrollTop = terminal.scrollHeight;
    if (terminal.childElementCount > 50) terminal.removeChild(terminal.firstChild);
}

// --- Voice & Targeting Logic ---
let isMuted = true;
const muteBtn = document.getElementById('mute-btn');

muteBtn.addEventListener('click', () => {
    isMuted = !isMuted;
    muteBtn.innerText = isMuted ? 'VOICE: OFF' : 'VOICE: ON';
    muteBtn.classList.toggle('active', !isMuted);
    if (!isMuted) speak("Predator Voice System Activated.");
});

function speak(text) {
    if (isMuted) return;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.1;
    utterance.pitch = 0.8; // Deep tactical voice
    window.speechSynthesis.speak(utterance);
}

function drawLockOn(x, y) {
    ctx.strokeStyle = '#ff00ff';
    ctx.lineWidth = 2;
    const size = 15 + Math.sin(Date.now() / 100) * 5;
    
    ctx.beginPath();
    ctx.moveTo(x - size, y - size); ctx.lineTo(x - size + 10, y - size);
    ctx.moveTo(x - size, y - size); ctx.lineTo(x - size, y - size + 10);
    
    ctx.moveTo(x + size, y + size); ctx.lineTo(x + size - 10, y + size);
    ctx.moveTo(x + size, y + size); ctx.lineTo(x + size, y + size - 10);
    
    ctx.stroke();
}

// --- Alerts & Drama ---
const alertOverlay = document.getElementById('alert-overlay');
let lastSeenLogId = null;

function triggerAlert(type, pair, pnl) {
    const isTP = type === 'TP';
    
    if (isTP) triggerGlitch(); // Spectacular transition on TP
    
    const text = isTP ? 
        `[ TARGET NEUTRALIZED ]\n${pair.toUpperCase()} PROFIT: ${pnl}` : 
        `[ SYSTEM BREACH ]\n${pair.toUpperCase()} LIQUIDATED`;
    
    alertOverlay.innerText = text;
    
    // Speak event
    if (isTP) speak(`Target neutralized on ${pair}. Profit secured.`);
    else speak(`Position liquidated on ${pair}. System breach detected.`);

    alertOverlay.className = isTP ? 'tp-alert' : 'sl-alert';
    alertOverlay.style.display = 'flex';
    document.body.classList.add('shake');

    setTimeout(() => {
        alertOverlay.style.display = 'none';
        document.body.classList.remove('shake');
    }, 3000);
}

const specialContainer = document.getElementById('special-alerts-container');
const regimeBg = document.getElementById('regime-bg');

function triggerSpecialEvent(type, text = "") {
    if (type === 'WHALE') {
        triggerGlitch(); // Spectacular entry for whales
        const whale = document.createElement('div');
        whale.className = 'whale-swim';
        whale.innerHTML = `🐳<br><span style="font-size: 14px; font-family: 'Orbitron';">${text}</span>`;
        specialContainer.appendChild(whale);
        addTerminalLine(`[!] WHALE SIGHTING: ${text}`);
        speak(`Warning. Massive liquidity detected on ${text.split(' ')[0]}. Whale shadowing initiated.`);
        setTimeout(() => whale.remove(), 8000);
    }
    
    if (type === 'FAKE_PUMP') {
        const stamp = document.createElement('div');
        stamp.className = 'fake-pump-alert';
        stamp.innerHTML = `FAKE PUMP<br><span style="font-size: 20px;">${text}</span>`;
        specialContainer.appendChild(stamp);
        addTerminalLine(`[!] WARNING: FAKE PUMP DETECTED ON ${text}`);
        speak(`Caution. Fake pump detected on ${text}. Volume is artificial.`);
        document.body.classList.add('shake');
        setTimeout(() => {
            stamp.remove();
            document.body.classList.remove('shake');
        }, 3000);
    }
}

// --- Spectacular Logic ---
const pulseContainer = document.getElementById('volume-pulse');
const pulseBars = [];
const BAR_COUNT = 60;

function initPulse() {
    for (let i = 0; i < BAR_COUNT; i++) {
        const bar = document.createElement('div');
        bar.className = 'pulse-bar';
        const angle = (i / BAR_COUNT) * 360;
        bar.style.transform = `rotate(${angle}deg) translateY(-210px)`;
        pulseContainer.appendChild(bar);
        pulseBars.push(bar);
    }
}

function updatePulse(volume) {
    const intensity = Math.min(100, volume / 10000000); // Scale to 10M volume
    pulseBars.forEach((bar, i) => {
        const h = 5 + Math.random() * intensity;
        bar.style.height = `${h}px`;
        bar.style.opacity = 0.2 + (intensity / 150);
    });
}

function triggerGlitch() {
    document.body.classList.add('glitch-active');
    setTimeout(() => document.body.classList.remove('glitch-active'), 600);
}

function triggerBuyEffect(pair) {
    const matrix = document.getElementById('matrix-overlay');
    matrix.classList.add('active');
    
    // Voice confirmation
    speak(`Engaging target ${pair}. Oracle synchronization complete. Execution initiated.`);
    
    // Crosshair on radar (simulated at center for now, or random point)
    const crosshair = document.createElement('div');
    crosshair.className = 'lock-on-crosshair';
    crosshair.style.left = '50%';
    crosshair.style.top = '50%';
    document.getElementById('center-radar').appendChild(crosshair);
    
    setTimeout(() => {
        matrix.classList.remove('active');
        crosshair.remove();
    }, 5000);
}

// Data Fetching
let lastRegime = '';
let lastWhaleStr = '';
let lastFakeStr = '';
async function fetchData() {
    try {
        const statsRes = await fetch(`${API_BASE}/stats`);
        const stats = await statsRes.json();
        
        // Auto-Trigger Whale Alert
        if (stats.latest_whale && stats.latest_whale !== lastWhaleStr) {
            const [pair, val] = stats.latest_whale.split('|');
            triggerSpecialEvent('WHALE', `${pair.toUpperCase()} - Rp ${parseInt(val).toLocaleString()}`);
            lastWhaleStr = stats.latest_whale;
        }

        // Auto-Trigger Fake Pump Alert
        if (stats.latest_fake_pump && stats.latest_fake_pump !== lastFakeStr) {
            const [pair, chg] = stats.latest_fake_pump.split('|');
            triggerSpecialEvent('FAKE_PUMP', `${pair.toUpperCase()} (+${chg}%)`);
            lastFakeStr = stats.latest_fake_pump;
        }

        const regimeEl = document.getElementById('market-regime');
        regimeEl.innerText = stats.regime;
        
        if (stats.regime !== lastRegime) {
            triggerGlitch(); // Spectacular Regime shift
            addTerminalLine(`MARKET REGIME SHIFT: ${stats.regime} (Leader: ${stats.regime_leader})`);
            speak(`Market regime shift detected. Global sentiment is now ${stats.regime}. Leading pair: ${stats.regime_leader.split(' ')[0]}.`);
            lastRegime = stats.regime;
            
            // Update Background Watermark
            regimeBg.innerText = stats.regime === 'BULL' ? '📈' : (stats.regime === 'BEAR' ? '📉' : '⚡');
            regimeBg.style.color = stats.regime === 'BULL' ? '#00ff41' : (stats.regime === 'BEAR' ? '#ff003c' : '#00f2ff');
        }
        
        regimeEl.style.color = stats.regime === 'BULL' ? '#00ff41' : (stats.regime === 'BEAR' ? '#ff003c' : '#00f2ff');

        document.getElementById('daily-trades').innerText = stats.daily_trades;
        document.getElementById('total-pnl').innerText = `Rp ${(stats.total_pnl || 0).toLocaleString()}`;

        const weightsRes = await fetch(`${API_BASE}/weights`);
        const weights = await weightsRes.json();
        weightsChart.data.datasets[0].data = [
            weights.ai_prob || 1, weights.whale_boost || 1, 
            weights.sector_sentiment || 1, weights.imbalance || 1
        ];
        weightsChart.update();

        const radarRes = await fetch(`${API_BASE}/radar`);
        points = await radarRes.json();

        // Update Pulse Visualizer based on top pair volume
        if (points.length > 0) {
            updatePulse(points[0].price * 1000); // Simulated volume signal
            
            // Update Active Hunting Grounds (Top 10)
            const huntingList = document.getElementById('hunting-grounds');
            huntingList.innerHTML = '';
            [...points].sort((a,b) => b.score - a.score).slice(0, 10).forEach(p => {
                const item = document.createElement('div');
                item.className = 'hunting-item';
                item.style.borderLeft = `3px solid ${p.score > 80 ? '#00f2ff' : '#00ff41'}`;
                item.innerHTML = `
                    <span class="pair">${p.pair.toUpperCase()}</span>
                    <span class="score">SCORE: ${p.score.toFixed(0)}</span>
                `;
                huntingList.appendChild(item);
            });
        }

        const logsRes = await fetch(`${API_BASE}/logs`);
        const logs = await logsRes.json();
        
        // Detect NEW SELL events for alerts
        if (logs.length > 0) {
            const latest = logs[0];
            if (lastSeenLogId && latest.id > lastSeenLogId && latest.side === 'SELL') {
                const type = (latest.price > 0) ? 'TP' : 'SL'; // Simple heuristic
                triggerAlert(type, latest.pair, latest.price);
            }
            
            // Detect BUY events for visuals
            logs.forEach(log => {
                if (log.side === 'BUY') {
                    if (!window.lastBuyHandled || window.lastBuyHandled !== log.id) {
                        triggerBuyEffect(log.pair);
                        window.lastBuyHandled = log.id;
                    }
                }
            });
            lastSeenLogId = logs[0].id;
        }

        const feed = document.getElementById('log-feed');
        feed.innerHTML = logs.map(l => `
            <div style="border-bottom: 1px solid rgba(255,255,255,0.05); padding: 5px 0; color: ${l.side === 'BUY' ? '#00ff41' : '#ff003c'}">
                [${new Date(l.created_at).toLocaleTimeString()}] ${l.pair.toUpperCase()} ${l.side} ${l.status} @ ${l.price}
            </div>
        `).join('');

    } catch (err) {
        console.error('Fetch error:', err);
    }
}

// Initialize
initCharts();
initPulse();
drawRadar();
setInterval(fetchData, 3000);
fetchData();

// Simulate AI thoughts
const thoughts = [
    "Analyzing order book imbalance...",
    "Scanning for whale footprints in BLUECHIP sector...",
    "Calculating predictive bounce probability...",
    "Optimizing dynamic entry weights...",
    "Market regime analyzed: Neutralizing bias.",
    "Shadow Jaring positions updated.",
    "Liquidity Grab target identified: Waiting for flash crash..."
];
setInterval(() => {
    addTerminalLine(thoughts[Math.floor(Math.random() * thoughts.length)]);
}, 8000);

// Test Trigger (Press T for TP, S for SL, W for Whale, F for Fake Pump)
window.addEventListener('keydown', (e) => {
    const key = e.key.toLowerCase();
    if (key === 't') triggerAlert('TP', 'BTCIDR', '+Rp 50,000');
    if (key === 's') triggerAlert('SL', 'ETHIDR', '-Rp 20,000');
    if (key === 'w') triggerSpecialEvent('WHALE');
    if (key === 'f') triggerSpecialEvent('FAKE_PUMP');
    if (key === 'g') triggerGlitch();
    if (key === 't') triggerBuyEffect('BTCIDR'); // Test BUY effect
    if (key === 'b') {
        // Test Regime Change
        lastRegime = (lastRegime === 'BULL' ? 'BEAR' : 'BULL');
        document.getElementById('market-regime').innerText = lastRegime;
        regimeBg.innerText = lastRegime === 'BULL' ? '📈' : '📉';
        regimeBg.style.color = lastRegime === 'BULL' ? '#00ff41' : '#ff003c';
    }
});
