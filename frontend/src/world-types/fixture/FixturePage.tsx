import type { WorldTypePageProps } from '../registry'

/** 仅 e2e 装配的受控页面，证明类型模块无需修改主工作台即可呈现自己的内容。 */
export function FixturePage({ view, onOpenConversation }: WorldTypePageProps) {
  return <section aria-label="受控类型专页" className="space-y-2 rounded-xl border border-indigo-200 bg-panel p-3 text-xs">
    <h4 className="font-medium">类型接入验证</h4>
    <p>{String(view.configuration.label || '等待配置')}</p>
    {typeof view.initialization.resources.conversation_id === 'number' && <button type="button" className="text-indigo-600 underline"
      onClick={() => onOpenConversation(view.initialization.resources.conversation_id as number)}>打开类型会话</button>}
  </section>
}
