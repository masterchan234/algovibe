<template>
  <div class="about-page">
    <header class="about-header">
      <div class="container head-inner">
        <div class="brand">
          <p class="kicker">ALGOVIBE SYSTEM DOCS</p>
          <h1>About Us</h1>
          <p class="subtitle">Penjelasan faktual tentang alur pengambilan keputusan trading yang berjalan di runtime saat ini.</p>
        </div>
        <nav class="top-nav" aria-label="About navigation">
          <a href="/login" class="nav-home">Back to Landing</a>
          <div class="nav-pills">
            <button
              v-for="section in sections"
              :key="section.id"
              type="button"
              class="nav-pill"
              :class="{ active: activeSection === section.id }"
              @click="goToSection(section.id)"
            >
              {{ section.label }}
            </button>
          </div>
        </nav>
      </div>
    </header>

    <main class="container about-main">
      <section class="hero">
        <p class="hero-kicker">ABOUT ALGOVIBE</p>
        <h2>
          Coding boleh dimulai dari vibe, tapi eksekusi tetap harus tunduk pada data.
          Algovibe lahir dari eksperimen itu: ide kreatif dibantu Agentic AI, lalu dipaksa disiplin oleh market reality.
        </h2>
        <p class="hero-tagline">AI = <strong>Aku dan Intelligence</strong></p>
        <div class="chips">
          <span>Eksperimen: <b>Vibe Coding x Real-Time Trading</b></span>
          <span>Fokus: <b>Crypto Scalping (Binance Spot)</b></span>
          <span>Prinsip: <b>Creativity in Build, Discipline in Execution</b></span>
        </div>
      </section>

      <section id="what-is" class="block">
        <h3>Apa Itu Algovibe</h3>
        <p>
          Algovibe adalah sistem trading bot untuk market USDT yang dibangun dari kolaborasi manusia + AI.
          Awalnya berangkat dari bot sederhana, tapi berkembang menjadi arsitektur yang memadukan indikator teknikal,
          pembacaan konteks market, veto AI, dan loop pembelajaran agar keputusan tidak berhenti di pola statis.
        </p>
        <p class="note">
          Tujuan utamanya bukan sekadar "asal buy/sell", tetapi membangun sistem yang bisa menilai konteks,
          mengendalikan risiko, dan belajar dari hasil eksekusinya sendiri.
        </p>
      </section>

      <section id="who-we-are" class="block">
        <h3>Siapa Kita</h3>
        <div class="grid">
          <article class="card">
            <h4>Dia</h4>
            <p>
              Dia adalah seorang pengembang yang berpengalaman lebih dari 20 tahun dalam pengembangan software,
              disini dia merancang ide, mendefinisikan risiko, dan memutuskan apa yang layak dijalankan di lingkungan real.
            </p>
          </article>
          <article class="card">
            <h4>Saya (GitHub Copilot)</h4>
            <p>
              Saya berperan sebagai partner engineering: membantu menerjemahkan ide jadi implementasi,
              mempercepat iterasi, dan menjaga kualitas teknis sepanjang pengembangan.
            </p>
          </article>
          <article class="card">
            <h4>Konsep AI di Algovibe</h4>
            <p>
              Di proyek ini, AI bukan hanya <em>Artificial Intelligence</em>. AI juga dimaknai sebagai
              <strong>Aku dan Intelligence</strong>: kolaborasi antara intuisi pembuat sistem dengan mesin analitik
              yang disiplin terhadap data.
            </p>
          </article>
          <article class="card">
            <h4>Partner AI Lain Dalam Riset</h4>
            <p>
              Dalam eksplorasi awal, kamu juga menandemkan Agentic AI Gemini sebagai sparring teknis.
              Kombinasi ini membuat proses riset cepat, tapi tetap diarahkan ke arsitektur yang bisa diuji dengan data.
            </p>
          </article>
          <article class="card">
            <h4>Siapa Ambil Keputusan Akhir</h4>
            <p>
              Keputusan akhir tetap di tangan kamu. AI mempercepat analisis dan coding,
              tetapi kebijakan strategi, risk appetite, dan kontrol produksi tetap manusia yang menentukan.
            </p>
          </article>
        </div>
      </section>

      <section id="how-we-work" class="block">
        <h3>Tiga Fondasi yang Membentuk Algovibe</h3>
        <ul class="plain-list">
          <li><strong>Algoritma dengan "Hati Nurani"</strong>: layer AI lokal digunakan sebagai filter terakhir (veto power) saat market terlihat anomali walau sinyal teknikal tampak menarik.</li>
          <li><strong>The Oracle</strong>: otak sentral yang membaca market regime (bullish, bearish, atau choppy) dan menyesuaikan sikap bot agar tidak one-size-fits-all.</li>
          <li><strong>Belajar dari Kesalahan</strong>: sistem melakukan evaluasi berkala dari hasil trade, lalu insight yang relevan dipakai untuk penyesuaian konteks jangka pendek.</li>
        </ul>
      </section>

      <section id="safety" class="block">
        <h3>Prinsip Keamanan Sistem</h3>
        <ul class="plain-list">
          <li>Mode dry run dan live dipisah agar eksperimen tidak langsung menyentuh dana real.</li>
          <li>Perhitungan hasil memperhitungkan komponen biaya trading (maker/taker) dan komponen pajak sesuai konfigurasi sistem.</li>
          <li>Monitoring posisi tetap berjalan saat restart melalui recovery dan reconcile state.</li>
          <li>Layer exit management menjaga disiplin TP/SL pada posisi yang sudah terbuka.</li>
        </ul>
      </section>

      <section id="public" class="block">
        <h3>Peran Halaman Landing</h3>
        <p>
          Landing adalah jendela publik Algovibe: menampilkan ringkasan market, artikel, dan brief dalam format yang mudah dibaca.
          Halaman ini dirancang untuk menunjukkan progres riset secara terbuka, tanpa membuka detail sensitif dari engine internal.
        </p>
        <p class="note">
          Intinya: ini proyek riset yang serius. AI dipakai bukan hanya untuk menjawab pertanyaan,
          tapi sebagai komponen decision-making di sistem real-time yang kompleks.
        </p>
      </section>

      <div class="page-actions">
        <button type="button" class="page-action-btn" @click="goToSection('what-is')">Kembali ke Apa Itu Algovibe</button>
        <button type="button" class="page-action-btn secondary" @click="goToTop">Kembali ke Atas</button>
      </div>
    </main>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'

