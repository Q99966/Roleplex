import { test } from '@playwright/test'
import { verifyRealProviderConversation } from './provider-flow'

const STAMP = process.env.ROLEPLEX_REAL_E2E_STAMP ?? 'missing'
const API_ORIGIN = process.env.ROLEPLEX_E2E_API_ORIGIN ?? 'http://127.0.0.1:8002'

test('keeps browser conversation history across two real provider turns', async ({ page }) => {
  await verifyRealProviderConversation(page, {
    stamp: STAMP,
    apiOrigin: API_ORIGIN,
    expectedWorldManaged: false,
    screenshotName: 'real-provider-chat.png',
  })
})
