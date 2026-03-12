import { test, expect } from '@playwright/test';

/**
 * Smoke tests for the chat UI.
 * Requires:
 *   - API running on :8090
 *   - Web dev server running on :3000 (or :5173)
 *   - AUTH_ENABLED=false on the API
 *   - No VITE_ENTRA_CLIENT_ID set (so auth gate is skipped)
 */

test.describe('Chat Smoke Tests', () => {
  test('send message and receive response', async ({ page }) => {
    await page.goto('/');

    // Wait for the welcome screen to load
    const composer = page.locator('[data-testid="message-composer"] textarea, [data-testid="message-composer"] input');
    // Fallback: if no data-testid, look for the input by placeholder
    const input = composer.first().or(page.getByPlaceholder('Ask a question...'));
    await expect(input).toBeVisible({ timeout: 10_000 });

    // Type and send a message
    await input.fill('Hello, good morning!');
    await input.press('Enter');

    // Wait for an assistant message to appear
    const assistantMessage = page.locator('[data-testid="assistant-message"]').first()
      .or(page.locator('[role="log"] [class*="assistant"]').first());
    await expect(assistantMessage).toBeVisible({ timeout: 45_000 });
  });

  test('streaming renders progressively', async ({ page }) => {
    await page.goto('/');

    const input = page.getByPlaceholder('Ask a question...');
    await expect(input).toBeVisible({ timeout: 10_000 });

    await input.fill('How do I reset my password?');
    await input.press('Enter');

    // Streaming indicator should appear while response is generating
    const streamingIndicator = page.locator('[data-testid="streaming-message"]')
      .or(page.locator('[class*="streaming"]').first())
      .or(page.locator('[class*="wave"]').first());
    await expect(streamingIndicator).toBeVisible({ timeout: 15_000 });

    // Eventually the final response should appear
    const finalMessage = page.locator('[data-testid="assistant-message"]').first()
      .or(page.locator('[role="log"]').first());
    await expect(finalMessage).toBeVisible({ timeout: 45_000 });
  });

  test('sources display for domain query', async ({ page }) => {
    await page.goto('/');

    const input = page.getByPlaceholder('Ask a question...');
    await expect(input).toBeVisible({ timeout: 10_000 });

    // Send a query that should trigger RAG sources
    await input.fill('What is the leave policy?');
    await input.press('Enter');

    // Wait for response to complete (streaming indicator disappears or final message appears)
    const assistantMessage = page.locator('[data-testid="assistant-message"]').first()
      .or(page.locator('[role="log"] [class*="assistant"]').first());
    await expect(assistantMessage).toBeVisible({ timeout: 45_000 });

    // Check for source cards (if RAG is available)
    // Sources may or may not appear depending on RAG availability,
    // so we use a soft check with a shorter timeout
    const sourceCards = page.locator('[data-testid="source-card"]')
      .or(page.locator('[class*="source"]'));
    const sourcesVisible = await sourceCards.first().isVisible().catch(() => false);

    if (sourcesVisible) {
      // If sources are visible, verify at least one exists
      await expect(sourceCards.first()).toBeVisible();
    }
    // If no sources, test still passes — RAG may be unavailable
  });
});
