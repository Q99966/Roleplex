import React from 'react'
import { PetSkin, PetState } from '../../store/pet'

interface PetVisualProps {
  skin: PetSkin
  state: PetState
  direction: 'left' | 'right'
  isPomodoroActive?: boolean
  className?: string
}

/**
 * 现代矢量 SVG 萌宠视觉渲染核心
 * 包含纯代码 SVG、关键部位关节变形与 CSS 微动作
 */
export function PetVisual({
  skin,
  state,
  direction,
  isPomodoroActive = false,
  className = ''
}: PetVisualProps) {
  const isFlipped = direction === 'left'

  return (
    <div
      className={`relative w-28 h-28 select-none pointer-events-none transition-transform duration-200 ${
        isFlipped ? '-scale-x-100' : 'scale-x-100'
      } ${className}`}
    >
      {/* 底部柔和接触阴影 */}
      <div
        className={`absolute bottom-1 left-1/2 -translate-x-1/2 h-3.5 bg-black/25 rounded-full blur-[3px] transition-all duration-300 ${
          state === 'dragged'
            ? 'w-10 opacity-30 scale-75'
            : state === 'sleeping'
            ? 'w-20 opacity-40'
            : 'w-16 opacity-50'
        }`}
      />

      {/* 角色 SVG 渲染 */}
      <div
        className={`w-full h-full relative transition-all duration-250 ${
          state === 'dragged'
            ? 'animate-wiggle scale-105 -translate-y-2'
            : state === 'walking'
            ? 'animate-pet-bounce'
            : state === 'happy'
            ? 'animate-pet-jump'
            : state === 'sleeping'
            ? 'translate-y-2.5 scale-y-90'
            : 'animate-pet-breathe'
        }`}
      >
        {skin === 'cat' && <CatVisual state={state} isPomodoro={isPomodoroActive} />}
        {skin === 'shiba' && <ShibaVisual state={state} isPomodoro={isPomodoroActive} />}
        {skin === 'fairy' && <FairyVisual state={state} isPomodoro={isPomodoroActive} />}
        {skin === 'robot' && <RobotVisual state={state} isPomodoro={isPomodoroActive} />}

        {/* 专注番茄钟专属配饰：耳机 */}
        {isPomodoroActive && skin !== 'robot' && <HeadphonesOverlay />}
      </div>
    </div>
  )
}

/* =========================================================================
 * 1. 软萌暖阳橘猫 (Cat Visual)
 * ========================================================================= */
