import { RoleUsagePanel } from './RoleUsagePanel'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Bot, Info, UsersRound, LockKeyhole } from 'lucide-react'
import type { Conversation, Role } from '../api/client'
import { useAppStore } from '../store/app'

type Module = 'overview' | 'members'
const MODULES = [{ id: 'overview', name: '会话概览', Icon: Info }, { id: 'members', name: '会话成员', Icon: UsersRound }] as const

type WheelId = Module | 'reserved-1' | 'reserved-2' | 'reserved-3'
const WHEEL_ITEMS = [...MODULES, ...(['reserved-1', 'reserved-2', 'reserved-3'] as const).map(id => ({ id, name: '未开放', Icon: LockKeyhole }))]
const SLOT_COUNT = 5
const STEP_ANGLE = 90 / SLOT_COUNT
const FAN_RADIUS = 320

/** 计算可见扇区和进入侧的循环副本；统一围绕右上圆心旋转。
 * @param index 模块在可见队列及循环副本中的顺序。
 * @param count 可见窗口的格数，包含不可选的未开放补位。
 * @param radius 当前右栏的可用宽度，使圆弧贴近左边缘。
 */
function fanSector(index: number, count: number, radius: number) {
  const FAN_RADIUS = radius
  const start = Math.PI - index * Math.PI / (2 * count)
  const end = Math.PI - (index + 1) * Math.PI / (2 * count)
  const point = (radius: number, angle: number) => [FAN_RADIUS + radius * Math.cos(angle), FAN_RADIUS + radius * Math.sin(angle)]
  const outerStart = point(FAN_RADIUS, start), outerEnd = point(FAN_RADIUS, end)
  const label = point(FAN_RADIUS * .9, start - Math.PI / 36)
  return {
    path: `M ${outerStart.join(' ')} A ${FAN_RADIUS} ${FAN_RADIUS} 0 0 0 ${outerEnd.join(' ')} L ${FAN_RADIUS} ${FAN_RADIUS} Z`,
    label,
  }
}

/** 当前会话的按需详情；模块切换只改变本面板，不修改消息或导航。
 * @param conversation 当前已授权会话。
 * @param open 面板是否展开。
 * @param drawer 窄屏时使用带焦点约束的抽屉。
 * @param onClose 关闭详情面板。
 * @param onEditRole 复用原角色编辑入口，仅 Owner 可用。
 * @param onManageMembers 复用现有群聊成员管理。
 */
