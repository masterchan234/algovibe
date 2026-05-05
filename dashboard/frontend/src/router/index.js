import { createRouter, createWebHistory } from 'vue-router'
import { useAuth } from '../composables/useAuth'
import { useMode } from '../composables/useMode'

// Lazy-loaded components
const PublicDashboard = () => import('../App.vue')
const LiveDashboard   = () => import('../views/LiveDashboard.vue')
const ModeSelector    = () => import('../views/ModeSelector.vue')

const routes = [
  {
    // Public simulation dashboard — accessible without auth
    path: '/',
    name: 'public',
    component: PublicDashboard,
    meta: { requiresAuth: false },
  },
  {
    // Dry-run dashboard — same component as '/', reached from mode selector
    path: '/dashboard',
    name: 'dashboard',
    component: PublicDashboard,
    meta: { requiresAuth: false },
  },
  {
    // Post-login mode selection screen
    path: '/mode-select',
    name: 'mode-select',
    component: ModeSelector,
    meta: { requiresAuth: true },
  },
  {
    // Guarded live member dashboard
    path: '/dashboard-live',
    name: 'live',
    component: LiveDashboard,
    meta: { requiresAuth: true },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// Route guard
router.beforeEach((to) => {
  const { isAuthenticated } = useAuth()
  const { hasModeSelected } = useMode()

  // Unauthenticated users cannot access protected routes
  if (to.meta.requiresAuth && !isAuthenticated.value) {
    return { path: '/', query: { redirect: to.fullPath } }
  }

  // Authenticated users hitting /dashboard-live without selecting a mode
  // are gently redirected to the mode selector first
  if (to.name === 'live' && isAuthenticated.value && !hasModeSelected.value) {
    return { path: '/mode-select' }
  }
})

export default router