function CatVisual({ state, isPomodoro: _isPomodoro }: { state: PetState; isPomodoro: boolean }) {
  return (
    <svg viewBox="0 0 100 100" className="w-full h-full drop-shadow-md overflow-visible">
      <defs>
        {/* 身体主渐变 */}
        <linearGradient id="catBodyGrad" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#fb923c" />
          <stop offset="100%" stopColor="#ea580c" />
        </linearGradient>
        {/* 肚皮奶白渐变 */}
        <linearGradient id="catBellyGrad" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#fffbeb" />
          <stop offset="100%" stopColor="#fef3c7" />
        </linearGradient>
        {/* 耳朵粉嫩内耳 */}
        <linearGradient id="pinkEarGrad" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#fda4af" />
          <stop offset="100%" stopColor="#f43f5e" />
        </linearGradient>
      </defs>

      {/* 摆动的尾巴 */}
      <path
        d="M 28 65 C 10 60, 5 40, 18 35 C 24 33, 20 48, 30 60"
        fill="none"
        stroke="#ea580c"
        strokeWidth="6.5"
        strokeLinecap="round"
        className={`origin-[28px_65px] ${
          state === 'happy'
            ? 'animate-tail-fast'
            : state === 'sleeping'
            ? 'animate-none'
            : 'animate-tail-wag'
        }`}
      />

      {/* 猫咪耳朵 */}
      {/* 左耳 */}
      <g className={state === 'dragged' ? 'origin-[35px_30px] rotate-[-15deg]' : ''}>
        <polygon points="26,35 34,12 48,28" fill="url(#catBodyGrad)" stroke="#c2410c" strokeWidth="1" />
        <polygon points="29,32 35,16 44,28" fill="url(#pinkEarGrad)" opacity="0.85" />
      </g>
      {/* 右耳 */}
      <g className={state === 'dragged' ? 'origin-[65px_30px] rotate-[15deg]' : ''}>
        <polygon points="74,35 66,12 52,28" fill="url(#catBodyGrad)" stroke="#c2410c" strokeWidth="1" />
        <polygon points="71,32 65,16 56,28" fill="url(#pinkEarGrad)" opacity="0.85" />
      </g>

      {/* 身体 */}
      <ellipse
        cx="50"
        cy="62"
        rx="26"
        ry="22"
        fill="url(#catBodyGrad)"
        stroke="#c2410c"
        strokeWidth="1.2"
      />
      {/* 白肚皮 */}
      <ellipse cx="50" cy="65" rx="16" ry="14" fill="url(#catBellyGrad)" />

      {/* 头部 */}
      <circle
        cx="50"
        cy="40"
        r="24"
        fill="url(#catBodyGrad)"
        stroke="#c2410c"
        strokeWidth="1.2"
      />

      {/* 额头花纹 */}
      <path d="M 46 22 L 50 28 L 54 22" stroke="#c2410c" strokeWidth="2" strokeLinecap="round" fill="none" />
      <path d="M 50 20 L 50 26" stroke="#c2410c" strokeWidth="2" strokeLinecap="round" />

      {/* 脸颊粉嫩腮红 */}
      <ellipse cx="33" cy="46" rx="4.5" ry="2.5" fill="#f43f5e" opacity="0.5" />
      <ellipse cx="67" cy="46" rx="4.5" ry="2.5" fill="#f43f5e" opacity="0.5" />

      {/* 胡须 */}
      <line x1="22" y1="42" x2="34" y2="44" stroke="#7c2d12" strokeWidth="1.2" strokeLinecap="round" />
      <line x1="20" y1="47" x2="33" y2="47" stroke="#7c2d12" strokeWidth="1.2" strokeLinecap="round" />
      <line x1="78" y1="42" x2="66" y2="44" stroke="#7c2d12" strokeWidth="1.2" strokeLinecap="round" />
      <line x1="80" y1="47" x2="67" y2="47" stroke="#7c2d12" strokeWidth="1.2" strokeLinecap="round" />

      {/* 眼睛状态机渲染 */}
      {state === 'sleeping' ? (
        // 睡觉闭眼弧线
        <g stroke="#431407" strokeWidth="2" strokeLinecap="round" fill="none">
          <path d="M 36 40 Q 40 44 44 40" />
          <path d="M 56 40 Q 60 44 64 40" />
        </g>
      ) : state === 'happy' || state === 'eating' ? (
        // 开心眯眯眼
        <g stroke="#431407" strokeWidth="2.5" strokeLinecap="round" fill="none">
          <path d="M 35 42 Q 40 36 45 42" />
          <path d="M 55 42 Q 60 36 65 42" />
        </g>
      ) : state === 'dragged' ? (
        // 抓起惊讶大圆眼
        <g fill="#1e1b4b">
          <circle cx="40" cy="39" r="5" />
          <circle cx="60" cy="39" r="5" />
          <circle cx="41.5" cy="37.5" r="2" fill="#ffffff" />
          <circle cx="61.5" cy="37.5" r="2" fill="#ffffff" />
        </g>
      ) : (
        // 正常大眼睛（带高光）
        <g fill="#1e1b4b">
          <ellipse cx="40" cy="39" rx="4.2" ry="5.2" />
          <ellipse cx="60" cy="39" rx="4.2" ry="5.2" />
          {/* 高光 */}
          <circle cx="41.5" cy="37" r="1.8" fill="#ffffff" />
          <circle cx="61.5" cy="37" r="1.8" fill="#ffffff" />
          <circle cx="39" cy="41.5" r="0.8" fill="#ffffff" />
          <circle cx="59" cy="41.5" r="0.8" fill="#ffffff" />
        </g>
      )}

      {/* 鼻子与猫咪嘴巴 */}
      <polygon points="48.5,44 51.5,44 50,46" fill="#f43f5e" />
      {state === 'eating' || state === 'talking' ? (
        <path d="M 46 47 Q 50 53 54 47 Z" fill="#f43f5e" stroke="#431407" strokeWidth="1" />
      ) : (
        <path
          d="M 46 46.5 Q 50 49.5 50 46.5 Q 50 49.5 54 46.5"
          fill="none"
          stroke="#431407"
          strokeWidth="1.4"
          strokeLinecap="round"
        />
      )}

      {/* 小爪子 */}
      {state === 'dragged' ? (
        // 悬空扑腾小爪
        <g fill="#fef3c7" stroke="#ea580c" strokeWidth="1.2">
          <circle cx="36" cy="74" r="5" className="animate-paw-flail-left" />
          <circle cx="64" cy="74" r="5" className="animate-paw-flail-right" />
        </g>
      ) : (
        // 正常乖巧坐姿爪爪
        <g fill="#fef3c7" stroke="#ea580c" strokeWidth="1.2">
          <ellipse cx="40" cy="79" rx="5.5" ry="4" />
          <ellipse cx="60" cy="79" rx="5.5" ry="4" />
        </g>
      )}
    </svg>
  )
}

