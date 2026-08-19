import { useState } from 'react'
import {
  X,
  Heart,
  Utensils,
  Palette,
  Bot,
  Volume2,
  VolumeX,
  Minimize2,
  Edit2,
  Check,
  Award,
  Zap,
  Smile,
  Coffee
} from 'lucide-react'
import { usePetStore, PetSkin, FoodType } from '../../store/pet'
import { useAppStore } from '../../store/app'

interface PetMenuProps {
  isOpen: boolean
  onClose: () => void
}

export function PetMenu({ isOpen, onClose }: PetMenuProps) {
  const {
    name,
    setName,
    skin,
    setSkin,
    soundEnabled,
    setSoundEnabled,
    setMinimized,
    boundRoleId,
    setBoundRoleId,
    hunger,
    mood,
    energy,
    affinity,
    petPet,
    feedPet,
    startPomodoro,
    stopPomodoro,
    pomodoro
  } = usePetStore()

  const roles = useAppStore((state) => state.roles)

  const [activeTab, setActiveTab] = useState<'interact' | 'skin' | 'role' | 'settings'>('interact')
  const [isEditingName, setIsEditingName] = useState(false)
  const [nameInput, setNameInput] = useState(name)

  if (!isOpen) return null

  const handleSaveName = () => {
    if (nameInput.trim()) {
      setName(nameInput.trim())
    }
    setIsEditingName(false)
  }

  // 亲密度等级计算
  const affinityLevel = Math.floor(affinity / 10) + 1
  const affinityProgress = (affinity % 10) * 10

  const skins: { id: PetSkin; name: string; icon: string; desc: string }[] = [
    { id: 'cat', name: '经典砖红方块', icon: '🧱', desc: '原版像素造型，呆萌耐看，头角可弹射' },
    { id: 'shiba', name: '元气金黄方块', icon: '🟨', desc: '暖阳金色色块，活力充沛' },
    { id: 'fairy', name: '梦幻粉紫方块', icon: '🟪', desc: '霓虹像素幻彩，神秘灵动' },
    { id: 'robot', name: '赛博天蓝方块', icon: '🟦', desc: '极客智械科技，冷静专注' }
  ]

  const foods: { type: FoodType; name: string; icon: string; desc: string }[] = [
    { type: 'shrimp', name: '香脆大虾片', icon: '🦐', desc: '饱食 +30 / 心情 +30' },
    { type: 'riceball', name: '手作饭团', icon: '🍙', desc: '饱食 +35 / 心情 +20' },
    { type: 'coffee', name: '像素黑咖啡', icon: '☕', desc: '精力 +35 / 心情 +15' },
    { type: 'candy', name: '彩虹方块糖', icon: '🍬', desc: '心情 +25 / 饱食 +15' }
  ]


  return (
    <div
      className="absolute bottom-full right-0 mb-4 z-40 w-80 rounded-2xl bg-slate-900/95 border border-slate-700/80 p-4 shadow-2xl shadow-black/80 backdrop-blur-xl text-slate-100 animate-in fade-in zoom-in-95 duration-200"
      onClick={(e) => e.stopPropagation()}
    >
      {/* 头部：宠物状态与名字 */}
      <div className="flex items-center justify-between pb-3 mb-3 border-b border-slate-800">
        <div className="flex items-center gap-2">
          {isEditingName ? (
            <div className="flex items-center gap-1">
              <input
                type="text"
                value={nameInput}
                onChange={(e) => setNameInput(e.target.value)}
                maxLength={10}
                className="w-24 bg-slate-950 border border-indigo-500 rounded px-1.5 py-0.5 text-xs text-white"
                autoFocus
              />
              <button
                type="button"
                onClick={handleSaveName}
                className="p-1 rounded bg-indigo-600 hover:bg-indigo-500 text-white"
              >
                <Check size={12} />
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-1.5">
              <span className="font-bold text-sm text-white">{name}</span>
              <button
                type="button"
                onClick={() => {
                  setNameInput(name)
                  setIsEditingName(true)
                }}
                className="text-slate-400 hover:text-white p-0.5 transition"
                title="修改昵称"
              >
                <Edit2 size={12} />
              </button>
            </div>
          )}

          <span className="flex items-center gap-0.5 text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/30">
            <Award size={10} /> Lv.{affinityLevel}
          </span>
        </div>

        <button
          type="button"
          onClick={onClose}
          className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-white transition"
          title="关闭面板"
        >
          <X size={16} />
        </button>
      </div>

      {/* 状态数值展示条 */}
      <div className="grid grid-cols-3 gap-2 mb-3 bg-slate-950/60 p-2.5 rounded-xl border border-slate-800/80">
        {/* 饱食度 */}
        <div className="space-y-1">
          <div className="flex justify-between text-[10px] text-slate-400 font-medium">
            <span className="flex items-center gap-1">
              <Utensils size={10} className="text-amber-400" /> 饱食
            </span>
            <span>{Math.round(hunger)}%</span>
          </div>
          <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-amber-500 rounded-full transition-all duration-300"
              style={{ width: `${hunger}%` }}
            />
          </div>
        </div>

        {/* 心情值 */}
        <div className="space-y-1">
          <div className="flex justify-between text-[10px] text-slate-400 font-medium">
            <span className="flex items-center gap-1">
              <Smile size={10} className="text-pink-400" /> 心情
            </span>
            <span>{Math.round(mood)}%</span>
          </div>
          <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-pink-500 rounded-full transition-all duration-300"
              style={{ width: `${mood}%` }}
            />
          </div>
        </div>

        {/* 精力值 */}
        <div className="space-y-1">
          <div className="flex justify-between text-[10px] text-slate-400 font-medium">
            <span className="flex items-center gap-1">
              <Zap size={10} className="text-cyan-400" /> 精力
            </span>
            <span>{Math.round(energy)}%</span>
          </div>
          <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-cyan-500 rounded-full transition-all duration-300"
              style={{ width: `${energy}%` }}
            />
          </div>
        </div>
      </div>

      {/* 亲密度经验条 */}
      <div className="mb-3.5 px-1">
        <div className="flex justify-between text-[10px] text-slate-400 mb-1 font-medium">
          <span className="flex items-center gap-1 text-indigo-300">
            <Heart size={10} className="text-indigo-400" /> 亲密度等级进度 (Lv.{affinityLevel})
          </span>
          <span>{affinityProgress}%</span>
        </div>
        <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-indigo-500 to-pink-500 rounded-full transition-all duration-300"
            style={{ width: `${affinityProgress}%` }}
          />
        </div>
      </div>

      {/* 导航标签切换 */}
      <div className="flex border-b border-slate-800 mb-3 text-xs">
        <button
          type="button"
          onClick={() => setActiveTab('interact')}
          className={`flex-1 pb-2 font-medium transition text-center border-b-2 ${
            activeTab === 'interact'
              ? 'border-indigo-500 text-indigo-400'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
        >
          投喂互动
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('skin')}
          className={`flex-1 pb-2 font-medium transition text-center border-b-2 ${
            activeTab === 'skin'
              ? 'border-indigo-500 text-indigo-400'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
        >
          更换外观
        </button>
        <button
          type="button"
          onClick={() => setActiveTab('role')}
          className={`flex-1 pb-2 font-medium transition text-center border-b-2 ${
            activeTab === 'role'
              ? 'border-indigo-500 text-indigo-400'
              : 'border-transparent text-slate-400 hover:text-slate-200'
          }`}
        >
          绑定角色
        </button>
      </div>

      {/* 标签 1: 投喂与互动 */}
      {activeTab === 'interact' && (
        <div className="space-y-2.5">
          <div className="grid grid-cols-2 gap-2">
            {foods.map((item) => (
              <button
                key={item.type}
                type="button"
                onClick={() => feedPet(item.type)}
                className="flex items-center gap-2 p-2 rounded-xl bg-slate-800/60 hover:bg-slate-800 border border-slate-700/60 hover:border-slate-600 transition text-left group"
              >
                <span className="text-xl shrink-0 group-hover:scale-110 transition-transform">
                  {item.icon}
                </span>
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-slate-200 group-hover:text-white truncate">
                    {item.name}
                  </p>
                  <p className="text-[10px] text-slate-400 truncate">{item.desc}</p>
                </div>
              </button>
            ))}
          </div>

          <div className="flex gap-2 pt-1">
            <button
              type="button"
              onClick={petPet}
              className="flex-1 flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl bg-pink-500/20 hover:bg-pink-500/30 text-pink-300 border border-pink-500/30 text-xs font-semibold transition active:scale-98"
            >
              <Heart size={14} /> 抚摸抱抱
            </button>

            <button
              type="button"
              onClick={() => {
                if (pomodoro.active) stopPomodoro()
                else startPomodoro(25, 'work')
              }}
              className={`flex-1 flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl border text-xs font-semibold transition active:scale-98 ${
                pomodoro.active
                  ? 'bg-red-500/20 text-red-300 border-red-500/40'
                  : 'bg-indigo-500/20 hover:bg-indigo-500/30 text-indigo-300 border-indigo-500/30'
              }`}
            >
              <Coffee size={14} />
              {pomodoro.active ? '结束专注' : '开启番茄钟'}
            </button>
          </div>
        </div>
      )}

      {/* 标签 2: 更换外观 */}
      {activeTab === 'skin' && (
        <div className="grid grid-cols-2 gap-2">
          {skins.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => setSkin(s.id)}
              className={`flex flex-col p-2.5 rounded-xl border transition text-left relative ${
                skin === s.id
                  ? 'bg-indigo-600/20 border-indigo-500 text-white'
                  : 'bg-slate-800/40 hover:bg-slate-800 border-slate-700/60 text-slate-300'
              }`}
            >
              <div className="flex items-center gap-1.5 mb-1">
                <span className="text-xl">{s.icon}</span>
                <span className="text-xs font-bold">{s.name}</span>
              </div>
              <p className="text-[10px] text-slate-400 line-clamp-2">{s.desc}</p>
              {skin === s.id && (
                <span className="absolute top-2 right-2 w-2 h-2 rounded-full bg-indigo-400 shadow-sm shadow-indigo-400" />
              )}
            </button>
          ))}
        </div>
      )}

      {/* 标签 3: 绑定 Roleplex 角色 */}
      {activeTab === 'role' && (
        <div className="space-y-2 max-h-44 overflow-y-auto pr-1">
          <p className="text-[11px] text-slate-400 mb-1">
            将桌宠绑定为你创建的 AI 角色，桌宠将化身为该角色的随身看板娘：
          </p>

          <button
            type="button"
            onClick={() => setBoundRoleId(null)}
            className={`w-full flex items-center justify-between p-2 rounded-lg border text-xs transition ${
              boundRoleId === null
                ? 'bg-indigo-600/20 border-indigo-500 text-white font-medium'
                : 'bg-slate-800/40 border-slate-700/60 text-slate-300 hover:bg-slate-800'
            }`}
          >
            <span className="flex items-center gap-1.5">
              <Palette size={13} className="text-amber-400" /> 默认独立桌宠形态
            </span>
            {boundRoleId === null && <Check size={13} className="text-indigo-400" />}
          </button>

          {roles.map((r) => (
            <button
              key={r.id}
              type="button"
              onClick={() => setBoundRoleId(r.id)}
              className={`w-full flex items-center justify-between p-2 rounded-lg border text-xs transition ${
                boundRoleId === r.id
                  ? 'bg-indigo-600/20 border-indigo-500 text-white font-medium'
                  : 'bg-slate-800/40 border-slate-700/60 text-slate-300 hover:bg-slate-800'
              }`}
            >
              <span className="flex items-center gap-1.5 truncate max-w-[200px]">
                <Bot size={13} className="text-indigo-400 shrink-0" />
                <span className="truncate">{r.name}</span>
              </span>
              {boundRoleId === r.id && <Check size={13} className="text-indigo-400 shrink-0" />}
            </button>
          ))}
        </div>
      )}

      {/* 底部快捷控制栏 (音效 / 最小化收起) */}
      <div className="flex items-center justify-between pt-3 mt-3 border-t border-slate-800 text-xs text-slate-400">
        <button
          type="button"
          onClick={() => setSoundEnabled(!soundEnabled)}
          className="flex items-center gap-1 hover:text-white transition p-1"
          title={soundEnabled ? '音效已开启' : '音效已静音'}
        >
          {soundEnabled ? <Volume2 size={14} className="text-indigo-400" /> : <VolumeX size={14} />}
          <span>{soundEnabled ? '音效已开' : '静音模式'}</span>
        </button>

        <button
          type="button"
          onClick={() => {
            setMinimized(true)
            onClose()
          }}
          className="flex items-center gap-1 hover:text-white transition p-1"
          title="最小化为侧边贴纸"
        >
          <Minimize2 size={14} />
          <span>贴边收起</span>
        </button>
      </div>
    </div>
  )
}
