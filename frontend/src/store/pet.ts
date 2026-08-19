import { create } from 'zustand'
import { petAudio } from '../components/WebPet/petAudio'

export type PetSkin = 'cat' | 'shiba' | 'fairy' | 'robot'
export type PetState = 'idle' | 'walking' | 'dragged' | 'happy' | 'sleeping' | 'eating' | 'talking'
export type FoodType = 'shrimp' | 'riceball' | 'coffee' | 'candy' | 'fish' | 'pudding'

export interface VisualEffect {
  id: string
  type: 'heart' | 'crumbs' | 'sparkle' | 'levelUp'
  x: number
  y: number
  text?: string
}


interface PetStoreState {
  // 基础信息与偏好
  name: string
  skin: PetSkin
  enabled: boolean
  isMinimized: boolean
  soundEnabled: boolean
  boundRoleId: number | null

  // 物理与动作状态
  position: { x: number; y: number }
  state: PetState
  direction: 'left' | 'right'

  // 养成属性数值 (0 - 100)
  hunger: number
  mood: number
  energy: number
  affinity: number

  // 对话气泡
  dialogue: {
    text: string | null
    visible: boolean
    isTyping: boolean
    timestamp: number
  }

  // 番茄钟专注模式
  pomodoro: {
    active: boolean
    mode: 'work' | 'break'
    timeLeft: number
    totalDuration: number
  }

  // 动态粒子/互动特效
  effects: VisualEffect[]

  // 操作方法
  setName: (name: string) => void
  setSkin: (skin: PetSkin) => void
  setEnabled: (enabled: boolean) => void
  setMinimized: (minimized: boolean) => void
  setSoundEnabled: (enabled: boolean) => void
  setBoundRoleId: (roleId: number | null) => void
  setPosition: (pos: { x: number; y: number }) => void
  setState: (state: PetState) => void
  setDirection: (dir: 'left' | 'right') => void

  // 核心互动
  petPet: () => void
  feedPet: (food: FoodType) => void
  say: (text: string, duration?: number) => void
  hideDialogue: () => void

  // 番茄钟
  startPomodoro: (minutes: number, mode?: 'work' | 'break') => void
  stopPomodoro: () => void
  tickPomodoro: () => void

  // 属性自然衰减与自主漫步
  tickNaturalState: () => void

  // 特效管理
  addEffect: (effect: Omit<VisualEffect, 'id'>) => void
  removeEffect: (id: string) => void
}

const STORAGE_KEY = 'roleplex_webpet_config_v1'

const loadPersistedConfig = () => {
  if (typeof window === 'undefined') return null
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    return JSON.parse(raw)
  } catch {
    return null
  }
}

const saved = loadPersistedConfig()

// 默认生成初始位置（网页右下角）
const getInitialPosition = () => {
  if (typeof window === 'undefined') return { x: 800, y: 500 }
  return {
    x: Math.max(20, window.innerWidth - 180),
    y: Math.max(20, window.innerHeight - 190)
  }
}