/* =========================================================================
 * 2. 元气柴犬 (Shiba Inu Visual)
 * ========================================================================= */
function ShibaVisual({ state }: { state: PetState; isPomodoro: boolean }) {
  return (
    <svg viewBox="0 0 100 100" className="w-full h-full drop-shadow-md overflow-visible">
      <defs>
        <linearGradient id="shibaGoldGrad" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#f59e0b" />
          <stop offset="100%" stopColor="#d97706" />
        </linearGradient>
        <linearGradient id="shibaWhiteGrad" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="100%" stopColor="#fef3c7" />
        </linearGradient>
      </defs>

      {/* 卷尾巴 */}
      <path
        d="M 26 62 C 12 56, 8 36, 22 36 C 30 36, 28 50, 18 48"
        fill="none"
        stroke="#d97706"
        strokeWidth="7"
        strokeLinecap="round"
        className={state === 'happy' ? 'animate-tail-fast' : 'animate-tail-wag'}
      />

      {/* 尖耳朵 */}
      <polygon points="26,38 34,14 48,30" fill="url(#shibaGoldGrad)" stroke="#b45309" strokeWidth="1" />
      <polygon points="30,34 35,20 44,30" fill="#fef3c7" />
      <polygon points="74,38 66,14 52,30" fill="url(#shibaGoldGrad)" stroke="#b45309" strokeWidth="1" />
      <polygon points="70,34 65,20 56,30" fill="#fef3c7" />

      {/* 身体 */}
      <ellipse cx="50" cy="62" rx="26" ry="22" fill="url(#shibaGoldGrad)" stroke="#b45309" strokeWidth="1.2" />
      <ellipse cx="50" cy="65" rx="16" ry="14" fill="url(#shibaWhiteGrad)" />

      {/* 红色小领巾 */}
      <path d="M 34 54 Q 50 64 66 54 L 50 68 Z" fill="#ef4444" stroke="#b91c1c" strokeWidth="1" />
      <circle cx="50" cy="60" r="2.5" fill="#fef08a" />

      {/* 头部 */}
      <circle cx="50" cy="40" r="24" fill="url(#shibaGoldGrad)" stroke="#b45309" strokeWidth="1.2" />

      {/* 柴犬白色脸颊斑块 */}
      <ellipse cx="38" cy="46" rx="11" ry="9" fill="url(#shibaWhiteGrad)" />
      <ellipse cx="62" cy="46" rx="11" ry="9" fill="url(#shibaWhiteGrad)" />
      {/* 柴犬白眉毛小圆点 */}
      <circle cx="39" cy="30" r="2.5" fill="#ffffff" />
      <circle cx="61" cy="30" r="2.5" fill="#ffffff" />

      {/* 脸颊腮红 */}
      <ellipse cx="30" cy="47" rx="4" ry="2" fill="#f43f5e" opacity="0.4" />
      <ellipse cx="70" cy="47" rx="4" ry="2" fill="#f43f5e" opacity="0.4" />

      {/* 眼睛状态 */}
      {state === 'sleeping' ? (
        <g stroke="#451a03" strokeWidth="2" strokeLinecap="round" fill="none">
          <path d="M 36 39 Q 40 43 44 39" />
          <path d="M 56 39 Q 60 43 64 39" />
        </g>
      ) : state === 'happy' ? (
        <g stroke="#451a03" strokeWidth="2.5" strokeLinecap="round" fill="none">
          <path d="M 35 41 Q 40 35 45 41" />
          <path d="M 55 41 Q 60 35 65 41" />
        </g>
      ) : (
        <g fill="#451a03">
          <circle cx="40" cy="38" r="4.5" />
          <circle cx="60" cy="38" r="4.5" />
          <circle cx="41.5" cy="36.5" r="1.6" fill="#ffffff" />
          <circle cx="61.5" cy="36.5" r="1.6" fill="#ffffff" />
        </g>
      )}

      {/* 黑色鼻子 */}
      <ellipse cx="50" cy="43" rx="4" ry="3" fill="#1c1917" />

      {/* 嘴巴与吐舌头 */}
      {state === 'happy' ? (
        <g>
          <path d="M 44 46 Q 50 54 56 46 Z" fill="#451a03" />
          <ellipse cx="50" cy="50" rx="3.5" ry="4" fill="#f43f5e" />
        </g>
      ) : (
        <path d="M 45 46 Q 50 49 50 46 Q 50 49 55 46" fill="none" stroke="#451a03" strokeWidth="1.5" strokeLinecap="round" />
      )}

      {/* 爪爪 */}
      <g fill="#ffffff" stroke="#d97706" strokeWidth="1.2">
        <ellipse cx="40" cy="79" rx="5.5" ry="4" />
        <ellipse cx="60" cy="79" rx="5.5" ry="4" />
      </g>
    </svg>
  )
}

