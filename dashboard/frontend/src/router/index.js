import { createRouter, createWebHistory } from 'vue-router'
import { useAuth } from '../composables/useAuth'

// Lazy-loaded components
const PublicDashboard = () => import('../App.vue')
const LiveDashboard = () => import('../views/LiveDashboard.vue')

const routes = [
  {
    path: '/',
    name: 'public',
    component: PublicDashboard,
    meta: { requiresAuth: false },
  },
  {
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

// Route guard — redirect unauthenticated users away from /dashboard-live
router.beforeEach((to) => {
  const { isAuthenticated } = useAuth()
  if (to.meta.requiresAuth && !isAuthenticated.value) {
    return { path: '/', query: { redirect: to.fullPath } }
  }
})

export default router
