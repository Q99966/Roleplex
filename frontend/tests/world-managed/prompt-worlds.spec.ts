import { test, expect, type Page } from '@playwright/test'
import { ensureOwnerSession } from '../owner'

async function openPrompts(page: Page) {
  await page.getByRole('button', { name: /管理运行世界与存储/ }).click()
  await page.getByRole('tab', { name: '提示词与规则', exact: true }).click()
}

async function switchWorld(page: Page, target: string) {
  await page.getByRole('tab', { name: '运行世界与存储', exact: true }).click()
  page.once('dialog', dialog => dialog.accept())
  await page.getByLabel('切换世界', { exact: true }).selectOption(target)
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible({ timeout: 30_000 })
  await ensureOwnerSession(page)
}

test('世界提示词与未保存草稿不跨 World，重启切回仍保留已提交版本', async ({ page }) => {
  test.setTimeout(100_000); page.setDefaultTimeout(12_000)
  await page.setViewportSize({ width: 1680, height: 1000 }); await ensureOwnerSession(page)
  await openPrompts(page)
  const region = page.getByRole('region', { name: '世界提示词配置', exact: true })
  await expect(region).toContainText('alpha')
  await region.getByLabel('世界系统提示词', { exact: true }).fill('ALPHA 的已提交背景')
  await region.getByRole('button', { name: '保存世界提示词', exact: true }).click()
  await expect(region.getByRole('status')).toContainText('已保存')
  await region.getByLabel('世界系统提示词', { exact: true }).fill('ALPHA 的未提交草稿')
  await switchWorld(page, 'beta')
  await openPrompts(page)
  await expect(region).toContainText('beta')
  await expect(region.getByLabel('世界系统提示词', { exact: true })).toHaveValue('')
  await expect(region.getByLabel('自定义平台规则', { exact: true })).not.toBeChecked()
  await region.getByLabel('世界系统提示词', { exact: true }).fill('BETA 的独立背景')
  await region.getByRole('button', { name: '保存世界提示词', exact: true }).click()
  await expect(region.getByRole('status')).toContainText('已保存')
  await switchWorld(page, 'alpha')
  await openPrompts(page)
  await expect(region).toContainText('alpha')
  await expect(region.getByLabel('世界系统提示词', { exact: true })).toHaveValue('ALPHA 的已提交背景')
  await expect(region).not.toContainText('BETA 的独立背景')
})
