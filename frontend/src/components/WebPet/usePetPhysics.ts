import { useEffect, useRef } from 'react'
import { usePetStore } from '../../store/pet'

export const PET_WIDTH = 110
export const PET_HEIGHT = 140

/** 只处理手动移动和视口边界；世界协调入口不自行游走或用养成数值改变任务状态。 */
export function usePetPhysics() {
  const { position, setPosition, setState, setDirection } = usePetStore()
  const moved = useRef(false), cleanup = useRef<() => void>(() => {})
  const clamp = (x: number, y: number) => ({
    x: Math.min(Math.max(8, Number.isFinite(x) ? x : window.innerWidth - PET_WIDTH - 16), Math.max(8, window.innerWidth - PET_WIDTH - 8)),
    y: Math.min(Math.max(8, Number.isFinite(y) ? y : window.innerHeight - PET_HEIGHT - 24), Math.max(8, window.innerHeight - PET_HEIGHT - 8)),
  })
  useEffect(() => {
    const resize = () => { const p = usePetStore.getState().position; setPosition(clamp(p.x, p.y)) }
    resize(); window.addEventListener('resize', resize)
    return () => { cleanup.current(); window.removeEventListener('resize', resize) }
  }, [setPosition])
  function handlePointerDown(event: React.PointerEvent) {
    if (event.button !== 0) return
    cleanup.current(); moved.current = false
    const start = { x: event.clientX, y: event.clientY }, initial = usePetStore.getState().position
    const move = (e: PointerEvent) => {
      const dx = e.clientX - start.x, dy = e.clientY - start.y
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) { moved.current = true; setState('dragged'); setDirection(dx < 0 ? 'left' : 'right'); setPosition(clamp(initial.x + dx, initial.y + dy)) }
    }
    const end = () => { cleanup.current(); if (moved.current) setState('idle') }
    cleanup.current = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', end); window.removeEventListener('pointercancel', end) }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', end); window.addEventListener('pointercancel', end)
  }
  return { position, handlePointerDown, hasMoved: () => moved.current }
}
