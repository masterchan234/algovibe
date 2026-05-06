import { computed } from 'vue'
import { useTradeStore } from '../stores/trade'

/**
 * useSocket Composable
 * Provides access to the global SocketIO instance managed by the trade store.
 */
export function useSocket() {
  const store = useTradeStore()
  
  const socket = computed(() => store.socket)
  const connected = computed(() => store.connected)
  
  return {
    socket,
    connected
  }
}
