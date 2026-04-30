import { ref } from 'vue'

const notifications = ref([])

export function useNotifications() {
  function notify(payload) {
    const { title, message, type = 'info', duration = 5000 } = payload
    const id = Date.now() + Math.random()
    
    notifications.value.push({ id, title, message, type })
    
    if (duration > 0) {
      setTimeout(() => {
        remove(id)
      }, duration)
    }
    
    return id
  }

  function remove(id) {
    notifications.value = notifications.value.filter(n => n.id !== id)
  }

  return {
    notifications,
    notify,
    remove
  }
}
