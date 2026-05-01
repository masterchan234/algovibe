import { ref } from 'vue'

const isMuted = ref(localStorage.getItem('algovibe_muted') === 'true')

export function useAudio() {
  function toggleMute() {
    isMuted.value = !isMuted.value
    localStorage.setItem('algovibe_muted', isMuted.value.toString())
  }

  function playBeep(freq = 440, duration = 0.1, type = 'sine') {
    if (isMuted.value) return
    
    try {
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)()
      const oscillator = audioCtx.createOscillator()
      const gainNode = audioCtx.createGain()

      oscillator.type = type
      oscillator.frequency.setValueAtTime(freq, audioCtx.currentTime)
      
      gainNode.gain.setValueAtTime(0.1, audioCtx.currentTime)
      gainNode.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + duration)

      oscillator.connect(gainNode)
      gainNode.connect(audioCtx.destination)

      oscillator.start()
      oscillator.stop(audioCtx.currentTime + duration)
    } catch (e) {
      console.warn('Audio play failed:', e)
    }
  }

  function speak(text) {
    if (isMuted.value) return
    if (!window.speechSynthesis) return

    const utterance = new SpeechSynthesisUtterance(text)
    utterance.rate = 1.1
    utterance.pitch = 1.0
    // Try to find a nice English/Indonesian female voice if available
    const voices = window.speechSynthesis.getVoices()
    const preferredVoice = voices.find(v => v.name.includes('Google') && v.lang.includes('en')) || voices[0]
    if (preferredVoice) utterance.voice = preferredVoice

    window.speechSynthesis.speak(utterance)
  }

  return {
    isMuted,
    toggleMute,
    playBeep,
    speak
  }
}
