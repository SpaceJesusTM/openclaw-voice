// Optional live comparison against the unchanged Step 1 browser app.
// Uses its existing Playwright dependency and the same synthetic WAV inputs.
import { createRequire } from 'node:module';
import { readFile, writeFile, mkdir } from 'node:fs/promises';

if (!process.argv.includes('--live')) throw new Error('Pass --live to permit live API testing');
const require = createRequire(new URL('../higgs-realtime-1.5/package.json', import.meta.url));
const { chromium, expect } = require('@playwright/test');
const args = process.argv.slice(2);
const url = args.includes('--url') ? args[args.indexOf('--url') + 1] : 'http://127.0.0.1:3002';
const audio = await readFile(new URL('captures/planner-input.wav', import.meta.url));
const barge = await readFile(new URL('captures/interrupt-input.wav', import.meta.url));
const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
const checks = [];
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    let context, destination;
    navigator.mediaDevices.getUserMedia = async () => {
      context = new AudioContext({ sampleRate: 48000 });
      await context.resume();
      destination = context.createMediaStreamDestination();
      return destination.stream;
    };
    window.__speakTestWav = async base64 => {
      const bytes = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
      const buffer = await context.decodeAudioData(bytes.buffer);
      const source = context.createBufferSource();
      source.buffer = buffer; source.connect(destination); source.start();
    };
  });
  await page.goto(url);
  await page.getByRole('button', { name: 'Connect to preview' }).click();
  await expect(page.locator('#status')).toHaveText('Preview verified', { timeout: 20000 });
  checks.push({ kind: 'model', status: 'PASS', acknowledged: await page.locator('#acknowledged').innerText() });
  await page.locator('#message').fill('Say: Higgs preview connection verified.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.locator('.turn.assistant').last()).toContainText('Higgs preview connection verified', { timeout: 30000 });
  await expect(page.locator('#planner')).toBeEnabled({ timeout: 30000 });
  checks.push({ kind: 'text', status: 'PASS' });
  await page.locator('#stop').click();
  await page.locator('#planner').click();
  await expect(page.locator('#tools')).toContainText('completed', { timeout: 30000 });
  await expect(page.locator('.turn.assistant').last()).toContainText('Copper Otter', { timeout: 30000 });
  await expect(page.locator('#send')).toBeEnabled({ timeout: 30000 });
  checks.push({ kind: 'planner', status: 'PASS' });
  await page.locator('#stop').click();
  await page.locator('#mic').click();
  await expect(page.locator('#mic')).toHaveText('Stop microphone');
  const userRows = await page.locator('.turn.user').count();
  const assistantRows = await page.locator('.turn.assistant').count();
  await page.evaluate(base64 => window.__speakTestWav(base64), audio.toString('base64'));
  await expect.poll(() => page.locator('.turn.user').count(), { timeout: 30000 }).toBeGreaterThan(userRows);
  await expect(page.locator('.turn.user').last()).toContainText(/planner.*today/i);
  await expect.poll(() => page.locator('.turn.assistant').count(), { timeout: 30000 }).toBeGreaterThan(assistantRows);
  await expect(page.locator('.turn.assistant').last()).toContainText('Copper Otter', { timeout: 30000 });
  await expect(page.locator('#send')).toBeEnabled({ timeout: 30000 });
  checks.push({ kind: 'audio-input', status: 'PASS', transcription: await page.locator('.turn.user').last().innerText() });
  await page.locator('#stop').click();
  const interruptedBefore = await page.locator('.turn.interrupted').count();
  const cutsBefore = await page.locator('#log summary').filter({ hasText: 'conversation.item.truncate' }).count();
  await page.locator('#message').fill('Count from one to forty slowly, saying every number.');
  await page.locator('#send').click();
  await expect(page.locator('.turn.assistant').last()).toContainText(/one|1/i, { timeout: 30000 });
  await page.waitForTimeout(300);
  await page.evaluate(base64 => window.__speakTestWav(base64), barge.toString('base64'));
  await expect.poll(() => page.locator('.turn.interrupted').count(), { timeout: 30000 }).toBeGreaterThan(interruptedBefore);
  await expect.poll(() => page.locator('#log summary').filter({ hasText: 'conversation.item.truncate' }).count(), { timeout: 30000 }).toBeGreaterThan(cutsBefore);
  await expect(page.locator('.turn.assistant').last()).toContainText('Interruption confirmed', { timeout: 30000 });
  checks.push({ kind: 'barge-in', status: 'PASS', interruption: await page.locator('.turn.interrupted').last().innerText() });
  expect(errors).toEqual([]);
  await page.screenshot({ path: new URL('captures/direct-comparison.png', import.meta.url).pathname, fullPage: true });
  await page.locator('#disconnect').click();
  await mkdir(new URL('captures/', import.meta.url), { recursive: true });
  await writeFile(new URL('captures/direct-browser-comparison.json', import.meta.url), JSON.stringify({ status: 'PASS', checks }, null, 2));
  console.log(JSON.stringify({ status: 'PASS', checks }, null, 2));
} finally { await browser.close(); }
