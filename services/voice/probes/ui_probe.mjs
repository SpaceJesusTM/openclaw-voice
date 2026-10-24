// Live browser-path probe for the ClawVoice UI (apps/web) against `make dev`.
// Uses the Playwright already installed for experiments/higgs-realtime-1.5 and a
// virtual microphone fed with synthetic speech (macOS `say`); not a physical mic.
//
//   node probes/ui_probe.mjs --live [--url http://127.0.0.1:7860]
//
// Starts one harmless background *question* job (no changes) to show the task panel
// and the spoken completion notice. Report: captures/ui-probe.json + screenshots.
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';

if (!process.argv.includes('--live')) throw new Error('Pass --live to permit live API testing');
const require = createRequire(new URL('../../../experiments/higgs-realtime-1.5/package.json', import.meta.url));
const { chromium, expect } = require('@playwright/test');
const args = process.argv.slice(2);
const url = args.includes('--url') ? args[args.indexOf('--url') + 1] : 'http://127.0.0.1:7860';
const captures = new URL('../captures/', import.meta.url);
mkdirSync(captures, { recursive: true });

function wav(name, text) {
  const path = fileURLToPath(new URL(`probe-${name}.wav`, captures));
  if (!existsSync(path)) execFileSync('say', ['-o', path, '--file-format=WAVE', '--data-format=LEI16@48000', text]);
  return readFileSync(path).toString('base64');
}

const checks = [];
const started = Date.now();
const mark = (kind, extra = {}) => { checks.push({ kind, status: 'PASS', at_ms: Date.now() - started, ...extra }); console.log(`PASS ${kind}`); };
const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
let status = 'PASS';
let failure = null;
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript(() => {
    let context, destination;
    navigator.mediaDevices.getUserMedia = async () => {
      context = new AudioContext({ sampleRate: 48000 });
      await context.resume();
      destination = context.createMediaStreamDestination();
      // A real microphone streams continuously; an empty Web Audio destination does
      // not, which would hide the trailing silence that ends a spoken turn. Keep the
      // stream flowing with an inaudible tone (about -70 dBFS, far below VAD levels).
      const hum = context.createOscillator();
      const gain = context.createGain();
      hum.frequency.value = 40;
      gain.gain.value = 0.0003;
      hum.connect(gain).connect(destination);
      hum.start();
      return destination.stream;
    };
    window.__speakTestWav = async (base64) => {
      const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
      const buffer = await context.decodeAudioData(bytes.buffer);
      const source = context.createBufferSource();
      source.buffer = buffer; source.connect(destination); source.start();
    };
  });
  await page.goto(url);
  await expect(page.locator('#agent-name')).toContainText('Tari');
  await expect(page.locator('#gateway-badge')).toContainText(/HTTP|WebSocket/, { timeout: 15000 });
  await expect(page.locator('#topics')).toContainText('planner.today', { timeout: 15000 });
  mark('identity-and-state', { gateway: await page.locator('#gateway-badge').innerText() });
  await page.screenshot({ path: fileURLToPath(new URL('ui-before-connect.png', captures)), fullPage: true });

  await page.locator('#connect').click();
  await expect(page.locator('#model-badge')).toContainText('higgs-realtime-1.5-preview · verified', { timeout: 30000 });
  await expect(page.locator('.turn.bot').first()).toBeVisible({ timeout: 30000 });
  mark('connect-and-model', { badge: await page.locator('#model-badge').innerText() });

  await page.locator('#message').fill('Say exactly: browser path verified.');
  await page.locator('#send').click();
  await expect(page.locator('.turn.bot').last()).toContainText(/browser path verified/i, { timeout: 30000 });
  mark('typed-turn');

  const users = await page.locator('.turn.user').count();
  await page.evaluate((b64) => window.__speakTestWav(b64), wav('planner-today', 'What is on my planner today?'));
  await expect.poll(() => page.locator('.turn.user').count(), { timeout: 30000 }).toBeGreaterThan(users);
  await expect(page.locator('.turn.user').last()).toContainText(/planner/i, { timeout: 30000 });
  await expect(page.locator('#tools')).toContainText('get_context', { timeout: 30000 });
  // Wait for the answer itself (not a "let me check" preamble) and for it to finish playing.
  await expect(page.locator('.turn.bot').last()).toContainText(/clear|nothing|no (calendar )?events|block|scheduled/i, { timeout: 30000 });
  await expect(page.locator('#agent-state')).toContainText(/Ready/, { timeout: 30000 });
  const answer = await page.locator('.turn.bot').last().innerText();
  const words = answer.toLowerCase().split(/\s+/);
  const grams = words.slice(0, -5).map((_, i) => words.slice(i, i + 6).join(' '));
  if (new Set(grams).size !== grams.length) throw new Error(`transcript repeated text: ${answer}`);
  mark('spoken-turn-with-cache-tool', { heard: await page.locator('.turn.user').last().innerText(), answer });

  await page.locator('#message').fill('Please ask the agent in the background, as a question: what is two plus two?');
  await page.locator('#send').click();
  await expect(page.locator('#jobs .job').first()).toBeVisible({ timeout: 30000 });
  await expect(page.locator('#jobs .job').first().locator('.chip')).toContainText(/completed/, { timeout: 240000 });
  await expect(page.locator('.turn.notice').last()).toContainText(/task finished/i, { timeout: 30000 });
  await expect(page.locator('.turn.bot').last()).toContainText(/4|four/i, { timeout: 60000 });
  mark('background-job-and-notice', { job: await page.locator('#jobs .job').first().innerText() });
  await page.screenshot({ path: fileURLToPath(new URL('ui-connected.png', captures)), fullPage: true });

  await page.locator('#disconnect').click();
  await expect(page.locator('#agent-state')).toContainText('Not connected');
  mark('disconnect');
  if (errors.length) throw new Error(`page errors: ${errors.join('; ')}`);
} catch (error) {
  status = 'FAIL';
  failure = String(error?.message || error).slice(0, 2000);
  console.log(`FAIL ${failure}`);
} finally {
  await browser.close();
  writeFileSync(new URL('ui-probe.json', captures), JSON.stringify({ status, failure, checks, seconds: (Date.now() - started) / 1000 }, null, 2));
  console.log(JSON.stringify({ status, failure, checks }, null, 2));
}
process.exit(status === 'PASS' ? 0 : 1);