const sections = [
  { id: 'what-is', label: 'Apa Itu' },
  { id: 'who-we-are', label: 'Siapa Kita' },
  { id: 'how-we-work', label: 'Cara Kerja' },
  { id: 'safety', label: 'Safety' },
  { id: 'public', label: 'Landing' },
]

const activeSection = ref('what-is')

function goToSection(sectionId) {
  const el = document.getElementById(sectionId)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  history.replaceState(null, '', `#${sectionId}`)
  activeSection.value = sectionId
}

function goToTop() {
  window.scrollTo({ top: 0, behavior: 'smooth' })
  history.replaceState(null, '', window.location.pathname)
}

function syncActiveSection() {
  const offset = 140
  let current = sections[0].id

  for (const section of sections) {
    const el = document.getElementById(section.id)
    if (!el) continue
    const top = el.getBoundingClientRect().top
    if (top - offset <= 0) {
      current = section.id
    }
  }

  activeSection.value = current
}

onMounted(() => {
  document.title = 'About Us | Algovibe Intelligence'
  const hash = window.location.hash.replace('#', '')
  if (hash && sections.some((section) => section.id === hash)) {
    requestAnimationFrame(() => goToSection(hash))
  }
  window.addEventListener('scroll', syncActiveSection, { passive: true })
  syncActiveSection()
})

onUnmounted(() => {
  window.removeEventListener('scroll', syncActiveSection)
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
  align-items: flex-start;
  gap: 14px;
}

.nav-home {
  color: #b7ccdb;
  text-decoration: none;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.6px;
  border: 1px solid rgba(126, 171, 195, 0.22);
  background: rgba(10, 25, 37, 0.68);
  border-radius: 999px;
  padding: 10px 14px;
  white-space: nowrap;
}

.nav-home:hover {
  color: #d7edf9;
}

.nav-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.nav-pill {
  border: 1px solid rgba(126, 171, 195, 0.18);
  background: rgba(8, 20, 29, 0.52);
  color: #a9c0cf;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.5px;
  border-radius: 999px;
  padding: 10px 12px;
  cursor: pointer;
  transition: border-color 0.2s ease, color 0.2s ease, background 0.2s ease;
}

.nav-pill:hover,
.nav-pill.active {
  color: #e8f4fb;
  border-color: rgba(138, 195, 226, 0.42);
  background: rgba(15, 38, 54, 0.82);
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

.hero-tagline {
  margin: 14px 0 0;
  color: #9fd6ee;
  font-size: 15px;
  letter-spacing: 0.4px;
}

.hero-tagline strong {
  color: #e7f6ff;
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
  scroll-margin-top: 128px;
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

.page-actions {
  margin-top: 28px;
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.page-action-btn {
  border: 1px solid rgba(125, 171, 196, 0.24);
  background: rgba(10, 24, 35, 0.78);
  color: #d3e6f1;
  padding: 10px 14px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.page-action-btn.secondary {
  color: #9ec0d3;
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
    width: 100%;
    flex-direction: column;
  }

  .nav-pills {
    width: 100%;
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
