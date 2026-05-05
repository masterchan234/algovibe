import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router/index.js'
import './style.css'

// RouterView entry point — wraps App.vue and LiveDashboard.vue
import RouterRoot from './RouterRoot.vue'

const app = createApp(RouterRoot)
app.use(createPinia())
app.use(router)
app.mount('#app')
