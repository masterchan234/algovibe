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
    // Guarded dry-run member dashboard — requires auth + mode selection
    path: '/dashboard-dry',
    name: 'dry',
    component: PublicDashboard,
    meta: { requiresAuth: true },
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

  // Temporary debug logs — remove once mode-selection flow is verified
  console.log('[Router Guard] Auth Status:', isAuthenticated.value, '| LocalMode:', localStorage.getItem('algovibe_mode'), '| Target Route:', to.fullPath)

  // Unauthenticated users cannot access protected routes
  if (to.meta.requiresAuth && !isAuthenticated.value) {
    console.log('[Router Guard] → Redirecting unauthenticated user to /')
    return { path: '/', query: { redirect: to.fullPath } }
  }

  // Authenticated users on a guarded dashboard without a mode selection
  // must choose their operating mode first
  const requiresModeSelection = (to.name === 'live' || to.name === 'dry')
  if (requiresModeSelection && isAuthenticated.value && !hasModeSelected.value) {
    console.log('[Router Guard] → No mode selected, redirecting to /mode-select')
    return { path: '/mode-select' }
  }
})

export default router