/* =========================================================================
 * 3. 星愿精灵 (Star Fairy Visual)
 * ========================================================================= */
function FairyVisual({ state }: { state: PetState; isPomodoro: boolean }) {
  return (
    <svg viewBox="0 0 100 100" className="w-full h-full drop-shadow-lg overflow-visible">
      <defs>
        <linearGradient id="fairyBodyGrad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#c084fc" />
          <stop offset="50%" stopColor="#818cf8" />
          <stop offset="100%" stopColor="#38bdf8" />
        </linearGradient>
        <radialGradient id="fairyGlow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#a855f7" stopOpacity="0.6" />
          <stop offset="100%" stopColor="#38bdf8" stopOpacity="0" />
        </radialGradient>
      </defs>

      {/* 魔法光晕背景 */}
      <circle cx="50" cy="50" r="38" fill="url(#fairyGlow)" className="animate-pulse" />

      {/* 闪烁的小翅膀 (左) */}
      <path
        d="M 32 45 C 10 30, 8 10, 26 22 C 34 27, 34 38, 32 45 Z"
        fill="#e0e7ff"
        fillOpacity="0.75"
        stroke="#818cf8"
        strokeWidth="1"
        className="animate-wing-left origin-[32px_45px]"
      />
      {/* 翅膀 (右) */}
      <path
        d="M 68 45 C 90 30, 92 10, 74 22 C 66 27, 66 38, 68 45 Z"
        fill="#e0e7ff"
        fillOpacity="0.75"
        stroke="#818cf8"
        strokeWidth="1"
        className="animate-wing-right origin-[68px_45px]"
      />

      {/* 精灵发光主体 */}
      <ellipse cx="50" cy="52" rx="24" ry="26" fill="url(#fairyBodyGrad)" />

      {/* 头顶星星触角 */}
      <path d="M 50 26 Q 50 14 50 12" stroke="#fbbf24" strokeWidth="2.5" strokeLinecap="round" />
      <polygon
        points="50,4 52.5,9.5 58,10 54,14 55,19 50,16 45,19 46,14 42,10 47.5,9.5"
        fill="#fde047"
        stroke="#f59e0b"
        strokeWidth="0.8"
        className="animate-spin-slow origin-[50px_12px]"
      />

      {/* 梦幻眼睛 */}
      {state === 'sleeping' ? (
        <g stroke="#ffffff" strokeWidth="2" strokeLinecap="round" fill="none">
          <path d="M 37 50 Q 42 54 47 50" />
          <path d="M 53 50 Q 58 54 63 50" />
        </g>
      ) : (
        <g fill="#ffffff">
          <circle cx="42" cy="48" r="4.5" />
          <circle cx="58" cy="48" r="4.5" />
          <circle cx="43.5" cy="46.5" r="1.8" fill="#a855f7" />
          <circle cx="59.5" cy="46.5" r="1.8" fill="#a855f7" />
          <circle cx="41" cy="49.5" r="1" fill="#ffffff" />
          <circle cx="57" cy="49.5" r="1" fill="#ffffff" />
        </g>
      )}

      {/* 微笑 */}
      <path d="M 47 56 Q 50 59 53 56" fill="none" stroke="#ffffff" strokeWidth="1.5" strokeLinecap="round" />

      {/* 腮红 */}
      <circle cx="34" cy="53" r="3" fill="#f43f5e" opacity="0.6" />
      <circle cx="66" cy="53" r="3" fill="#f43f5e" opacity="0.6" />
    </svg>
  )
}

