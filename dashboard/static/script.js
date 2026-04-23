// --- Configuration ---
const API_BASE = window.location.origin + '/api';

// --- Radar Engine ---
const canvas = document.getElementById('radarCanvas');
const ctx = canvas.getContext('2d');
let points = [];

function resize() {
    canvas.width = canvas.parentElement.clientWidth;
    canvas.height = canvas.parentElement.clientHeight;
}
window.addEventListener('resize', resize);
resize();

function drawRadar() {
    const centerX = canvas.width / 2;
    const centerY = canvas.height / 2;
    const maxRadius = Math.min(centerX, centerY) * 0.9;

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Draw Background Circles
    ctx.strokeStyle = 'rgba(0, 242, 255, 0.2)';
    ctx.lineWidth = 1;
    for (let i = 1; i <= 4; i++) {
        ctx.beginPath();
        ctx.arc(centerX, centerY, (maxRadius / 4) * i, 0, Math.PI * 2);
        ctx.stroke();
    }

    // Draw Rotating Sweep
    const sweepAngle = (Date.now() / 1000) % (Math.PI * 2);
    ctx.beginPath();
    ctx.moveTo(centerX, centerY);
    ctx.arc(centerX, centerY, maxRadius, sweepAngle - 0.2, sweepAngle);
    ctx.lineTo(centerX, centerY);
    ctx.fillStyle = 'rgba(0, 242, 255, 0.1)';
    ctx.fill();

    // Draw Points (Coins)
    points.forEach(p => {
        const angle = p.angle || 0;
        const radius = (1 - (p.score / 100)) * maxRadius; // Higher score = closer to center
        const x = centerX + Math.cos(angle) * radius;
        const y = centerY + Math.sin(angle) * radius;

        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fillStyle = p.score > 80 ? '#ff00ff' : '#00f2ff';
        ctx.shadowBlur = 10;
        ctx.shadowColor = ctx.fillStyle;
        ctx.fill();
        ctx.shadowBlur = 0;

        ctx.fillStyle = 'white';
        ctx.font = '10px Roboto Mono';
        ctx.fillText(p.pair.toUpperCase(), x + 8, y + 4);
    });

    requestAnimationFrame(drawRadar);
}

// --- Charts ---
let weightsChart, allocationChart;

function initCharts() {
    const ctxW = document.getElementById('weightsChart').getContext('2d');
    weightsChart = new Chart(ctxW, {
        type: 'radar',
        data: {
            labels: ['AI Prob', 'Whale', 'Sector', 'Imbalance'],
            datasets: [{
                label: 'Weights',
                data: [1, 1, 1, 1],
                backgroundColor: 'rgba(255, 0, 255, 0.2)',
                borderColor: '#ff00ff',
                pointBackgroundColor: '#ff00ff'
            }]
        },
        options: {
            scales: {
                r: {
                    angleLines: { color: 'rgba(255, 255, 255, 0.1)' },
                    grid: { color: 'rgba(255, 255, 255, 0.1)' },
                    suggestedMin: 0.5,
                    suggestedMax: 2.0,
                    ticks: { display: false }
                }
            },
            plugins: { legend: { display: false } }
        }
    });
}

// --- Data Fetching ---
async function fetchData() {
    try {
        // Stats
        const statsRes = await fetch(`${API_BASE}/stats`);
        const stats = await statsRes.json();
        document.getElementById('market-regime').innerText = stats.regime;
        document.getElementById('daily-trades').innerText = stats.daily_trades;
        document.getElementById('total-pnl').innerText = `Rp ${stats.total_pnl.toLocaleString()}`;

        // Weights
        const weightsRes = await fetch(`${API_BASE}/weights`);
        const weights = await weightsRes.json();
        weightsChart.data.datasets[0].data = [
            weights.ai_prob || 1,
            weights.whale_boost || 1,
            weights.sector_sentiment || 1,
            weights.imbalance || 1
        ];
        weightsChart.update();

        // Radar Data
        const radarRes = await fetch(`${API_BASE}/radar`);
        const radarData = await radarRes.json();
        points = radarData.map((d, i) => ({
            ...d,
            angle: (i / radarData.length) * Math.PI * 2
        }));

        // Logs
        const logsRes = await fetch(`${API_BASE}/logs`);
        const logs = await logsRes.json();
        const feed = document.getElementById('log-feed');
        feed.innerHTML = logs.map(l => `
            <div class="log-item">
                <span class="log-time">[${new Date(l.created_at).toLocaleTimeString()}]</span>
                <span class="log-msg">${l.pair}: ${l.side} ${l.status} @ ${l.price}</span>
            </div>
        `).join('');

    } catch (err) {
        console.error('Fetch error:', err);
    }
}

// Initialize
initCharts();
drawRadar();
setInterval(fetchData, 5000);
fetchData();
