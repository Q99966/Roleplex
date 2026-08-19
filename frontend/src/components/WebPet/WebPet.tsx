import { useState, useEffect } from 'react'
import { Sparkles, Maximize2 } from 'lucide-react'
import { usePetStore } from '../../store/pet'
import { usePetPhysics } from './usePetPhysics'
import { PixelPetVisual } from './PixelPetVisual'
import { PetDialogueBubble } from './PetDialogueBubble'
import { PetMenu } from './PetMenu'
import { PetEffects } from './PetEffects'

export function WebPet() {
  const {
    enabled,
    isMinimized,
    setMinimized,
    skin,
    name,
    pomodoro,
    state,
    direction,
    say
  } = usePetStore()

  const { position, handlePointerDown, hasMoved } = usePetPhysics()
  const [isMenuOpen, setIsMenuOpen] = useState(false)
  const [isHovered, setIsHovered] = useState(false)

  // 点击空白区域自动关闭菜单
  useEffect(() => {
    if (!isMenuOpen) return
    const handleOutsideClick = () => setIsMenuOpen(false)
    window.addEventListener('click', handleOutsideClick)
    return () => window.removeEventListener('click', handleOutsideClick)
  }, [isMenuOpen])

  if (!enabled) return null

  // 贴边最小化状态（显示为右侧浮动小挂件）
  if (isMinimized) {
    return (
      <div className="fixed right-2 bottom-8 z-50 animate-in fade-in slide-in-from-right-4 duration-300">
        <button
          type="button"
          onClick={() => setMinimized(false)}
          className="group flex items-center gap-2 px-3 py-2 rounded-full bg-slate-900/90 border border-slate-700 text-slate-200 shadow-xl shadow-black/60 hover:bg-slate-800 hover:border-slate-600 transition-all hover:scale-105"
          title={`展开桌宠 ${name}`}
        >
          <span className="text-base">🧱</span>
          <span className="text-xs font-semibold text-slate-300 max-w-[80px] truncate">
            {name}
          </span>
          <Maximize2 size={13} className="text-slate-400 group-hover:text-white" />
        </button>
      </div>
    )
  }

  // 点击宠物时的趣味呆萌台词触发 (参考 bytecatcode 与 样例 3/4)
  const handlePetClick = () => {
    const sampleQuotes = [
      '现在睡个午觉好像不错……',
      '有点饿了……来包虾片就好了。',
      '🔍 委托人，请说来龙去脉。',
      '今天也是没有 bug 的一天（大概）。',
      '盯————（呆呆凝视）',
      '咔嚓咔嚓…咀嚼美味虾片中。',
      '代码无 bug，运行如飞！✨'
    ]
    const randomQuote = sampleQuotes[Math.floor(Math.random() * sampleQuotes.length)]
    say(randomQuote, 4500)
  }

  // 正常悬浮实体状态
  return (
    <>
      <PetEffects />

      <div
        style={{
          transform: `translate3d(${position.x}px, ${position.y}px, 0)`,
          willChange: 'transform'
        }}
        className="fixed top-0 left-0 z-50 touch-none select-none cursor-grab active:cursor-grabbing group"
        onPointerDown={handlePointerDown}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
        onClick={(e) => {
          e.stopPropagation()
          if (!hasMoved()) {
            handlePetClick()
          }
        }}
        onContextMenu={(e) => {
          e.preventDefault()
          setIsMenuOpen(true)
        }}
      >
        {/* 自适应深色对话气泡 */}
        <PetDialogueBubble />

        {/* 8-bit 极简像素方块视觉渲染核心 */}
        <div className="relative">
          <PixelPetVisual
            skin={skin}
            state={state}
            direction={direction}
            isPomodoroActive={pomodoro.active}
            isHovered={isHovered}
            onClickHead={handlePetClick}
          />


          {/* 悬停时的操作提示小图标 */}
          <div className="absolute -top-1 right-2 opacity-0 group-hover:opacity-100 transition-opacity bg-slate-900/90 border border-slate-700 text-white rounded-full p-1 shadow-md">
            <Sparkles size={10} className="text-amber-400" />
          </div>
        </div>

        {/* 右键菜单或长按菜单 (可选高级设置) */}
        {isMenuOpen && <PetMenu isOpen={isMenuOpen} onClose={() => setIsMenuOpen(false)} />}
      </div>
    </>
  )
}
