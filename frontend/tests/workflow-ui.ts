import { expect, type Page } from '@playwright/test'

export const workflowToolbar = (page: Page) => page.getByRole('toolbar', { name: '工作流操作', exact: true })
export const workflowInspector = (page: Page) => page.getByRole('region', { name: '工作流上下文', exact: true })

/** 使用产品入口打开模板；刷新会自行恢复对象，无须再次打断模块恢复。 */
export async function openWorkflowTemplate(page: Page, name: string, restored = false) {
  if (restored) { await expect(workflowToolbar(page)).toBeVisible(); return }
  await page.getByRole('button', { name: '切换详情模块', exact: true }).click()
  await page.getByRole('menuitemradio', { name: '工作流', exact: true }).locator('span').last().click()
  await page.getByRole('button', { name: `打开模板：${name}`, exact: true }).click()
}

export async function closeWorkflowDrawer(page: Page) {
  const drawer = page.getByRole('dialog', { name: '会话详情', exact: true })
  if (await drawer.isVisible()) { await page.keyboard.press('Escape'); await expect(drawer).not.toBeVisible() }
}

export async function workflowMore(page: Page, name: string) {
  await closeWorkflowDrawer(page)
  await workflowToolbar(page).getByRole('button', { name: '更多操作', exact: true }).click()
  await page.getByRole('menuitem', { name, exact: true }).click()
}

export async function saveWorkflow(page: Page) {
  await closeWorkflowDrawer(page)
  await workflowToolbar(page).getByRole('button', { name: /^(保存模板|提交本次运行调整)$/ }).click()
  await expect(workflowToolbar(page).getByRole('status')).toContainText('已提交')
}

export async function workflowOverview(page: Page) {
  if (!await page.getByRole('dialog', { name: '会话详情', exact: true }).isVisible()) {
    await expect(workflowToolbar(page).getByLabel('工作流对象')).toBeEnabled()
  }
  if (!await workflowInspector(page).isVisible()) await workflowToolbar(page).getByRole('button', { name: '详情', exact: true }).click()
  const overview = workflowInspector(page).getByRole('button', { name: '流程概况', exact: true })
  if (await overview.isVisible()) await overview.click()
}

export async function startWorkflow(page: Page, coordinated = false) {
  await closeWorkflowDrawer(page)
  if (coordinated) await workflowToolbar(page).getByLabel('运行方式', { exact: true }).selectOption('coordinated')
  await workflowToolbar(page).getByRole('button', { name: '启动流程', exact: true }).click()
}

export async function workflowRecords(page: Page) {
  await closeWorkflowDrawer(page)
  await workflowToolbar(page).getByRole('button', { name: '记录', exact: true }).click()
}

export async function workflowFeedback(page: Page, summary: string) {
  await closeWorkflowDrawer(page)
  await workflowToolbar(page).getByRole('button', { name: /^反馈( \d+)?$/ }).click()
  await workflowInspector(page).getByRole('button', { name: `打开反馈：${summary}`, exact: true }).click()
  return workflowInspector(page).getByRole('article', { name: `反馈：${summary}`, exact: true })
}
