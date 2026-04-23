const canvas = document.getElementById('race-canvas');
const ctx = canvas.getContext('2d');
const API_BASE = window.location.origin + '/api';

let width, height;
let racers = {}; 
let particles = [];
const COLORS = ['#00ff95', '#00d0ff', '#ff00ff', '#ffae00', '#fff', '#0077ff'];

// Ultra-Accurate Mandalika Map (17 Turns)
const mandalikaBasePoints = [
    {x: 100, y: 400}, // Start/Finish
    {x: 500, y: 400}, // Main Straight
    {x: 650, y: 380}, // T1 (Fast Right)
    {x: 680, y: 300}, // T2 (Left)
    {x: 750, y: 250}, // T3 (Right)
    {x: 820, y: 300}, // T4 (Right)
    {x: 880, y: 380}, // T5 (Right)
    {x: 950, y: 350}, // T6 (Fast Right)
    {x: 980, y: 250}, // T7
    {x: 950, y: 150}, // T8
    {x: 850, y: 100}, // T9
    {x: 750, y: 150}, // T10 (Hairpin)
    {x: 650, y: 100}, // T11
    {x: 550, y: 150}, // T12
    {x: 450, y: 100}, // T13
    {x: 350, y: 150}, // T14
    {x: 250, y: 100}, // T15
    {x: 150, y: 150}, // T16
    {x: 100, y: 250}, // T17
    {x: 100, y: 400}  // Back to Start
];

let circuitPath = [];

function resize() {
    width = canvas.width = window.innerWidth;
    height = canvas.height = window.innerHeight;
    generateSmoothPath();
}

// Catmull-Rom Spline implementation for ULTRA-SMOOTH pathing
function generateSmoothPath() {
    circuitPath = [];
    const scaleX = width / 1200;
    const scaleY = height / 600;
    const scaledPoints = mandalikaBasePoints.map(p => ({ x: p.x * scaleX, y: p.y * scaleY }));

    for (let i = 0; i < scaledPoints.length - 1; i++) {
        const p0 = scaledPoints[i === 0 ? scaledPoints.length - 2 : i - 1];
        const p1 = scaledPoints[i];
        const p2 = scaledPoints[i + 1];
        const p3 = scaledPoints[i + 2] || scaledPoints[0];

        for (let t = 0; t < 1; t += 0.005) { // High density for smoothness
            const t2 = t * t;
            const t3 = t2 * t;
            
            const x = 0.5 * (
                (2 * p1.x) +
                (-p0.x + p2.x) * t +
                (2 * p0.x - 5 * p1.x + 4 * p2.x - p3.x) * t2 +
                (-p0.x + 3 * p1.x - 3 * p2.x + p3.x) * t3
            );
            const y = 0.5 * (
                (2 * p1.y) +
                (-p0.y + p2.y) * t +
                (2 * p0.y - 5 * p1.y + 4 * p2.y - p3.y) * t2 +
                (-p0.y + 3 * p1.y - 3 * p2.y + p3.y) * t3
            );
            circuitPath.push({ x, y });
        }
    }
}

window.addEventListener('resize', resize);
resize();

function getTrackPoint(progress, laneOffset = 0) {
    const idx = Math.floor(progress * (circuitPath.length - 1));
    const p = circuitPath[idx];
    const nextIdx = (idx + 10) % circuitPath.length;
    const pNext = circuitPath[nextIdx];
    const angle = Math.atan2(pNext.y - p.y, pNext.x - p.x);
    
    return { 
        x: p.x + Math.cos(angle + Math.PI/2) * laneOffset, 
        y: p.y + Math.sin(angle + Math.PI/2) * laneOffset,
        angle: angle 
    };
}

