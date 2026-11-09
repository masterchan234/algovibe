import { createRouter, createWebHistory } from 'vue-router'
import { useAuth } from '../composables/useAuth'
import { useMode } from '../composables/useMode'

// Lazy-loaded components
const LoginView       = () => import('../views/Login.vue')
const AboutView       = () => import('../views/About.vue')
const BacktestReport  = () => import('../views/BacktestReport.vue')
const AppDashboard    = () => import('../App.vue')
const NotFound        = () => import('../views/NotFound.vue')

const routes = [
  {
    path: '/login',
    name: 'login',
    component: LoginView,
    meta: { requiresAuth: false },
  },
  {
    path: '/articles/:slug',
    name: 'article',
    component: LoginView,
    meta: { requiresAuth: false },
  },
  {
    path: '/about-us',
    name: 'about',
    component: AboutView,
    meta: { requiresAuth: false },
  },
  {
    path: '/backtest-report',
    name: 'backtest-report',
    component: BacktestReport,
    meta: { requiresAuth: false },
  },
  {
    path: '/',
    redirect: '/login',
  },
  {
    // Dry-run dashboard
    path: '/dashboard',
    name: 'dry',
    component: AppDashboard,
    meta: { requiresAuth: true, requiresMode: 'dry' },
  },
  {
    // Live member dashboard
    path: '/dashboard-live',
    name: 'live',
    component: AppDashboard,
    meta: { requiresAuth: true, requiresMode: 'live' },
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: NotFound,
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

function getSafeRedirectPath(value) {
  if (!value || typeof value !== 'string') return null
  if (!value.startsWith('/')) return null
  if (value.startsWith('//')) return null
  if (value.startsWith('/login')) return null
  return value
}

// Route guard
router.beforeEach(async (to) => {
  const { isAuthenticated, token, userInfo } = useAuth()
  const { hasModeSelected, selectedMode } = useMode()

  // 1. Redirect unauthenticated users to login
  if (to.meta.requiresAuth) {
    if (!isAuthenticated.value) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }
    
    // Verify token with backend to prevent loading dashboard with stale session
    try {
      const backendUrl = import.meta.env.DEV ? (import.meta.env.VITE_BACKEND_URL || 'http://127.0.0.1:8080') : window.location.origin
      const res = await fetch(`${backendUrl}/api/check-auth`, {
        headers: { Authorization: `Bearer ${token.value}` }
      })
      if (!res.ok) {
        token.value = null
        userInfo.value = null
        localStorage.removeItem('algovibe_jwt')
        return { name: 'login', query: { redirect: to.fullPath } }
      }
    } catch (e) {
      // Ignore network errors, let it pass to allow offline caching or subsequent error handling
    }
  }

  // 2. Prevent authenticated users from going back to login
  if (to.name === 'login' && isAuthenticated.value) {
    const redirectPath = getSafeRedirectPath(to.query?.redirect)
    if (redirectPath) {
      return redirectPath
    }
    return { name: 'dry' } // Default to dry mode
  }

  // 3. Set default mode for authenticated users if not set
  if (isAuthenticated.value && !hasModeSelected.value) {
    selectedMode.value = 'dry'
  }

  // 4. Ensure mode matches the route (e.g. can't go to /dashboard-live while in dry mode)
  if (to.meta.requiresMode && to.meta.requiresMode !== selectedMode.value) {
    // If they manually navigate to a mode, let the app update the state instead of blocking
    selectedMode.value = to.meta.requiresMode
  }
})

export default router
