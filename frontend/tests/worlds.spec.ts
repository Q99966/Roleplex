import { expect, test } from '@playwright/test'
import { ensureOwnerSession } from './owner'

test.describe('A1 world visibility', () => {
  test('shows the current world and disables switching without the wrapper', async ({ page }) => {
    await ensureOwnerSession(page)

    await expect(page.getByText('default', { exact: true }).first()).toBeVisible()
    const worldSettings = page.getByRole('button', { name: /管理运行世界与存储/ })
    await worldSettings.focus()
    await page.keyboard.press('Enter')
    const selector = page.getByLabel('切换世界')
    await expect(selector).toBeVisible()
    await expect(selector).toBeDisabled()
    await expect(page.getByText('需使用世界包装器启动').first()).toBeVisible()
  })

  test('does not expose workspace settings to a Guest', async ({ page }) => {
    await ensureOwnerSession(page)
    const guestToken = await page.evaluate(async (suffix) => {
      const response = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: `guest_workspace_${suffix}`,
          nickname: '工作区 Guest',
          password: 'Roleplex-Test-1234',
        }),
      })
      if (!response.ok) throw new Error(`guest registration failed: ${response.status}`)
      return (await response.json()).access_token as string
    }, `${Date.now()}`)
    await page.evaluate((token) => localStorage.setItem('roleplex_token', token), guestToken)
    await page.reload()

    await page.getByTitle('打开设置', { exact: true }).click()
    await expect(page.getByRole('dialog', { name: '系统与环境设置' })).toBeVisible()
    await expect(page.getByRole('tab', { name: '工作区', exact: true })).toHaveCount(0)
  })
})