async function fetchData() {
    try {
        const res = await fetch(`${API_BASE}/radar`);
        const points = await res.json();
        const top10 = points.sort((a, b) => b.change - a.change).slice(0, 10);

        top10.forEach((r, i) => {
            if (!racers[r.pair]) {
                racers[r.pair] = { progress: Math.random(), speed: 0.001, targetSpeed: 0.001, lane: (Math.random()-0.5)*30, color: COLORS[i % COLORS.length], data: r };
            }
            racers[r.pair].targetSpeed = 0.0005 + (Math.max(0, r.change) * 0.0008);
            racers[r.pair].data = r;
        });

        const active = top10.map(r => r.pair);
        Object.keys(racers).forEach(p => { if (!active.includes(p)) delete racers[p]; });
        updateLeaderboard(top10);
    } catch (e) {}
}

function updateLeaderboard(top10) {
    const list = document.getElementById('rank-list');
    list.innerHTML = top10.map((r, i) => `
        <div class="rank-row ${i < 3 ? 'active' : ''}">
            <span>#${i+1} ${r.pair.replace('idr','').toUpperCase()}</span>
            <span>${r.change > 0 ? '+' : ''}${r.change}%</span>
        </div>
    `).join('');
}

class Particle {
    constructor(x, y, color) {
        this.x = x; this.y = y; this.color = color;
        this.vx = (Math.random() - 0.5) * 1.5; this.vy = (Math.random() - 0.5) * 1.5;
        this.life = 1.0; this.decay = 0.04;
    }
    update() { this.x += this.vx; this.y += this.vy; this.life -= this.decay; }
    draw() { ctx.fillStyle = this.color; ctx.globalAlpha = this.life; ctx.beginPath(); ctx.arc(this.x, this.y, 1.5, 0, Math.PI * 2); ctx.fill(); }
}

function drawTrack() {
    // Asphalt (Real Mandalika Dark Grey)
    ctx.strokeStyle = '#222';
    ctx.lineWidth = 50;
    ctx.beginPath();
    circuitPath.forEach((p, i) => { if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y); });
    ctx.closePath(); ctx.stroke();

    // Mandalika Green-Yellow-Blue Run-off Pattern
    ctx.lineWidth = 90;
    ctx.setLineDash([50, 100]);
    ctx.strokeStyle = 'rgba(0, 100, 255, 0.05)';
    ctx.stroke();
    
    // Kerbs Red/White
    ctx.lineWidth = 4;
    ctx.setLineDash([15, 15]);
    ctx.strokeStyle = '#fff'; ctx.stroke();
    ctx.strokeStyle = '#ff3300'; ctx.lineDashOffset = 15; ctx.stroke();
    ctx.setLineDash([]);
}

function animate() {
    ctx.fillStyle = 'rgba(0, 0, 0, 0.2)';
    ctx.fillRect(0, 0, width, height);
    drawTrack();

    particles = particles.filter(p => p.life > 0);
    particles.forEach(p => { p.update(); p.draw(); });

    Object.values(racers).forEach(r => {
        r.speed += (r.targetSpeed - r.speed) * 0.01;
        r.progress = (r.progress + r.speed) % 1;
        
        const pos = getTrackPoint(r.progress, r.lane);
        if (Math.random() > 0.6) particles.push(new Particle(pos.x, pos.y, r.color));

        ctx.save();
        ctx.translate(pos.x, pos.y); ctx.rotate(pos.angle);
        ctx.shadowBlur = 15; ctx.shadowColor = r.color;
        ctx.fillStyle = '#000'; ctx.strokeStyle = r.color; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(-12, -7); ctx.lineTo(18, 0); ctx.lineTo(-12, 7); ctx.lineTo(-8, 0); ctx.closePath(); ctx.fill(); ctx.stroke();
        ctx.shadowBlur = 0; ctx.fillStyle = '#fff'; ctx.font = 'bold 10px Orbitron'; ctx.textAlign = 'center';
        ctx.fillText(r.data.pair.replace('idr','').toUpperCase(), 0, -12);
        ctx.restore();
    });
    requestAnimationFrame(animate);
}

setInterval(fetchData, 2000);
fetchData();
animate();
