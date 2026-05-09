<template>
  <div class="about-page">
    <header class="about-header">
      <div class="container head-inner">
        <div class="brand">
          <p class="kicker">ALGOVIBE SYSTEM DOCS</p>
          <h1>About Us</h1>
          <p class="subtitle">Penjelasan implementasi runtime Algovibe yang berjalan saat ini.</p>
        </div>
        <nav class="top-nav" aria-label="About navigation">
          <a href="/login">Landing</a>
          <a href="#flow">Flow</a>
          <a href="#components">Components</a>
          <a href="#safety">Safety</a>
          <a href="#public">Public Data</a>
        </nav>
      </div>
    </header>

    <main class="container about-main">
      <section class="hero">
        <p class="hero-kicker">WHAT ALGOVIBE IS</p>
        <h2>
          Algovibe adalah sistem trading crypto berbasis event, dengan pipeline market data real-time,
          analisis teknikal + AI, eksekusi order, monitoring exit, dan dashboard publik/member.
        </h2>
        <div class="chips">
          <span>Main Entrypoint: <b>main.py -> app/interfaces/cli.py -> app/bootstrap.py</b></span>
          <span>Bot Runtime: <b>app/application/scalper_bot.py</b></span>
          <span>Web/API Runtime: <b>dashboard/server.py</b></span>
        </div>
      </section>

      <section id="flow" class="block">
        <h3>End-to-End Flow</h3>
        <ol class="steps">
          <li>
            <strong>Bootstrap runtime</strong>
            <p>
              Saat startup, sistem memasang event loop policy (uvloop jika tersedia), preload model prediktor,
              dan mengaktifkan PID lock untuk mencegah dua instance bot aktif bersamaan.
            </p>
          </li>
          <li>
            <strong>Initialize trading core</strong>
            <p>
              <code>ScalperBot</code> membangun koneksi exchange Indodax, menyalakan market websocket,
              private websocket, oracle engine, exit manager, deadman switch, dan recovery state dari database.
            </p>
          </li>
          <li>
            <strong>Warm-up historical context</strong>
            <p>
              Bot melakukan backfill OHLCV per market IDR untuk membentuk context harga (1m), lalu menyimpan
              ringkasan perubahan (5m/15m/1h) ke storage agar scoring tidak mulai dari nol.
            </p>
          </li>
          <li>
            <strong>Market cycle and decisioning</strong>
            <p>
              Siklus utama memproses ticker/trade stream, menghitung sinyal teknikal dan scoring, lalu mengeksekusi
              trade sesuai mode (dry/live), gate risiko, dan rule strategy yang aktif di konfigurasi.
            </p>
          </li>
          <li>
            <strong>Position and order lifecycle</strong>
            <p>
              Exit manager memonitor posisi terbuka, update order private feed, rekonsiliasi order live saat restart,
              dan menjaga state posisi tetap sinkron dengan exchange.
            </p>
          </li>
          <li>
            <strong>API, dashboard, and intelligence delivery</strong>
            <p>
              Service dashboard menyiapkan endpoint publik/member. Landing publik melakukan polling API,
              sedangkan member dashboard memakai autentikasi session/JWT dengan websocket terkontrol.
            </p>
          </li>
        </ol>
      </section>

      <section id="components" class="block">
        <h3>Komponen Utama yang Terimplementasi</h3>
        <div class="grid">
          <article class="card">
            <h4>Trading Orchestrator</h4>
            <p>
              Runtime inti ada di <code>ScalperBot</code> dan use case aplikasi. Komponen ini mengatur lifecycle bot,
              loop market cycle, sinkronisasi balance, dan trigger eksekusi.
            </p>
          </article>
          <article class="card">
            <h4>Market & Private Streams</h4>
            <p>
              Market stream menangani data ticker/trade publik. Private stream menangani update order/akun untuk
              memastikan status order dan posisi real-time saat mode live.
            </p>
          </article>
          <article class="card">
            <h4>Oracle + Predictor Layer</h4>
            <p>
              Layer analisis menggabungkan teknikal, scoring, dan komponen AI/ML (termasuk worker LLM) untuk
              menghasilkan insight dan dukungan keputusan trading.
            </p>
          </article>
          <article class="card">
            <h4>Persistence Layer</h4>
            <p>
              State bot, histori ticker, posisi, order book, event log, laporan, dan telemetry disimpan melalui
              abstraction database di <code>app/infrastructure/database.py</code>.
            </p>
          </article>
          <article class="card">
            <h4>Dashboard/API Layer</h4>
            <p>
              <code>dashboard/server.py</code> menyajikan API publik dan member, static frontend build,
              autentikasi JWT/session, serta emit event dashboard untuk pengguna yang terautentikasi.
            </p>
          </article>
          <article class="card">
            <h4>News & Intelligence</h4>
            <p>
              Service <code>news_maker</code> menjadi jalur tunggal pembuatan konten intelijen market,
              dengan trigger manual/otomatis dan integrasi LLM sesuai pipeline yang aktif.
            </p>
          </article>
        </div>
      </section>

      <section id="safety" class="block">
        <h3>Risk & Safety Controls</h3>
        <ul class="plain-list">
          <li>PID lock untuk mencegah multi-instance menulis ke state yang sama.</li>
          <li>Pemisahan mode dry run vs live agar validasi strategi aman sebelum eksekusi real.</li>
          <li>Recovery startup: resume open positions dan reconcile open orders ke database.</li>
          <li>Deadman switch dan heartbeat untuk mitigasi kondisi koneksi/exchange abnormal.</li>
          <li>Session enforcement: satu login aktif per user, sesi lama dapat diputus paksa.</li>
          <li>Websocket anonymous diblokir; akses event sensitif memerlukan autentikasi valid.</li>
        </ul>
      </section>

      <section id="public" class="block">
        <h3>Data Publik yang Ditampilkan di Landing</h3>
        <p>
          Landing page publik menggunakan endpoint <code>/api/public/landing-data</code> dengan polling periodik.
          Data yang ditampilkan mencakup ringkasan signal market, artikel intelijen, market stats, dan activity log
          publik yang sudah disanitasi. Konten hero "Market Intelligence Brief" diprioritaskan dari output
          <code>MARKET_INTEL_BRIEF</code> pipeline LLM ketika tersedia.
        </p>
        <p class="note">
          Catatan: halaman publik hanya menampilkan data yang aman untuk konsumsi publik, bukan detail internal order
          execution atau payload sensitif akun.
        </p>
      </section>
    </main>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'

