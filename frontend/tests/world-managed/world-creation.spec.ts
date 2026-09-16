import { expect, test } from '@playwright/test'
import { ensureOwnerSession } from '../owner'

/** 经真实接口创建、校验并切入新 World，结束时回到原 World 供其他专项用例使用。 */
test('creates an independent world and registers its first Owner', async ({ page }) => {
  await ensureOwnerSession(page)
  const source = await page.evaluate(async () => (await (await fetch('/api/health')).json()).world_name as string)
  const target = `新世界-${process.env.ROLEPLEX_E2E_STAMP}`
  await page.getByRole('button', { name: /管理运行世界与存储/ }).click()
  const name = page.getByRole('textbox', { name: '世界名称', exact: true })
  const submit = page.getByRole('button', { name: '创建世界', exact: true })
  await expect(submit).toBeDisabled()
  await name.fill('../outside')
  await submit.click()
  await expect(page.getByRole('alert')).toContainText('世界名称无效')
  await name.fill(target)
  await submit.click()
  await expect(page.getByRole('status')).toContainText(`世界“${target}”已创建`)
  const selector = page.getByLabel('切换世界')
  await expect(selector).toHaveValue(source)
  await expect(selector.locator('option', { hasText: target })).toHaveCount(1)
  await name.fill(target)
  await submit.click()
  await expect(page.getByRole('alert')).toContainText('这个世界名称已被占用')
  page.once('dialog', (dialog) => void dialog.dismiss())
  await selector.selectOption(target)
  await expect(selector).toHaveValue(source)
  page.once('dialog', (dialog) => void dialog.accept())
  await selector.selectOption(target)
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible({ timeout: 30_000 })
  expect(await page.evaluate(() => localStorage.getItem('roleplex_token'))).toBeNull()
  await ensureOwnerSession(page)
  const state = await page.evaluate(async () => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('roleplex_token')}` }
    const get = async (url: string) => (await fetch(url, { headers })).json()
    return { user: await get('/api/auth/me'), roles: await get('/api/roles'), configs: await get('/api/model-configs') }
  })
  expect(state.user.is_owner).toBe(true)
  expect(state.roles).toEqual([])
  expect(state.configs).toEqual([])
  await page.getByRole('button', { name: /管理运行世界与存储/ }).click()
  await expect(page.getByLabel('切换世界')).toHaveValue(target)
  page.once('dialog', (dialog) => void dialog.accept())
  await page.getByLabel('切换世界').selectOption(source)
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible({ timeout: 30_000 })
})