/* =========================================================================
 * 4. 赛博智械小机人 (Cyber Bot Visual)
 * ========================================================================= */
function RobotVisual({ state }: { state: PetState; isPomodoro: boolean }) {
  return (
    <svg viewBox="0 0 100 100" className="w-full h-full drop-shadow-md overflow-visible">
      <defs>
        <linearGradient id="metalGrad" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#334155" />
          <stop offset="100%" stopColor="#0f172a" />
        </linearGradient>
        <linearGradient id="neonScreen" x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#0284c7" />
          <stop offset="100%" stopColor="#0369a1" />
        </linearGradient>
      </defs>

      {/* 喷气悬浮火焰 */}
      <polygon
        points="44,78 56,78 50,92"
        fill="#38bdf8"
        className="animate-pulse origin-[50px_78px]"
      />
      <polygon
        points="47,78 53,78 50,86"
        fill="#ffffff"
      />

      {/* 天线 */}
      <line x1="50" y1="20" x2="50" y2="10" stroke="#64748b" strokeWidth="3" strokeLinecap="round" />
      <circle cx="50" cy="8" r="4" fill="#06b6d4" className="animate-ping" opacity="0.75" />
      <circle cx="50" cy="8" r="4" fill="#22d3ee" />

      {/* 机器人圆角机体 */}
      <rect
        x="24"
        y="20"
        width="52"
        height="56"
        rx="18"
        fill="url(#metalGrad)"
        stroke="#475569"
        strokeWidth="2"
      />

      {/* 侧面小螺栓/小耳朵 */}
      <rect x="18" y="40" width="6" height="14" rx="3" fill="#0284c7" />
      <rect x="76" y="40" width="6" height="14" rx="3" fill="#0284c7" />

      {/* 荧光面罩屏幕 */}
      <rect
        x="30"
        y="28"
        width="40"
        height="30"
        rx="10"
        fill="#082f49"
        stroke="#0284c7"
        strokeWidth="1.5"
      />

      {/* 数字表情状态 */}
      {state === 'sleeping' ? (
        <text x="50" y="48" fill="#38bdf8" fontSize="13" fontWeight="bold" textAnchor="middle" fontFamily="monospace">
          - 💤 -
        </text>
      ) : state === 'happy' ? (
        <text x="50" y="48" fill="#4ade80" fontSize="14" fontWeight="bold" textAnchor="middle" fontFamily="monospace">
          ^ ‿ ^
        </text>
      ) : state === 'dragged' ? (
        <text x="50" y="48" fill="#f87171" fontSize="13" fontWeight="bold" textAnchor="middle" fontFamily="monospace">
          ! ▵ !
        </text>
      ) : (
        <g fill="#38bdf8">
          <circle cx="42" cy="42" r="3.5" />
          <circle cx="58" cy="42" r="3.5" />
          <rect x="46" y="48" width="8" height="2" rx="1" />
        </g>
      )}

      {/* 胸前电量核心 */}
      <circle cx="50" cy="65" r="3" fill="#38bdf8" className="animate-pulse" />
    </svg>
  )
}

/* =========================================================================
 * 番茄钟专属头戴式耳机
 * ========================================================================= */
function HeadphonesOverlay() {
  return (
    <svg viewBox="0 0 100 100" className="w-full h-full absolute inset-0 pointer-events-none z-10 overflow-visible">
      {/* 弧形头梁 */}
      <path
        d="M 23 44 C 23 15, 77 15, 77 44"
        fill="none"
        stroke="#6366f1"
        strokeWidth="4"
        strokeLinecap="round"
      />
      {/* 左耳罩 */}
      <rect x="18" y="36" width="8" height="18" rx="4" fill="#4f46e5" stroke="#818cf8" strokeWidth="1" />
      <circle cx="22" cy="45" r="2" fill="#22c55e" className="animate-ping" />
      {/* 右耳罩 */}
      <rect x="74" y="36" width="8" height="18" rx="4" fill="#4f46e5" stroke="#818cf8" strokeWidth="1" />
      <circle cx="78" cy="45" r="2" fill="#22c55e" className="animate-ping" />
    </svg>
  )
}
