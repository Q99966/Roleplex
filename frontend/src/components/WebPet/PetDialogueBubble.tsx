import React, { useState, useEffect, useRef } from 'react'
import { Send, X, Bot, Sparkles } from 'lucide-react'
import { usePetStore } from '../../store/pet'
import { useAppStore } from '../../store/app'

interface PetDialogueBubbleProps {
  onOpenMenu?: () => void
}

export function PetDialogueBubble({ onOpenMenu: _onOpenMenu }: PetDialogueBubbleProps) {
  const {
    dialogue,
    hideDialogue,
    say,
    boundRoleId,
    name
  } = usePetStore()

  const roles = useAppStore((state) => state.roles)
  const boundRole = roles.find((r) => r.id === boundRoleId)

  const [displayedText, setDisplayedText] = useState('')
  const [showChatInput, setShowChatInput] = useState(false)
  const [inputText, setInputText] = useState('')
  const [isSending, setIsSending] = useState(false)

  const typingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 打字机呈现效果
  useEffect(() => {
    if (!dialogue.visible || !dialogue.text) {
      setDisplayedText('')
      return
    }

    const fullText = dialogue.text
    setDisplayedText('')
    let currentIndex = 0

    if (typingTimerRef.current) clearInterval(typingTimerRef.current)

    typingTimerRef.current = setInterval(() => {
      currentIndex++
      if (currentIndex <= fullText.length) {
        setDisplayedText(fullText.slice(0, currentIndex))
      } else {
        if (typingTimerRef.current) clearInterval(typingTimerRef.current)
      }
    }, 22)

    return () => {
      if (typingTimerRef.current) clearInterval(typingTimerRef.current)
    }
  }, [dialogue.text, dialogue.visible, dialogue.timestamp])

  // 发送对话
  const handleSendMessage = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!inputText.trim() || isSending) return

    const userMsg = inputText.trim()
    setInputText('')
    setIsSending(true)

    setTimeout(() => {
      let reply = ''
      const lower = userMsg.toLowerCase()

      if (boundRole) {
        if (lower.includes('你好') || lower.includes('hi') || lower.includes('hello')) {
          reply = `🔍 委托人，请说来龙去脉。`
        } else if (lower.includes('累') || lower.includes('歇') || lower.includes('困')) {
          reply = `现在睡个午觉好像不错……`
        } else if (lower.includes('饿') || lower.includes('吃')) {
          reply = `有点饿了……来包虾片就好了。`
        } else {
          reply = `${boundRole.name} 收到：“${userMsg}”！`
        }
      } else {
        if (lower.includes('你好') || lower.includes('hi')) {
          reply = `🔍 委托人，请说来龙去脉。`
        } else if (lower.includes('饿') || lower.includes('吃')) {
          reply = `有点饿了……来包虾片就好了。`
        } else if (lower.includes('睡') || lower.includes('困') || lower.includes('累')) {
          reply = `现在睡个午觉好像不错……`
        } else {
          const sampleQuotes = [
            `现在睡个午觉好像不错……`,
            `有点饿了……来包虾片就好了。`,
            `🔍 委托人，请说来龙去脉。`,
            `今天也是没有 bug 的一天（大概）。`,
            `盯————（呆呆凝视）`,
            `咔嚓咔嚓…咀嚼美味虾片中。`
          ]
          reply = sampleQuotes[Math.floor(Math.random() * sampleQuotes.length)]
        }
      }

      say(reply, 5000)
      setIsSending(false)
      setShowChatInput(false)
    }, 300)
  }

  if (!dialogue.visible && !showChatInput) {
    return null
  }

  return (
    <div
      className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2.5 z-30 min-w-[190px] max-w-[260px] select-text pointer-events-auto"
      onClick={(e) => e.stopPropagation()}
    >
      {/* 自适应深色高质感气泡框（与 Roleplex 工作台背景深度融合） */}
      <div className="relative rounded-xl bg-slate-900/95 border border-slate-700/80 p-3 shadow-2xl shadow-black/80 backdrop-blur-xl text-slate-100 animate-in fade-in zoom-in-95 duration-150">
        {/* 极简顶栏 */}
        <div className="flex items-center justify-between pb-1.5 mb-1.5 border-b border-slate-800/80 text-[11px] font-medium text-slate-400">
          <div className="flex items-center gap-1.5">
            {boundRole ? (
              <span className="flex items-center gap-1 text-indigo-400 font-semibold truncate max-w-[150px]">
                <Bot size={12} className="shrink-0" />
                {boundRole.name}
              </span>
            ) : (
              <span className="flex items-center gap-1 text-amber-400 font-semibold">
                <Sparkles size={11} />
                {name}
              </span>
            )}
          </div>
          <button
            type="button"
            onClick={hideDialogue}
            className="rounded p-0.5 hover:bg-slate-800 text-slate-500 hover:text-slate-300 transition"
            title="关闭气泡"
          >
            <X size={12} />
          </button>
        </div>

        {/* 对话文字主体 */}
        {dialogue.visible && (
          <p className="text-[13px] leading-relaxed text-slate-200 font-sans tracking-wide whitespace-pre-wrap py-0.5">
            {displayedText}
            {dialogue.isTyping && displayedText.length < (dialogue.text?.length ?? 0) && (
              <span className="inline-block w-1.5 h-3.5 bg-indigo-400 ml-0.5 animate-pulse align-middle" />
            )}
          </p>
        )}

        {/* 极简聊天互动栏（纯文本输入，无多余按键堆砌） */}
        {showChatInput ? (
          <form onSubmit={handleSendMessage} className="mt-2 flex items-center gap-1.5">
            <input
              type="text"
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder="说点什么…"
              autoFocus
              className="flex-1 bg-slate-950 border border-slate-700 rounded-lg px-2.5 py-1 text-xs text-white placeholder:text-slate-500 outline-none focus:border-indigo-500 transition-colors"
            />
            <button
              type="submit"
              disabled={!inputText.trim() || isSending}
              className="p-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white transition shrink-0"
            >
              <Send size={11} />
            </button>
          </form>
        ) : (
          <div className="mt-1.5 text-right">
            <button
              type="button"
              onClick={() => setShowChatInput(true)}
              className="text-[10px] text-slate-500 hover:text-indigo-400 transition"
            >
              回复一句…
            </button>
          </div>
        )}

        {/* 气泡底部深色三角小尾巴 */}
        <div className="absolute -bottom-[7px] left-1/2 -translate-x-1/2 w-0 h-0 border-l-[6px] border-l-transparent border-r-[6px] border-r-transparent border-t-[7px] border-t-slate-900" />
      </div>
    </div>
  )
}
