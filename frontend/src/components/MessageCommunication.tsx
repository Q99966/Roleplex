import type { Message, Role, CommunicationActor } from '../api/client'

function actorLabel(actor: CommunicationActor) {
  if (actor.kind === 'world_manager' && actor.name !== '世界管理者') return `${actor.name} · 世界管理者`
  return actor.duty === 'group_coordinator' ? `${actor.name} · 群协调者` : actor.name
}

/** 优先使用宿主保存的真实主体，自动安排不会因授权人为 Owner 就署名“我”。 */
export function messageAuthor(message: Message, user: { id: number; nickname: string } | null, roles: Record<number, Role>) {
  const actor = message.communication?.actor
  if (actor) return actorLabel(actor)
  if (message.sender_type === 'user') return message.sender_id === user?.id ? user.nickname : `用户 #${message.sender_id}`
  if (message.sender_type === 'system' || message.sender_type === 'orchestrator') return '历史工作流记录'
  return message.sender_id === null ? '系统' : roles[message.sender_id]?.name ?? `角色 #${message.sender_id}`
}

export function CommunicationAddress({ message }: { message: Message }) {
  const value = message.communication
  if (!value) return null
  return <div className="mb-2 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500" aria-label="消息接收对象">
    {value.via && <span className="mr-1">依据{value.via.kind === 'user' ? '用户配置' : `${actorLabel(value.via)}的计划`}</span>}
    {value.recipients.map(target => <span key={`${target.kind}:${target.id}`} className="rounded-full border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-indigo-700">@{actorLabel(target)}</span>)}
    {message.reply_to_id && <span>回复 #{message.reply_to_id}</span>}
  </div>
}
