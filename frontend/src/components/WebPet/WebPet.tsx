import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Bot, Heart, Minimize2, MoreHorizontal, Utensils, Volume2, VolumeX } from 'lucide-react'
import type { Role } from '../../api/client'
import { getAuthEpoch } from '../../api/client'
import { worldOrchestrator } from '../../api/worldOrchestrator'
import { useAppStore } from '../../store/app'
import { usePetStore } from '../../store/pet'
import { usePetPhysics } from './usePetPhysics'
import { PixelPetVisual } from './PixelPetVisual'
import { PetEffects } from './PetEffects'
import { clearCoordinatorSession } from './coordinatorSession'

const WorldCoordinatorPanel = lazy(() => import('./WorldCoordinatorPanel').then(module => ({ default: module.WorldCoordinatorPanel })))

/** 贴边角色入口与本地轻互动；任命、对话及运行状态全部读取真实世界服务。 */
export function WebPet({ onEditRole }: { onEditRole: (role: Role) => void }) {
  const pet = usePetStore(), { user, worldName } = useAppStore()
  const { position, handlePointerDown, hasMoved } = usePetPhysics()
  const [open, setOpen] = useState(false), [menu, setMenu] = useState(false), [roleName, setRoleName] = useState<string | null>(null)
  const [activeCount, setActiveCount] = useState(0)
  const menuRef = useRef<HTMLDivElement>(null)
  const epoch = getAuthEpoch()
  useEffect(() => {
    clearCoordinatorSession()
    setOpen(false); setMenu(false); setRoleName(null); setActiveCount(0)
    if (!user?.is_owner) return
    const controller = new AbortController()
    const load = () => { void worldOrchestrator.read(controller.signal).then(value => { if (!controller.signal.aborted && epoch === getAuthEpoch()) { setRoleName(value.role_name); setActiveCount(value.active_task_count) } }).catch(() => {}) }
    load(); const timer = window.setInterval(load, 5000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [epoch, worldName, user?.id])
  useEffect(() => {
    if (!menu) return
    const outside = (event: PointerEvent) => { if (!menuRef.current?.contains(event.target as Node)) setMenu(false) }
    window.addEventListener('pointerdown', outside)
    return () => window.removeEventListener('pointerdown', outside)
  }, [menu])
  if (!pet.enabled) return null
  const show = () => { if (user?.is_owner) { setOpen(true); setMenu(false) } else { pet.setMinimized(false); pet.petPet() } }
  return <>
    {!open && <PetEffects />}
    {!open && (pet.isMinimized ? <button type="button" aria-label={user?.is_owner ? '打开世界协调面板' : '和桌宠互动'} onClick={show}
      className="fixed bottom-8 right-3 z-40 flex items-center gap-2 rounded-full border border-slate-700 bg-panel px-3 py-2 text-xs shadow-md">
      <Bot size={17} className="text-indigo-500" />{user?.is_owner ? roleName ?? '世界协调' : '桌宠'}{activeCount > 0 && <span>{activeCount} 项待完成</span>}
    </button> : <div className="world-pet fixed left-0 top-0 z-40 select-none touch-none" style={{ transform: `translate3d(${position.x}px,${position.y}px,0)` }}>
      {pet.dialogue.visible && <p className="absolute bottom-full right-0 mb-2 w-48 rounded-xl border border-slate-700 bg-panel p-3 text-xs shadow-sm" role="status">{pet.dialogue.text}</p>}
      <button type="button" aria-label={user?.is_owner ? '打开世界协调面板' : '和桌宠互动'} onPointerDown={handlePointerDown} onClick={() => { if (!hasMoved()) show() }}
        className="block cursor-grab rounded-xl focus-visible:outline focus-visible:outline-indigo-400">
        <PixelPetVisual skin="cat" state={pet.state} direction={pet.direction} isPomodoroActive={false} isHovered={false} onClickHead={() => { if (!hasMoved()) show() }} />
      </button>
      <div ref={menuRef} className="flex items-center justify-center gap-1 text-xs">
        {activeCount > 0 && <span className="absolute bottom-6 right-0 rounded-full bg-indigo-100 px-2 py-1 text-indigo-700">{activeCount} 项待完成</span>}
        <button type="button" className="max-w-24 truncate rounded-full border border-slate-700 bg-panel px-2 py-1" onClick={show}>{user?.is_owner ? roleName ?? '世界协调' : '桌宠'}</button>
        <button type="button" aria-label="桌宠互动与设置" aria-expanded={menu} aria-haspopup="menu" className="rounded-full border border-slate-700 bg-panel p-1" onClick={() => setMenu(value => !value)}><MoreHorizontal size={14} /></button>
        {menu && <div role="menu" aria-label="桌宠轻互动" className="absolute bottom-8 right-0 w-40 rounded-xl border border-slate-700 bg-panel p-1.5 shadow-xl" onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); setMenu(false) } }}>
          <button type="button" role="menuitem" className="flex w-full items-center gap-2 rounded-lg p-2 text-left hover:bg-slate-100" onClick={() => { pet.feedPet('shrimp'); setMenu(false) }}><Utensils size={14} />投喂</button>
          <button type="button" role="menuitem" className="flex w-full items-center gap-2 rounded-lg p-2 text-left hover:bg-slate-100" onClick={() => { pet.petPet(); setMenu(false) }}><Heart size={14} />摸一下</button>
          <button type="button" role="menuitem" className="flex w-full items-center gap-2 rounded-lg p-2 text-left hover:bg-slate-100" onClick={() => pet.setSoundEnabled(!pet.soundEnabled)}>{pet.soundEnabled ? <Volume2 size={14} /> : <VolumeX size={14} />}{pet.soundEnabled ? '关闭音效' : '打开音效'}</button>
          <button type="button" role="menuitem" className="flex w-full items-center gap-2 rounded-lg p-2 text-left hover:bg-slate-100" onClick={() => { pet.setMinimized(true); setMenu(false) }}><Minimize2 size={14} />贴边收起</button>
        </div>}
      </div>
    </div>)}
    {open && <Suspense fallback={<p role="status" className="fixed bottom-8 right-4 z-50 rounded-xl bg-panel p-4 shadow-lg">正在打开世界协调…</p>}><WorldCoordinatorPanel onClose={() => { setOpen(false); void worldOrchestrator.read().then(value => { if (epoch === getAuthEpoch()) setRoleName(value.role_name) }).catch(() => {}) }} onEditRole={onEditRole} /></Suspense>}
  </>
}
