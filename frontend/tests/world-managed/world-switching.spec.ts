import { expect, test } from '@playwright/test'
import { ensureOwnerSession } from '../owner'

test('switches physical worlds, restarts the backend, and requires a new login', async ({ page }) => {
  await ensureOwnerSession(page)

  const selector = page.getByLabel('切换世界')
  await expect(selector).toBeEnabled()
  await expect(selector).toHaveValue('alpha')
  await expect(selector.locator('option')).toHaveCount(2)

  page.once('dialog', (dialog) => void dialog.accept())
  await selector.selectOption('beta')
  await expect(page.getByRole('status')).toContainText('正在进入世界“beta”')

  // 包装器重启到 beta 后，前端清除 alpha Token 并回到认证页。
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible({ timeout: 30_000 })
  const health = await page.evaluate(async (base) => (await fetch(`${base}/api/health`)).json(), process.env.ROLEPLEX_E2E_API_ORIGIN)
  expect(health.world_name).toBe('beta')

  // beta 是物理独立的新库；同名 Owner 需要重新创建，随后顶栏显示 beta。
  await ensureOwnerSession(page)
  await expect(page.getByText('beta', { exact: true }).first()).toBeVisible()
  await page.screenshot({ path: 'test-results/world-switching-beta.png', fullPage: true })
})
