import { create } from 'zustand'
import { petAudio } from '../components/WebPet/petAudio'

export type PetSkin = 'cat' | 'shiba' | 'fairy' | 'robot'
export type PetState = 'idle' | 'walking' | 'dragged' | 'happy' | 'sleeping' | 'eating' | 'talking'
export type VisualEffect = { id: string; type: 'heart' | 'crumbs'; x: number; y: number; text?: string }
type Position = { x: number; y: number }
interface PetStore {
  enabled: boolean; isMinimized: boolean; soundEnabled: boolean; position: Position
  state: PetState; direction: 'left' | 'right'; effects: VisualEffect[]
  dialogue: { text: string | null; visible: boolean }
  setEnabled: (value: boolean) => void; setMinimized: (value: boolean) => void
  setSoundEnabled: (value: boolean) => void; setPosition: (value: Position) => void
  setState: (value: PetState) => void; setDirection: (value: 'left' | 'right') => void
  petPet: () => void; feedPet: (food?: 'shrimp') => void; say: (text: string, duration?: number) => void
}
const storageKey = 'roleplex_webpet_config_v1'
const read = (): Partial<PetStore> => {
  try { return JSON.parse(localStorage.getItem(storageKey) ?? '{}') } catch { return {} }
}
const saved = read()
let dialogueTimer: ReturnType<typeof setTimeout> | undefined, interactionTimer: ReturnType<typeof setTimeout> | undefined
/** 仅保存入口位置、显示和音效偏好。旧本地角色绑定、外观与养成数据不再具有产品含义。 */
function persist(state: PetStore) {
  try { localStorage.setItem(storageKey, JSON.stringify({ enabled: state.enabled, isMinimized: state.isMinimized,
    soundEnabled: state.soundEnabled, position: state.position })) } catch { /* 本地偏好存储不可用不影响真实任务。 */ }
}
export const usePetStore = create<PetStore>((set, get) => {
  function interact(state: 'happy' | 'eating', text: string) {
    clearTimeout(interactionTimer)
    if (get().soundEnabled) { if (state === 'happy') petAudio.playHappy(); else petAudio.playEat() }
    const p = get().position
    set({ state, effects: [{ id: crypto.randomUUID(), type: state === 'happy' ? 'heart' : 'crumbs', x: p.x + 35, y: p.y, text: state === 'happy' ? '谢谢陪伴' : '投喂成功' }] })
    get().say(text, 3000)
    interactionTimer = setTimeout(() => set({ state: 'idle', effects: [] }), 1800)
  }
  return {
    enabled: saved.enabled !== false, isMinimized: saved.isMinimized === true, soundEnabled: saved.soundEnabled !== false,
    position: saved.position ?? { x: Math.max(8, window.innerWidth - 140), y: Math.max(8, window.innerHeight - 180) },
    state: 'idle', direction: 'left', effects: [], dialogue: { text: null, visible: false },
    setEnabled: enabled => { set({ enabled }); persist(get()) },
    setMinimized: isMinimized => { set({ isMinimized }); persist(get()) },
    setSoundEnabled: soundEnabled => { set({ soundEnabled }); persist(get()) },
    setPosition: position => { set({ position }); persist(get()) },
    setState: state => set({ state }), setDirection: direction => set({ direction }),
    petPet: () => interact('happy', '陪你待一会儿。'),
    feedPet: () => interact('eating', '收到小零食，谢谢！'),
    say: (text, duration = 3000) => {
      clearTimeout(dialogueTimer); set({ dialogue: { text, visible: true } })
      dialogueTimer = setTimeout(() => set({ dialogue: { text: null, visible: false } }), duration)
    },
  }
})
