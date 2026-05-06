<template>
  <div class="not-found">
    <div class="nf-container">
      <h1 class="nf-code">404</h1>
      <h2 class="nf-title">COORDINATES LOST</h2>
      <p class="nf-desc">
        The requested module or trade vector does not exist in the current grid.
      </p>
      <button @click="$router.push('/')" class="nf-btn">
        RETURN TO COMMAND CENTER
      </button>
    </div>
    <div class="nf-grid"></div>
  </div>
</template>

<style scoped>
.not-found {
  min-height: 100vh;
  background: #020308;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-family: 'Inter', system-ui, sans-serif;
  position: relative;
  overflow: hidden;
}

.nf-container {
  text-align: center;
  z-index: 10;
  max-width: 400px;
  padding: 40px;
}

.nf-code {
  font-size: 120px;
  font-weight: 900;
  margin: 0;
  background: linear-gradient(to bottom, #fff, #222);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  line-height: 1;
}

.nf-title {
  font-size: 18px;
  font-weight: 800;
  letter-spacing: 4px;
  margin: 20px 0 10px 0;
  color: #fff;
}

.nf-desc {
  font-size: 14px;
  color: #555;
  line-height: 1.6;
  margin-bottom: 40px;
}

.nf-btn {
  background: #fff;
  color: #000;
  border: none;
  border-radius: 12px;
  padding: 16px 32px;
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 1px;
  cursor: pointer;
  transition: all 0.3s ease;
}

.nf-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 20px rgba(255, 255, 255, 0.1);
}

.nf-grid {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background-image: 
    linear-gradient(rgba(255, 255, 255, 0.02) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.02) 1px, transparent 1px);
  background-size: 40px 40px;
  perspective: 1000px;
  transform: rotateX(60deg) translateY(-20%);
  mask-image: radial-gradient(circle at center, black, transparent 80%);
}
</style>
