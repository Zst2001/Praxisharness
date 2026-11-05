const { chromium } = require('../../frontend/node_modules/playwright');
const assert = require('node:assert/strict');

(async () => {
  const [url, turnId, situation, recommendation] = process.argv.slice(2);
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  page.setDefaultTimeout(12_000);
  let saves = 0;
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/memory/project-experiences') saves++;
  });
  try {
    await page.goto(url, { waitUntil: 'networkidle' });
    const session = page.getByRole('button', { name: '配置输出修复', exact: true });
    if (!(await session.isVisible())) await page.getByText('记忆验证项目', { exact: true }).click();
    await session.press('Enter');
    await page.getByRole('button', { name: '追踪修复 · output' }).first().click();
    await page.getByRole('button', { name: '保存为项目经验' }).click();
    const form = page.getByRole('dialog', { name: '保存为项目经验' });
    await form.getByLabel('适用情形').fill(situation);
    await form.getByLabel('建议做法').fill(recommendation);
    assert.equal(saves, 0);
    await form.getByRole('button', { name: '确认保存' }).click();
    await page.getByText('已保存为项目经验', { exact: true }).waitFor();
    assert.equal(saves, 1);
    await page.getByRole('button', { name: /个人简介/ }).click();
    await page.getByRole('menuitem', { name: '记忆', exact: true }).click();
    await page.getByText(situation, { exact: true }).waitFor();
    await page.getByRole('button', { name: '证据' }).click();
    const evidence = page.getByRole('dialog', { name: new RegExp(situation) });
    await evidence.getByText('版本 1 · 条件 output · 第 1 轮失败 → 第 2 轮通过', { exact: true }).waitFor();
    assert.ok((await evidence.innerText()).includes(turnId));
    const reportLink = await evidence.getByRole('link', { name: '下载原始验收报告' }).getAttribute('href');
    const report = await (await page.request.get(new URL(reportLink, url).href)).json();
    assert.equal(report.turn_id, turnId);
    assert.deepEqual(report.acceptance.reports.map(round => round.status), ['failed', 'passed']);
  } catch (error) {
    // The server uses an isolated test root containing only the fixture conversation.
    console.error((await page.locator('body').innerText()).slice(-3500));
    throw error;
  } finally {
    await page.close();
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
