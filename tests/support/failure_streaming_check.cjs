const { chromium } = require('../../frontend/node_modules/playwright');
const path = require('node:path');

(async () => {
  const [url, output] = process.argv.slice(2);
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  try {
    await page.goto(url);
    await page.locator('[contenteditable="true"]').first().fill('stream persistence failure test');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await page.getByText(/part0000/).first().waitFor();
    await page.getByText(/OSError: Injected local SQLite write failure/).first().waitFor();
    // A terminal snapshot can replace the streamed error component. Inspect
    // its details after the failed state settles, so the click is not lost.
    await page.getByRole('button', { name: '暂停', exact: true }).waitFor({ state: 'hidden' });
    await page.locator('.message.assistant .meta').filter({ hasText: 'failed' }).first().waitFor();
    await page.getByText("错误详情", { exact: true }).first().click();
    await page.locator("pre").filter({ hasText: "Injected local SQLite write failure" }).first().waitFor();
    if (!(await page.locator('.message.assistant').innerText()).includes('part0000')) throw new Error('Visible text withdrawn after persistence failure');
    if ((await page.locator('.message.assistant .meta').innerText()).includes('success')) throw new Error('Persistence failure reported as success');
    await page.reload();
    await page.getByText(/OSError: Injected local SQLite write failure/).first().waitFor();
    await page.getByText("错误详情", { exact: true }).first().click();
    await page.locator("pre").filter({ hasText: "Injected local SQLite write failure" }).first().waitFor();
    if (!(await page.locator('.message.assistant').innerText()).includes('part0000')) throw new Error('Persisted text missing after reload');
    console.log('SQLite failure stops streaming, retains visible text, and reports failure in the browser.');
  } catch (error) {
    await page.screenshot({ path: path.join(output, 'persistence-failure.png') });
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
