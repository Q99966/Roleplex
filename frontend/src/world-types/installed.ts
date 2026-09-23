import type { ComponentType } from 'react'
import type { WorldTypePageProps } from './registry'

/** 产品类型分支在此静态登记自己的组件；存档不能决定动态模块路径。 */
export const installedWorldTypePages: Record<string, ComponentType<WorldTypePageProps>> = {}
