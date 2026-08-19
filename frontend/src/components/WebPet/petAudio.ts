/**
 * Web Pet 纯代码合成音效系统 (Web Audio API)
 * 零外部音频文件依赖，毫秒级即时发声
 */

class PetAudioEngine {
  private ctx: AudioContext | null = null

  private getContext(): AudioContext | null {
    if (typeof window === 'undefined') return null
    if (!this.ctx) {
      const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
      if (AudioCtx) {
        this.ctx = new AudioCtx()
      }
    }
    if (this.ctx && this.ctx.state === 'suspended') {
      void this.ctx.resume()
    }
    return this.ctx
  }

  /** 点击/气泡弹出音 */
  playPop() {
    const ctx = this.getContext()
    if (!ctx) return
    try {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      const now = ctx.currentTime

      osc.type = 'sine'
      osc.frequency.setValueAtTime(450, now)
      osc.frequency.exponentialRampToValueAtTime(900, now + 0.08)

      gain.gain.setValueAtTime(0.15, now)
      gain.gain.exponentialRampToValueAtTime(0.01, now + 0.08)

      osc.connect(gain)
      gain.connect(ctx.destination)

      osc.start(now)
      osc.stop(now + 0.08)
    } catch {
      // ignore
    }
  }

  /** 开心/抚摸/爱心音效 (清脆和弦) */
  playHappy() {
    const ctx = this.getContext()
    if (!ctx) return
    try {
      const notes = [523.25, 659.25, 783.99, 1046.5] // C5, E5, G5, C6
      notes.forEach((freq, i) => {
        const osc = ctx.createOscillator()
        const gain = ctx.createGain()
        const now = ctx.currentTime + i * 0.05

        osc.type = 'triangle'
        osc.frequency.setValueAtTime(freq, now)

        gain.gain.setValueAtTime(0.12, now)
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.25)

        osc.connect(gain)
        gain.connect(ctx.destination)

        osc.start(now)
        osc.stop(now + 0.25)
      })
    } catch {
      // ignore
    }
  }

  /** 进食/嚼嚼嚼音效 */
  playEat() {
    const ctx = this.getContext()
    if (!ctx) return
    try {
      const now = ctx.currentTime
      for (let i = 0; i < 3; i++) {
        const osc = ctx.createOscillator()
        const gain = ctx.createGain()
        const t = now + i * 0.09

        osc.type = 'sine'
        osc.frequency.setValueAtTime(600 - i * 80, t)
        osc.frequency.exponentialRampToValueAtTime(300, t + 0.06)

        gain.gain.setValueAtTime(0.1, t)
        gain.gain.exponentialRampToValueAtTime(0.01, t + 0.06)

        osc.connect(gain)
        gain.connect(ctx.destination)

        osc.start(t)
        osc.stop(t + 0.06)
      }
    } catch {
      // ignore
    }
  }

  /** 萌宠叫声模拟 (轻快变频音) */
  playMeow() {
    const ctx = this.getContext()
    if (!ctx) return
    try {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      const now = ctx.currentTime

      osc.type = 'sine'
      osc.frequency.setValueAtTime(580, now)
      osc.frequency.exponentialRampToValueAtTime(880, now + 0.12)
      osc.frequency.exponentialRampToValueAtTime(650, now + 0.3)

      gain.gain.setValueAtTime(0.01, now)
      gain.gain.linearRampToValueAtTime(0.15, now + 0.08)
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.32)

      osc.connect(gain)
      gain.connect(ctx.destination)

      osc.start(now)
      osc.stop(now + 0.32)
    } catch {
      // ignore
    }
  }

  /** 番茄钟完成/成就提示音 (叮咚) */
  playChime() {
    const ctx = this.getContext()
    if (!ctx) return
    try {
      const freqs = [659.25, 880] // E5, A5
      freqs.forEach((freq, idx) => {
        const osc = ctx.createOscillator()
        const gain = ctx.createGain()
        const now = ctx.currentTime + idx * 0.12

        osc.type = 'sine'
        osc.frequency.setValueAtTime(freq, now)

        gain.gain.setValueAtTime(0.18, now)
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.6)

        osc.connect(gain)
        gain.connect(ctx.destination)

        osc.start(now)
        osc.stop(now + 0.6)
      })
    } catch {
      // ignore
    }
  }
}

export const petAudio = new PetAudioEngine()