export const usePetStore = create<PetStoreState>((set, get) => ({
  name: saved?.name ?? '方块仔',
  skin: saved?.skin ?? 'cat',

  enabled: saved?.enabled ?? true,
  isMinimized: saved?.isMinimized ?? false,
  soundEnabled: saved?.soundEnabled ?? true,
  boundRoleId: saved?.boundRoleId ?? null,

  position: saved?.position ?? getInitialPosition(),
  state: 'idle',
  direction: 'left',

  hunger: saved?.hunger ?? 85,
  mood: saved?.mood ?? 90,
  energy: saved?.energy ?? 80,
  affinity: saved?.affinity ?? 15,

  dialogue: {
    text: null,
    visible: false,
    isTyping: false,
    timestamp: 0
  },

  pomodoro: {
    active: false,
    mode: 'work',
    timeLeft: 25 * 60,
    totalDuration: 25 * 60
  },

  effects: [],

  setName: (name) => {
    set({ name })
    persistSettings(get())
  },

  setSkin: (skin) => {
    set({ skin })
    if (get().soundEnabled) petAudio.playPop()
    persistSettings(get())
  },

  setEnabled: (enabled) => {
    set({ enabled })
    persistSettings(get())
  },

  setMinimized: (isMinimized) => {
    set({ isMinimized })
    if (get().soundEnabled) petAudio.playPop()
    persistSettings(get())
  },

  setSoundEnabled: (soundEnabled) => {
    set({ soundEnabled })
    persistSettings(get())
  },

  setBoundRoleId: (boundRoleId) => {
    set({ boundRoleId })
    persistSettings(get())
  },

  setPosition: (position) => {
    set({ position })
    persistSettings(get())
  },

  setState: (state) => set({ state }),
  setDirection: (direction) => set({ direction }),

  // 抚摸互动
  petPet: () => {
    const { mood, affinity, soundEnabled, position } = get()
    const newMood = Math.min(100, mood + 12)
    const newAffinity = affinity + 2

    if (soundEnabled) {
      petAudio.playHappy()
    }

    // 生成爱心粒子
    get().addEffect({
      type: 'heart',
      x: position.x + 35 + (Math.random() * 20 - 10),
      y: position.y - 10,
      text: '+💖'
    })

    set({
      mood: newMood,
      affinity: newAffinity,
      state: 'happy'
    })

    // 随机互动台词
    const lines = [
      '呼噜呼噜…最喜欢被摸摸了！',
      '好舒服喵~ (蹭蹭你的手)',
      '主人今天也辛苦啦！',
      '精神百倍！今天也一起加油！',
      '(*^▽^*) 亲密度提升啦！'
    ]
    const randomLine = lines[Math.floor(Math.random() * lines.length)]
    get().say(randomLine, 3500)

    // 3秒后恢复待机
    setTimeout(() => {
      if (get().state === 'happy') {
        set({ state: 'idle' })
      }
    }, 3000)

    persistSettings(get())
  },

  // 喂食互动
  feedPet: (food) => {
    const { hunger, mood, energy, soundEnabled, position } = get()
    let dHunger = 20
    let dMood = 15
    let dEnergy = 5
    let line = '嚼嚼嚼…真好吃！'

    if (food === 'shrimp') {
      dHunger = 30
      dMood = 30
      line = '咔嚓咔嚓…香脆大虾片，太幸福了！🦐'
    } else if (food === 'riceball') {
      dHunger = 35
      dMood = 20
      line = '大口嚼饭团，能量满满！🍙'
    } else if (food === 'fish') {
      dHunger = 30
      dMood = 20
      line = '鲜美多汁的小鱼干！满足~ 🐟'
    } else if (food === 'pudding') {
      dHunger = 20
      dMood = 30
      line = 'QQ弹弹的布丁！太甜美了 🍮'
    } else if (food === 'coffee') {
      dHunger = 10
      dEnergy = 35
      dMood = 15
      line = '香浓像素咖啡！能量瞬间回满 ☕'
    } else if (food === 'candy') {
      dHunger = 15
      dMood = 25
      line = '彩虹方块糖果！心情棒极了 🍬'
    }


    if (soundEnabled) {
      petAudio.playEat()
    }

    get().addEffect({
      type: 'crumbs',
      x: position.x + 40,
      y: position.y + 20,
      text: '+饱食度'
    })

    set({
      hunger: Math.min(100, hunger + dHunger),
      mood: Math.min(100, mood + dMood),
      energy: Math.min(100, energy + dEnergy),
      state: 'eating'
    })

    get().say(line, 3500)

    setTimeout(() => {
      if (get().state === 'eating') {
        set({ state: 'idle' })
      }
    }, 3500)

    persistSettings(get())
  },

  // 气泡讲话
  say: (text, duration = 4000) => {
    if (get().soundEnabled) {
      petAudio.playPop()
    }
    set({
      dialogue: {
        text,
        visible: true,
        isTyping: true,
        timestamp: Date.now()
      }
    })

    if (duration > 0) {
      setTimeout(() => {
        const current = get().dialogue
        if (current.text === text) {
          set({
            dialogue: {
              ...current,
              visible: false
            }
          })
        }
      }, duration)
    }
  },

  hideDialogue: () => {
    set({
      dialogue: {
        ...get().dialogue,
        visible: false
      }
    })
  },

  // 番茄钟控制
  startPomodoro: (minutes, mode = 'work') => {
    const totalDuration = minutes * 60
    if (get().soundEnabled) petAudio.playPop()
    set({
      pomodoro: {
        active: true,
        mode,
        timeLeft: totalDuration,
        totalDuration
      }
    })
    get().say(
      mode === 'work'
        ? `🍅 开启 ${minutes} 分钟专注陪伴模式！我会认真陪着你的~`
        : `☕ 休息时间到啦！放松一下双眼吧~`,
      4000
    )
  },

  stopPomodoro: () => {
    set({
      pomodoro: {
        ...get().pomodoro,
        active: false
      }
    })
    get().say('番茄钟已暂停。', 2500)
  },

  tickPomodoro: () => {
    const { pomodoro, soundEnabled, position } = get()
    if (!pomodoro.active) return

    if (pomodoro.timeLeft <= 1) {
      if (soundEnabled) {
        petAudio.playChime()
      }
      get().addEffect({
        type: 'levelUp',
        x: position.x + 30,
        y: position.y - 20,
        text: '🎉 专注达成！'
      })

      if (pomodoro.mode === 'work') {
        set({
          pomodoro: {
            active: true,
            mode: 'break',
            timeLeft: 5 * 60,
            totalDuration: 5 * 60
          },
          affinity: get().affinity + 10
        })
        get().say('🎉 太棒啦！完成了 25 分钟深度专注，现在休息 5 分钟吧！', 5000)
      } else {
        set({
          pomodoro: {
            active: false,
            mode: 'work',
            timeLeft: 25 * 60,
            totalDuration: 25 * 60
          }
        })
        get().say('休息结束！准备好开启下一轮专注了吗？', 4000)
      }
    } else {
      set({
        pomodoro: {
          ...pomodoro,
          timeLeft: pomodoro.timeLeft - 1
        }
      })
    }
  },

  // 属性自然衰减与生命周期
  tickNaturalState: () => {
    const { hunger, mood, energy, state } = get()
    // 饱食度微量自然下降
    const newHunger = Math.max(0, hunger - 0.2)
    // 精力根据当前状态恢复或消耗
    let newEnergy = energy
    if (state === 'sleeping') {
      newEnergy = Math.min(100, energy + 1.5)
    } else {
      newEnergy = Math.max(0, energy - 0.1)
    }

    // 饱食度过低时心情降低
    let newMood = mood
    if (newHunger < 20) {
      newMood = Math.max(0, mood - 0.3)
    }

    // 随机打瞌睡检测
    if (state === 'idle' && newEnergy < 25 && Math.random() < 0.2) {
      set({ state: 'sleeping' })
      get().say('呼…有点困了，稍微打个盹… Zzz', 3000)
    } else if (state === 'sleeping' && newEnergy >= 90) {
      set({ state: 'idle' })
      get().say('睡饱啦！伸个懒腰~ ☀️', 3000)
    }

    set({
      hunger: Number(newHunger.toFixed(1)),
      mood: Number(newMood.toFixed(1)),
      energy: Number(newEnergy.toFixed(1))
    })
  },

  addEffect: (effect) => {
    const id = `${Date.now()}_${Math.random()}`
    set({
      effects: [...get().effects, { ...effect, id }]
    })
    setTimeout(() => {
      get().removeEffect(id)
    }, 1800)
  },

  removeEffect: (id) => {
    set({
      effects: get().effects.filter((e) => e.id !== id)
    })
  }
}))

// 持久化存储辅助函数
function persistSettings(state: PetStoreState) {
  if (typeof window === 'undefined') return
  try {
    const data = {
      name: state.name,
      skin: state.skin,
      enabled: state.enabled,
      isMinimized: state.isMinimized,
      soundEnabled: state.soundEnabled,
      boundRoleId: state.boundRoleId,
      position: state.position,
      hunger: state.hunger,
      mood: state.mood,
      energy: state.energy,
      affinity: state.affinity
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
  } catch {
    // ignore
  }
}
