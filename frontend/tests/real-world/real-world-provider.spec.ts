import { test } from '@playwright/test'
import { verifyRealProviderConversation } from '../real/provider-flow'

const STAMP = process.env.ROLEPLEX_REAL_WORLD_E2E_STAMP ?? 'missing'
const API_ORIGIN = process.env.ROLEPLEX_E2E_API_ORIGIN ?? 'http://127.0.0.1:8004'

test('runs two real provider turns inside an isolated managed world', async ({ page }) => {
  await verifyRealProviderConversation(page, {
    stamp: STAMP,
    apiOrigin: API_ORIGIN,
    expectedWorldManaged: true,
    screenshotName: 'real-world-provider-chat.png',
  })
})
