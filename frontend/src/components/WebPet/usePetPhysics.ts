import { useEffect, useRef, useCallback } from 'react'
import { usePetStore } from '../../store/pet'

export const PET_WIDTH = 110
export const PET_HEIGHT = 110

export function usePetPhysics() {
  const {
    position,
    setPosition,
    state,
    setState,
    direction,
    setDirection,
    isMinimized,
    tickNaturalState,
    tickPomodoro
  } = usePetStore()

  const isDraggingRef = useRef(false)
  const dragStartPosRef = useRef({ x: 0, y: 0 })
  const initialPetPosRef = useRef({ x: 0, y: 0 })
  const hasMovedRef = useRef(false)

  // 限制坐标在窗口可视区域内
  const clampPosition = useCallback((x: number, y: number) => {
    if (typeof window === 'undefined') return { x, y }
    const maxX = Math.max(0, window.innerWidth - PET_WIDTH)
    const maxY = Math.max(0, window.innerHeight - PET_HEIGHT)
    return {
      x: Math.min(Math.max(10, x), maxX - 10),
      y: Math.min(Math.max(10, y), maxY - 10)
    }
  }, [])

  // 窗口尺寸变化时自动防出界
  useEffect(() => {
    const handleResize = () => {
      const clamped = clampPosition(position.x, position.y)
      if (clamped.x !== position.x || clamped.y !== position.y) {
        setPosition(clamped)
      }
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [position, clampPosition, setPosition])

  // 自然状态衰减与番茄钟节拍器（每秒执行）
  useEffect(() => {
    const interval = setInterval(() => {
      tickPomodoro()
    }, 1000)
    return () => clearInterval(interval)
  }, [tickPomodoro])

  useEffect(() => {
    const naturalInterval = setInterval(() => {
      tickNaturalState()
    }, 5000)
    return () => clearInterval(naturalInterval)
  }, [tickNaturalState])

  // 待机自主漫步（每隔 15~25 秒随机触发一次可爱散步）
  useEffect(() => {
    if (isMinimized) return

    const walkInterval = setInterval(() => {
      const currentState = usePetStore.getState().state
      if (currentState !== 'idle') return

      // 40% 概率触发散步
      if (Math.random() < 0.4) {
        const currentPos = usePetStore.getState().position
        const moveDist = (Math.random() * 80 + 30) * (Math.random() < 0.5 ? -1 : 1)
        const targetX = currentPos.x + moveDist
        const clamped = clampPosition(targetX, currentPos.y)

        const newDir = moveDist > 0 ? 'right' : 'left'
        setDirection(newDir)
        setState('walking')

        // 步进平滑移动
        const steps = 20
        const stepTime = 120
        const stepX = (clamped.x - currentPos.x) / steps
        let currentStep = 0

        const stepInterval = setInterval(() => {
          currentStep++
          const pos = usePetStore.getState().position
          const nowState = usePetStore.getState().state

          if (nowState !== 'walking') {
            clearInterval(stepInterval)
            return
          }

          setPosition({ x: pos.x + stepX, y: pos.y })

          if (currentStep >= steps) {
            clearInterval(stepInterval)
            if (usePetStore.getState().state === 'walking') {
              setState('idle')
            }
          }
        }, stepTime)
      }
    }, 18000)

    return () => clearInterval(walkInterval)
  }, [clampPosition, isMinimized, setDirection, setPosition, setState])

  // 拖拽事件监听
  const handlePointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (e.button !== 0) return // 仅响应鼠标左键或触控
      e.stopPropagation()

      isDraggingRef.current = true
      hasMovedRef.current = false
      dragStartPosRef.current = { x: e.clientX, y: e.clientY }
      initialPetPosRef.current = { ...position }

      // 捕获指针移动
      const handlePointerMove = (moveEvent: PointerEvent) => {
        if (!isDraggingRef.current) return

        const dx = moveEvent.clientX - dragStartPosRef.current.x
        const dy = moveEvent.clientY - dragStartPosRef.current.y

        if (Math.abs(dx) > 4 || Math.abs(dy) > 4) {
          if (!hasMovedRef.current) {
            hasMovedRef.current = true
            setState('dragged')
          }
          if (dx !== 0) {
            setDirection(dx > 0 ? 'right' : 'left')
          }
          const nextX = initialPetPosRef.current.x + dx
          const nextY = initialPetPosRef.current.y + dy
          const clamped = clampPosition(nextX, nextY)
          setPosition(clamped)
        }
      }

      const handlePointerUp = () => {
        if (!isDraggingRef.current) return
        isDraggingRef.current = false

        window.removeEventListener('pointermove', handlePointerMove)
        window.removeEventListener('pointerup', handlePointerUp)

        if (hasMovedRef.current) {
          setState('idle')
        }
      }

      window.addEventListener('pointermove', handlePointerMove)
      window.addEventListener('pointerup', handlePointerUp)
    },
    [position, clampPosition, setDirection, setPosition, setState]
  )

  return {
    position,
    state,
    direction,
    handlePointerDown,
    isDragging: isDraggingRef.current,
    hasMoved: () => hasMovedRef.current
  }
}
