import React, { useState, useEffect } from 'react'
import { PetSkin, PetState } from '../../store/pet'

interface PixelPetVisualProps {
  skin: PetSkin
  state: PetState
  direction: 'left' | 'right'
  isPomodoroActive?: boolean
  onClickHead?: () => void
  isHovered?: boolean
  className?: string
}

/**
 * 8-bit 极简像素方块呆萌生物渲染核心
 * 1:1 像素级精准还原《示例3.png》与《样例4.mp4》
 */
export function PixelPetVisual({
  skin,
  state,
  direction,
  isPomodoroActive = false,
  onClickHead,
  isHovered = false,
  className = ''
}: PixelPetVisualProps) {
  const isFlipped = direction === 'left'
  const [isPoppingCorner, setIsPoppingCorner] = useState(false)
  const [showQuestionMark, setShowQuestionMark] = useState(false)
  const [questionColor, setQuestionColor] = useState<'yellow' | 'blue'>('yellow')

  // 皮肤色板 (严格对标示例3.png 原版色值)
  const colorMap: Record<PetSkin, { body: string; side: string; legs: string; shadow: string }> = {
    cat: {
      body: '#d87a5a', // 示例3.png 砖红/肉桂原版
      side: '#c86f52',
      legs: '#d87a5a',
      shadow: '#6b7280'
    },
    shiba: {
      body: '#eab308', // 元气金黄方块
      side: '#ca8a04',
      legs: '#eab308',
      shadow: '#6b7280'
    },
    fairy: {
      body: '#a855f7', // 梦幻粉紫方块
      side: '#9333ea',
      legs: '#a855f7',
      shadow: '#6b7280'
    },
    robot: {
      body: '#0ea5e9', // 赛博天蓝方块
      side: '#0284c7',
      legs: '#0ea5e9',
      shadow: '#475569'
    }
  }

  const colors = colorMap[skin] || colorMap.cat

  // 点击头部时触发角块弹射与小问号 (样例4: 00:03)
  const handleHeadTap = (e: React.MouseEvent) => {
    e.stopPropagation()
    setIsPoppingCorner(true)
    setShowQuestionMark(true)
    setQuestionColor(Math.random() > 0.5 ? 'yellow' : 'blue')

    setTimeout(() => setIsPoppingCorner(false), 550)
    setTimeout(() => setShowQuestionMark(false), 1400)

    if (onClickHead) onClickHead()
  }

  // 待机时左右微转头
  const [eyeOffset, setEyeOffset] = useState(0)
  useEffect(() => {
    if (state !== 'idle') return
    const interval = setInterval(() => {
      if (Math.random() < 0.35) {
        setEyeOffset(Math.random() > 0.5 ? 1 : -1)
        setTimeout(() => setEyeOffset(0), 1200)
      }
    }, 3800)
    return () => clearInterval(interval)
  }, [state])

  return (
    <div
      onClick={handleHeadTap}
      className={`relative w-24 h-24 select-none pointer-events-auto cursor-pointer transition-transform duration-150 ${
        isFlipped ? '-scale-x-100' : 'scale-x-100'
      } ${className}`}
    >
      {/* 像素问号浮动动效 (样例 4: 00:03 ~ 00:07) */}
      {showQuestionMark && (
        <div
          className={`absolute -top-3 left-6 z-20 animate-pixel-question-float pointer-events-none ${
            isFlipped ? '-scale-x-100' : 'scale-x-100'
          }`}
        >
          <PixelQuestionMark color={questionColor} />
        </div>
      )}

      {/* 悬停放大镜探查动效 (样例 4: 00:00) */}
      {isHovered && !showQuestionMark && (
        <div className="absolute -top-3 right-5 z-20 animate-bounce pointer-events-none opacity-90">
          <PixelMagnifier />
        </div>
      )}

      {/* 角色像素主体 SVG (8-bit Pixel Grid) */}
      <svg
        viewBox="0 0 32 32"
        className={`w-full h-full drop-shadow-md overflow-visible ${
          state === 'dragged'
            ? 'scale-105 -translate-y-1'
            : state === 'walking'
            ? 'animate-pixel-step'
            : state === 'happy'
            ? 'animate-pixel-bounce'
            : state === 'sleeping'
            ? 'translate-y-1'
            : 'animate-pixel-idle'
        }`}
        shapeRendering="crispEdges"
      >
        {/* 1. 小短腿 (2 根像素短立柱，拖拽提起时微幅自然轻颤) */}
        {state === 'dragged' ? (
          <g fill={colors.legs}>
            <rect x="11" y="20" width="2" height="3" className="animate-pixel-leg-left" />
            <rect x="19" y="20" width="2" height="3" className="animate-pixel-leg-right" />
          </g>
        ) : (
          <g fill={colors.legs}>
            <rect x="11" y="20" width="2" height="3" />
            <rect x="19" y="20" width="2" height="3" />
          </g>
        )}

        {/* 2. 左手 (被抓起时可爱轻微扑腾) */}
        <rect
          x="5"
          y="14"
          width="3"
          height="3"
          fill={colors.side}
          className={state === 'dragged' ? 'animate-pixel-arm-left' : ''}
        />

        {/* 3. 右手 (被抓起时可爱轻微扑腾) */}
        <rect
          x="24"
          y="14"
          width="3"
          height="3"
          fill={colors.side}
          className={state === 'dragged' ? 'animate-pixel-arm-right' : ''}
        />

        {/* 4. 主身体方块 (干净平整的几何矩形色块 16x9) */}
        <rect x="8" y="11" width="16" height="9" fill={colors.body} />


        {/* 6. 头顶右上角弹跳像素块 (样例 4: 00:03 弹射灵魂动作) */}
        {isPoppingCorner && (
          <rect
            x="21"
            y="9"
            width="3"
            height="3"
            fill={colors.body}
            className="animate-pixel-pop"
          />
        )}

        {/* 7. 眼睛像素渲染 (依据状态机切换) */}
        {state === 'sleeping' ? (
          // 瞌睡横线眼睛: - -
          <g fill="#18181b">
            <rect x="11" y="15" width="3" height="1" />
            <rect x="18" y="15" width="3" height="1" />
          </g>
        ) : state === 'happy' || state === 'eating' ? (
          // 开心/满足眯眯眼: > < (样例 4: 00:13)
          <g fill="#18181b">
            {/* 左眼 > */}
            <rect x="11" y="13" width="1" height="1" />
            <rect x="12" y="14" width="1" height="1" />
            <rect x="13" y="15" width="1" height="1" />
            <rect x="12" y="16" width="1" height="1" />
            <rect x="11" y="17" width="1" height="1" />
            {/* 右眼 < */}
            <rect x="20" y="13" width="1" height="1" />
            <rect x="19" y="14" width="1" height="1" />
            <rect x="18" y="15" width="1" height="1" />
            <rect x="19" y="16" width="1" height="1" />
            <rect x="20" y="17" width="1" height="1" />
          </g>
        ) : state === 'dragged' ? (
          // 抓起惊讶大眼: O O
          <g fill="#18181b">
            <rect x="11" y="13" width="2" height="4" />
            <rect x="19" y="13" width="2" height="4" />
          </g>
        ) : (
          // 经典直立长条黑像素眼睛: | | (精确对标 示例3.png 2x3 垂直立柱)
          <g fill="#18181b">
            <rect x={11 + eyeOffset} y="14" width="2" height="3" />
            <rect x={19 + eyeOffset} y="14" width="2" height="3" />
          </g>
        )}

        {/* 番茄钟专注模式专属：像素头戴式耳机 */}
        {isPomodoroActive && (
          <g fill="#6366f1">
            <rect x="7" y="10" width="18" height="2" />
            <rect x="5" y="12" width="3" height="6" fill="#4f46e5" />
            <rect x="24" y="12" width="3" height="6" fill="#4f46e5" />
            <rect x="3" y="14" width="2" height="2" fill="#22c55e" />
          </g>
        )}
      </svg>
    </div>
  )
}

