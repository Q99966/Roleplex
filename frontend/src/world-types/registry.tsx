import { lazy, type ComponentType } from 'react'
import type { WorldTypeView } from '../api/worldTypes'
import { installedWorldTypePages } from './installed'

export type WorldTypePageProps = { view: WorldTypeView; onOpenConversation: (id: number) => void }
const pages: Record<string, ComponentType<WorldTypePageProps>> = {
  general: ({ view }) => <p className="text-xs leading-relaxed text-slate-500">{view.descriptor.description}</p>,
}
for (const [id, page] of Object.entries(installedWorldTypePages)) registerWorldTypePage(id, page)
// 仅隔离开发测试入口安装；正常构建既不注册也不加载受控测试页面。
if (import.meta.env.DEV && import.meta.env.VITE_WORLD_TYPE_FIXTURE === 'true') {
  pages.fixture = lazy(() => import('./fixture/FixturePage').then(module => ({ default: module.FixturePage })))
}

/** 类型分支在受信装配文件中注册，不按后端字符串或存档路径执行任意 import。 */
export function registerWorldTypePage(id: string, page: ComponentType<WorldTypePageProps>) {
  if (pages[id]) throw new Error('WORLD_TYPE_PAGE_ALREADY_REGISTERED')
  pages[id] = page
}
export function worldTypePage(id: string) { return pages[id] }