export function ConversationDetails({ conversation, open, drawer, onClose, onEditRole, onManageMembers }: {
  conversation: Conversation; open: boolean; drawer: boolean; onClose: () => void;
  onEditRole: (role: Role) => void; onManageMembers: (conversation: Conversation) => void
}) {
  const { roles, roleDirectory, workspaceBindings, user } = useAppStore()
  const [module, setModule] = useState<Module>('overview')
  const [menuOpen, setMenuOpen] = useState(false)
  const [order, setOrder] = useState<WheelId[]>(['overview', 'members', 'reserved-1', 'reserved-2', 'reserved-3'])
  const [radius, setRadius] = useState(FAN_RADIUS)
  const [paint, setPaint] = useState({ background: '#f4fafc', text: '#435e6d', line: '#dce9ef' })
  const [rotation, setRotation] = useState(0)
  const pendingModule = useRef<Module | null>(null)
  const restoreTrigger = useRef(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const positions = useRef({ overview: 0, members: 0 })
  const current = MODULES.find(value => value.id === module)!
  const binding = workspaceBindings.find(value => value.id === conversation.workspace_binding_id)

  useLayoutEffect(() => {
    if (open && !menuOpen && restoreTrigger.current) {
      restoreTrigger.current = false
      triggerRef.current?.focus()
    }
  }, [menuOpen, open])

  /** 菜单收起后再返回焦点，避免聚焦仍不可见的入口。 */
  function dismissMenu() {
    restoreTrigger.current = true
    setMenuOpen(false)
  }
  useLayoutEffect(() => {
    const panel = panelRef.current
    if (!open || !panel) return
    const update = () => {
      setRadius(panel.clientWidth)
      const style = getComputedStyle(panel)
      setPaint({ background: style.backgroundColor, text: style.color, line: style.borderLeftColor })
    }
    update()
    const observer = new ResizeObserver(update)
    observer.observe(panel)
    return () => observer.disconnect()
  }, [open, drawer, menuOpen])
  useLayoutEffect(() => {
    if (open && scrollRef.current) scrollRef.current.scrollTop = positions.current[module]
  }, [module, open])
  useEffect(() => { if (!open) setMenuOpen(false) }, [open])
  useEffect(() => {
    if (!menuOpen) { setRotation(0); pendingModule.current = null; return }
    if (!rotation) return
    const timer = window.setTimeout(() => {
      const next = pendingModule.current
      if (!next) return
      const steps = rotation / STEP_ANGLE
      setOrder(value => [...value.slice(steps), ...value.slice(0, steps)])
      setModule(next)
      setRotation(0)
      pendingModule.current = null
      dismissMenu()
    }, window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 300)
    return () => window.clearTimeout(timer)
  }, [rotation, menuOpen])
  useEffect(() => {
    if (!menuOpen) return
    menuRef.current?.querySelector<HTMLButtonElement>('[role="menuitemradio"][aria-checked="true"]')?.focus()
    const outside = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [menuOpen])
  useEffect(() => {
    if (!open || !drawer) return
    const previous = document.activeElement as HTMLElement | null
    panelRef.current?.focus()
    return () => { if (previous?.isConnected) previous.focus() }
  }, [open, drawer])

  /** 切换前记录位置，选择后将焦点交回模块入口。
   * @param next 已实现的目标模块。
   */
  function select(next: Module) {
    if (scrollRef.current) positions.current[module] = scrollRef.current.scrollTop
    if (rotation) return
    const index = order.indexOf(next)
    if (!drawer && index > 0) {
      pendingModule.current = next
      setRotation(index * STEP_ANGLE)
      return
    }
    setOrder(value => { const i = value.indexOf(next); return [...value.slice(i), ...value.slice(0, i)] })
    setModule(next)
    dismissMenu()
  }
  /** 关闭抽屉前保留阅读位置，关闭菜单不会关闭会话。 */
  function close() {
    if (scrollRef.current) positions.current[module] = scrollRef.current.scrollTop
    setMenuOpen(false)
    onClose()
  }

  if (!open) return null
  return <>
    {drawer && <button type="button" aria-label="关闭会话详情遮罩" tabIndex={-1} onClick={close} className="fixed inset-0 z-40 bg-black/20 backdrop-blur-sm" />}
    <aside ref={panelRef} tabIndex={-1} role={drawer ? 'dialog' : 'complementary'} aria-modal={drawer || undefined} aria-label="会话详情"
      className={`${drawer ? 'fixed inset-y-0 right-0 z-50 w-[min(22rem,calc(100vw-1rem))] shadow-panel' : 'w-80 shrink-0'} flex h-full flex-col border-l border-slate-800 bg-slate-900 outline-none`}
      onKeyDown={event => {
        if (event.key === 'Escape') {
          event.stopPropagation()
          if (menuOpen) dismissMenu(); else close()
        }
        if (drawer && event.key === 'Tab') {
          const nodes = Array.from(panelRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input, select, textarea, summary, [tabindex="0"]') ?? []).filter(node => node.getClientRects().length)
          const first = nodes[0], last = nodes[nodes.length - 1]
          if (event.shiftKey && (document.activeElement === first || document.activeElement === panelRef.current)) { event.preventDefault(); last?.focus() }
          else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panelRef.current)) { event.preventDefault(); first?.focus() }
        }
      }}>
      <header className="relative h-16 shrink-0 border-b border-slate-800">
        <h3 className={`absolute left-6 top-[26px] text-xs font-semibold text-slate-100 ${menuOpen && !drawer ? 'invisible' : ''}`}>{current.name}</h3>
        <div ref={menuRef} className="pointer-events-none absolute inset-x-0 top-0">
          <button ref={triggerRef} type="button" title="切换详情模块" aria-label="切换详情模块" aria-haspopup="menu" aria-expanded={menuOpen} aria-hidden={menuOpen || undefined} disabled={menuOpen} tabIndex={menuOpen ? -1 : 0}
            onClick={() => setMenuOpen(value => !value)} className={`relative z-30 ml-auto mr-0.5 mt-0.5 flex h-6 w-6 items-center justify-center rounded-full border border-slate-700 bg-slate-900 text-slate-400 hover:bg-slate-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500 ${menuOpen ? 'invisible pointer-events-none' : 'pointer-events-auto'}`}>
            <current.Icon size={12} />
          </button>
          {menuOpen && <div role="menu" aria-label="详情模块" className={drawer
            ? 'pointer-events-auto absolute right-0 top-10 z-20 w-44 rounded-2xl border border-slate-800 bg-panel p-2 shadow-panel'
            : 'detail-wheel pointer-events-none absolute right-0 top-0 z-20 overflow-hidden'}
            style={drawer ? undefined : { width: radius, height: radius, borderBottomLeftRadius: '100%' }}
            onKeyDown={event => {
              if (['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
                event.preventDefault()
                const nodes = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]:not(:disabled)'))
                const index = nodes.indexOf(document.activeElement as HTMLButtonElement)
                const next = event.key === 'Home' ? 0 : event.key === 'End' ? nodes.length - 1 : (index + (['ArrowUp', 'ArrowLeft'].includes(event.key) ? -1 : 1) + nodes.length) % nodes.length
                nodes[next]?.focus()
              }
            }}>
            <div className={drawer ? '' : 'absolute left-0'} style={drawer ? undefined : {
              top: -radius, width: radius * 2, height: radius * 2,
              transformOrigin: '50% 50%', transform: `rotate(${rotation}deg)`, transition: rotation ? 'transform 300ms ease-in-out' : 'none',
            }}>
            {(drawer ? MODULES : [...order, ...order.slice(0, SLOT_COUNT - 1)].map(id => WHEEL_ITEMS.find(item => item.id === id)!)).map(({ id, name, Icon }, index) => {
              const ghost = !drawer && index >= SLOT_COUNT
              const reserved = id !== 'overview' && id !== 'members'
              const sector = fanSector(index, SLOT_COUNT, radius)
              return <button key={`${index}:${id}`} type="button" role="menuitemradio" aria-label={name} aria-checked={module === id}
                aria-hidden={ghost || undefined} disabled={reserved || ghost} tabIndex={ghost || reserved ? -1 : 0} aria-disabled={reserved || !!rotation}
                title={name} onClick={() => { if (id === 'overview' || id === 'members') select(id) }} style={drawer ? undefined : {
                  clipPath: `path("${sector.path}")`, backgroundColor: paint.background, color: paint.text,
                }}
                className={drawer
                  ? `flex w-full items-center gap-2 rounded-xl border px-3 py-3 text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500 ${module === id ? 'border-indigo-300 bg-indigo-100 text-indigo-700' : 'border-slate-800 bg-panel text-slate-300 hover:bg-emerald-100'}`
                  : `wheel-sector group pointer-events-auto absolute inset-0 text-xs outline-none ${reserved ? 'cursor-default' : ''}`}>
                {!drawer && <span aria-hidden="true" className={`wheel-tint pointer-events-none absolute inset-0 ${module === id ? 'is-selected' : ''}`} />}
                {!drawer && <svg viewBox={`0 0 ${radius * 2} ${radius * 2}`} aria-hidden="true" className="pointer-events-none absolute inset-0 h-full w-full fill-none" style={{ stroke: paint.line }} strokeWidth={1}>
                  <path d={sector.path} />
                </svg>}
                <span className={drawer ? 'flex items-center gap-2' : 'absolute flex flex-col items-center gap-1 whitespace-nowrap'}
                  style={drawer ? undefined : { left: sector.label[0], top: sector.label[1], transform: `translate(-50%, -50%) rotate(${-rotation}deg)`, transition: rotation ? 'transform 300ms ease-in-out' : 'none' }}>
                  <Icon size={14} />{drawer ? name : id === 'overview' ? '概览' : id === 'members' ? '成员' : '未开放'}
                </span>
              </button>
            })}
            </div>
          </div>}
        </div>

      </header>
      <div ref={scrollRef} role="region" aria-label="会话详情内容" onScroll={event => { positions.current[module] = event.currentTarget.scrollTop }} className="min-h-0 flex-1 overflow-y-auto p-4">
        <div hidden={module !== 'overview'} className="space-y-4">
          <div className="rounded-2xl border border-indigo-100 bg-panel p-4"><p className="text-xs text-slate-500">会话名称</p><p className="mt-2 break-words text-sm font-semibold text-slate-100">{conversation.title}</p></div>
          <dl className="space-y-4 rounded-2xl border border-slate-800 bg-panel p-4 text-xs">
            <div className="flex justify-between gap-3"><dt className="text-slate-500">类型</dt><dd>{conversation.type === 'group' ? '群聊' : '单聊'}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-slate-500">参与角色</dt><dd>{conversation.role_ids.length}</dd></div>
            <div className="flex justify-between gap-3"><dt className="text-slate-500">会话状态</dt><dd>{conversation.archived ? '已归档' : '进行中'}{conversation.pinned ? ' · 已置顶' : ''}</dd></div>
            {user?.is_owner && <div><dt className="text-slate-500">绑定工作区</dt><dd className="mt-2 break-words">{binding?.display_name ?? (conversation.workspace_binding_id ? '工作区信息暂不可用' : '未绑定工作区')}</dd></div>}
          </dl>
        </div><div hidden={module !== 'members'} className="space-y-3">
          <div className="flex items-center justify-between gap-2"><p className="text-xs text-slate-500">参与角色 · {conversation.role_ids.length}</p>
            {conversation.type === 'group' && user?.is_owner && <button type="button" onClick={() => { if (drawer) close(); onManageMembers(conversation) }} className="text-xs text-indigo-600">管理群聊成员</button>}</div>
          {conversation.role_ids.map(id => {
            const role = roles.find(value => value.id === id)
            const summary = role ?? roleDirectory[id]
            return <div key={id} role="group" aria-label={`会话角色：${summary?.name ?? `角色 #${id}`}`} className="rounded-xl border border-slate-800 bg-panel p-3">
              <div className="flex items-center gap-2"><span className="rounded-lg bg-emerald-100 p-2 text-emerald-700"><Bot size={15} /></span>
                <div className="min-w-0 flex-1"><p className="truncate text-xs font-semibold" title={summary?.name}>{summary?.name ?? `角色 #${id}`}{summary?.deleted_at ? '（已删除）' : ''}</p>
                  {user?.is_owner && role && <p className="mt-1 truncate text-[10px] text-slate-500">{role.model_name}</p>}</div>
                {user?.is_owner && role && <button type="button" aria-label={`编辑角色：${role.name}`} onClick={() => { if (drawer) close(); onEditRole(role) }} className="text-xs text-indigo-600">编辑</button>}
              </div>
              {user?.is_owner && <RoleUsagePanel conversationId={conversation.id} roleId={id} />}
              {user?.is_owner && role?.description && <details className="mt-2 text-xs text-slate-500"><summary className="cursor-pointer">角色描述</summary><p className="mt-2 whitespace-pre-wrap break-words">{role.description}</p></details>}
            </div>
          })}
          {!conversation.role_ids.length && <p className="py-6 text-center text-xs text-slate-500">会话内暂未绑定任何角色</p>}
        </div>
      </div>
    </aside>
  </>
}
