import { expect, test } from '@playwright/test'
import { ensureOwnerSession } from './owner'

test.describe('A1 world visibility', () => {
  test('shows the current world and disables switching without the wrapper', async ({ page }) => {
    await ensureOwnerSession(page)

    await expect(page.getByText('default', { exact: true }).first()).toBeVisible()
    const selector = page.getByLabel('切换世界')
    await expect(selector).toBeVisible()
    await expect(selector).toBeDisabled()
    await expect(page.getByText('需使用世界包装器启动')).toBeVisible()
  })
})