onMounted(() => {
  document.title = 'About Us | Algovibe Intelligence'
})
</script>

<style scoped>
.about-page {
  min-height: 100vh;
  color: #d9e3ea;
  background:
    radial-gradient(circle at 8% 6%, rgba(52, 106, 141, 0.28), transparent 36%),
    radial-gradient(circle at 90% 14%, rgba(168, 117, 47, 0.24), transparent 28%),
    linear-gradient(180deg, #091019 0%, #0b1612 100%);
  font-family: 'IBM Plex Sans', 'Space Grotesk', 'Segoe UI', sans-serif;
}

.container {
  max-width: 1120px;
  margin: 0 auto;
  padding: 0 24px;
}

.about-header {
  position: sticky;
  top: 0;
  z-index: 30;
  border-bottom: 1px solid rgba(133, 168, 192, 0.22);
  background: rgba(8, 15, 24, 0.9);
  backdrop-filter: blur(8px);
}

.head-inner {
  min-height: 92px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 20px;
}

.kicker {
  margin: 0 0 4px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.8px;
  color: #84adc7;
}

.brand h1 {
  margin: 0;
  font-size: 30px;
  font-family: 'Source Serif 4', 'Georgia', serif;
  color: #f2f8fc;
}

.subtitle {
  margin: 4px 0 0;
  color: #9ab3c4;
  font-size: 13px;
}

.top-nav {
  display: flex;
  align-items: center;
  gap: 16px;
}

.top-nav a {
  color: #b7ccdb;
  text-decoration: none;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.6px;
}

.top-nav a:hover {
  color: #d7edf9;
}

.about-main {
  padding-top: 36px;
  padding-bottom: 60px;
}

.hero {
  border: 1px solid rgba(123, 168, 194, 0.2);
  border-radius: 16px;
  background: rgba(8, 19, 29, 0.62);
  padding: 26px;
}

.hero-kicker {
  margin: 0 0 12px;
  color: #90b8cf;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1.4px;
}

.hero h2 {
  margin: 0;
  color: #f4f9fc;
  line-height: 1.18;
  font-size: clamp(24px, 4vw, 44px);
  font-family: 'Source Serif 4', 'Georgia', serif;
}

.chips {
  margin-top: 18px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.chips span {
  border: 1px solid rgba(131, 177, 204, 0.26);
  background: rgba(9, 26, 37, 0.72);
  color: #a6c3d6;
  font-size: 11px;
  border-radius: 999px;
  padding: 7px 12px;
}

.block {
  margin-top: 34px;
  border: 1px solid rgba(121, 163, 186, 0.2);
  border-radius: 14px;
  background: rgba(9, 20, 30, 0.56);
  padding: 22px;
}

.block h3 {
  margin: 0 0 14px;
  font-size: 28px;
  color: #eff7fc;
  font-family: 'Source Serif 4', 'Georgia', serif;
}

.steps {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 12px;
}

.steps li {
  color: #c5d4df;
}

.steps strong {
  color: #e9f5fd;
}

.steps p {
  margin: 5px 0 0;
  line-height: 1.65;
  color: #a5bdcc;
}

.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.card {
  border: 1px solid rgba(125, 169, 193, 0.2);
  border-radius: 12px;
  background: rgba(8, 22, 33, 0.62);
  padding: 16px;
}

.card h4 {
  margin: 0 0 8px;
  color: #edf7fc;
  font-size: 16px;
}

.card p {
  margin: 0;
  color: #a9c1cf;
  line-height: 1.62;
  font-size: 14px;
}

.plain-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 10px;
}

.plain-list li {
  color: #b4c9d8;
  line-height: 1.6;
}

.block p {
  margin: 0;
  color: #acc1cf;
  line-height: 1.7;
}

.note {
  margin-top: 12px;
  color: #88a8bb;
  font-size: 13px;
}

code {
  color: #9dd8f2;
  background: rgba(16, 39, 54, 0.6);
  border-radius: 6px;
  padding: 2px 6px;
}

@media (max-width: 900px) {
  .head-inner {
    align-items: flex-start;
    flex-direction: column;
    padding: 16px 24px;
  }

  .grid {
    grid-template-columns: 1fr;
  }

  .top-nav {
    flex-wrap: wrap;
  }
}

@media (max-width: 640px) {
  .container {
    padding: 0 16px;
  }

  .hero,
  .block {
    padding: 16px;
  }

  .block h3 {
    font-size: 24px;
  }
}
</style>
