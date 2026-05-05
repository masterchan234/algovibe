import { createRouter, createWebHistory } from 'vue-router'
import { useAuth } from '../composables/useAuth'
import { useMode } from '../composables/useMode'

// Lazy-loaded components
const PublicDashboard = () => import('../App.vue')
const LiveDashboard   = () => import('../views/LiveDashboard.vue')
const ModeSelector    = () => import('../views/ModeSelector.vue')
const LoginView       = () => import('../views/LoginView.vue')
const NotFound        = () => import('../views/NotFound.vue')

const routes = [
  {
    // Dedicated login page — auth gate for protected routes
    path: '/login',
    name: 'login',
    component: LoginView,
    meta: { requiresAuth: false },
  },
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
  {
    // Catch-all 404 — must be last
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: NotFound,
    meta: { requiresAuth: false },
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

  // Redirect already-authenticated users away from /login
  if (to.name === 'login' && isAuthenticated.value) {
    return { path: '/mode-select' }
  }

  // Unauthenticated users cannot access protected routes
  if (to.meta.requiresAuth && !isAuthenticated.value) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  // Authenticated users on a guarded dashboard without a mode selection
  // must choose their operating mode first
  const requiresModeSelection = (to.name === 'live' || to.name === 'dry')
  if (requiresModeSelection && isAuthenticated.value && !hasModeSelected.value) {
    return { path: '/mode-select' }
  }
})

export default router
