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
    ctx.fillStyle = 'rgba(5, 10, 15, 0.15)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Draw Background Grid
    ctx.strokeStyle = 'rgba(0, 242, 255, 0.1)';
    ctx.lineWidth = 1;
    for (let i = 1; i <= 4; i++) {
        ctx.beginPath();
        ctx.arc(centerX, centerY, (maxRadius / 4) * i, 0, Math.PI * 2);
        ctx.stroke();
    }

    // Draw Sweeping Beam
    const sweepAngle = (Date.now() / 1500) % (Math.PI * 2);
    const gradient = ctx.createRadialGradient(centerX, centerY, 0, centerX, centerY, maxRadius);
    gradient.addColorStop(0, 'rgba(0, 242, 255, 0)');
    gradient.addColorStop(1, 'rgba(0, 242, 255, 0.1)');

    ctx.beginPath();
    ctx.moveTo(centerX, centerY);
    ctx.arc(centerX, centerY, maxRadius, sweepAngle - 0.4, sweepAngle);
    ctx.lineTo(centerX, centerY);
    ctx.fillStyle = gradient;
    ctx.fill();

    // Beam Line
    ctx.strokeStyle = 'rgba(0, 242, 255, 0.5)';
    ctx.beginPath();
    ctx.moveTo(centerX, centerY);
    ctx.lineTo(centerX + Math.cos(sweepAngle) * maxRadius, centerY + Math.sin(sweepAngle) * maxRadius);
    ctx.stroke();

    // Draw Points (Coins)
    points.forEach((p, i) => {
        const angle = (i / (points.length || 1)) * Math.PI * 2 + (Date.now() / 10000);
        const radius = (1 - (p.score / 100)) * maxRadius;
        const x = centerX + Math.cos(angle) * radius;
        const y = centerY + Math.sin(angle) * radius;

        // Glow
        const color = p.score > 80 ? '#ff00ff' : '#00f2ff';
        ctx.shadowBlur = 15;
        ctx.shadowColor = color;
        ctx.fillStyle = color;
        
        ctx.beginPath();
        ctx.arc(x, y, 5, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;

        // Label
        ctx.fillStyle = 'white';
        ctx.font = 'bold 11px Roboto Mono';
        ctx.fillText(p.pair.toUpperCase(), x + 10, y + 4);

        // Particle Trigger for high scores
        if (p.score > 90 && Math.random() > 0.95) {
            particles.push(new Particle(x, y, color === '#ff00ff' ? 'rgb(255,0,255)' : 'rgb(0,242,255)'));
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

// --- Data Fetching ---
let lastRegime = '';
async function fetchData() {
    try {
        const statsRes = await fetch(`${API_BASE}/stats`);
        const stats = await statsRes.json();
        
        const regimeEl = document.getElementById('market-regime');
        regimeEl.innerText = stats.regime;
        regimeEl.style.color = stats.regime === 'BULL' ? '#00ff41' : (stats.regime === 'BEAR' ? '#ff003c' : '#00f2ff');
        
        if (stats.regime !== lastRegime) {
            addTerminalLine(`MARKET REGIME SHIFT DETECTED: ${stats.regime}`);
            lastRegime = stats.regime;
        }

        document.getElementById('daily-trades').innerText = stats.daily_trades;
        document.getElementById('total-pnl').innerText = `Rp ${stats.total_pnl.toLocaleString()}`;

        const weightsRes = await fetch(`${API_BASE}/weights`);
        const weights = await weightsRes.json();
        weightsChart.data.datasets[0].data = [
            weights.ai_prob || 1, weights.whale_boost || 1, 
            weights.sector_sentiment || 1, weights.imbalance || 1
        ];
        weightsChart.update();

        const radarRes = await fetch(`${API_BASE}/radar`);
        points = await radarRes.json();

        const logsRes = await fetch(`${API_BASE}/logs`);
        const logs = await logsRes.json();
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