/* =========================================================================
 * 像素小问号 (Pixel Question Mark - 样例 4)
 * ========================================================================= */
function PixelQuestionMark({ color }: { color: 'yellow' | 'blue' }) {
  const fill = color === 'yellow' ? '#facc15' : '#38bdf8'
  const stroke = color === 'yellow' ? '#ca8a04' : '#0284c7'

  return (
    <svg viewBox="0 0 12 16" className="w-5 h-6 drop-shadow-sm" shapeRendering="crispEdges">
      <rect x="2" y="2" width="6" height="2" fill={fill} stroke={stroke} strokeWidth="0.5" />
      <rect x="6" y="4" width="3" height="3" fill={fill} />
      <rect x="4" y="7" width="3" height="3" fill={fill} />
      <rect x="4" y="12" width="3" height="3" fill={fill} stroke={stroke} strokeWidth="0.5" />
    </svg>
  )
}

/* =========================================================================
 * 像素放大镜 (Pixel Magnifier - 样例 4 悬停探查)
 * ========================================================================= */
function PixelMagnifier() {
  return (
    <svg viewBox="0 0 16 16" className="w-5 h-5 drop-shadow" shapeRendering="crispEdges">
      <rect x="2" y="2" width="8" height="2" fill="#38bdf8" />
      <rect x="2" y="8" width="8" height="2" fill="#38bdf8" />
      <rect x="2" y="2" width="2" height="8" fill="#38bdf8" />
      <rect x="8" y="2" width="2" height="8" fill="#38bdf8" />
      <rect x="4" y="4" width="4" height="4" fill="#e0f2fe" opacity="0.8" />
      <rect x="4" y="4" width="2" height="2" fill="#ffffff" />
      <rect x="9" y="9" width="3" height="3" fill="#64748b" />
      <rect x="11" y="11" width="3" height="3" fill="#475569" />
    </svg>
  )
}
