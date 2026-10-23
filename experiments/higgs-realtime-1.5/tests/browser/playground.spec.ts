import { test, expect, type Page, type WebSocketRoute } from '@playwright/test';
import { PREVIEW_MODEL, type WireEvent } from '../../src/protocol';

// Entirely offline: route both credential/config endpoints and every Boson socket.
async function mock(page: Page, model: string | null = PREVIEW_MODEL) {
  const sent: WireEvent[] = [];
  let socket!: WebSocketRoute;
  let onSend: ((event: WireEvent) => void) | undefined;
  const browserErrors: string[] = [];
  page.on('pageerror', error => browserErrors.push(error.message));
  await page.route('**/api/config', route => route.fulfill({ json: {
    model: PREVIEW_MODEL, wsUrl: `wss://api.boson.ai/v1/realtime?model=${PREVIEW_MODEL}`,
    voice: 'default', keyAvailable: true,
  } }));
  await page.route('**/api/token', route => route.fulfill({ json: { value: 'bai-eph-offline-browser', expires_at: Date.now() / 1000 + 600 } }));
  await page.routeWebSocket('wss://api.boson.ai/**', ws => {
    socket = ws;
    ws.onMessage(raw => {
      const event = JSON.parse(String(raw)) as WireEvent; sent.push(event);
      if (event.type === 'session.update') ws.send(JSON.stringify({ type: 'session.created', session: {
        ...(model !== null ? { model } : {}), audio: { output: { voice: 'default' } },
      } }));
      onSend?.(event);
    });
  });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Connect to preview' })).toBeEnabled();
  return { sent, browserErrors, emit: (event: WireEvent) => socket.send(JSON.stringify(event)),
    respond: (handler: (event: WireEvent) => void) => { onSend = handler; } };
}
async function connect(page: Page) {
  await page.getByRole('button', { name: 'Connect to preview' }).click();
  await expect(page.locator('#status')).toHaveText('Preview verified');
}
function audioResponse(emit: (event: WireEvent) => void, id: string, text: string, duration = 24000) {
  emit({ type: 'response.created', response: { id } });
  emit({ type: 'response.output_item.added', response_id: id, item: { id: `${id}_item`, type: 'message', role: 'assistant' } });
  emit({ type: 'response.output_audio.delta', response_id: id, item_id: `${id}_item`, content_index: 0, delta: Buffer.alloc(duration * 2).toString('base64') });
  emit({ type: 'response.output_audio_transcript.delta', item_id: `${id}_item`, delta: text });
  emit({ type: 'response.output_audio_transcript.done', item_id: `${id}_item`, transcript: text });
  emit({ type: 'response.output_audio.done', item_id: `${id}_item` });
  emit({ type: 'response.done', response: { id, status: 'completed', output: [] } });
}

test('text/audio and real local planner handler complete with duplicate event protection', async ({ page }) => {
  const app = await mock(page); let phase = 0;
  app.respond(event => {
    if (event.type !== 'response.create') return;
    phase++;
    if (phase === 1) audioResponse(app.emit, 'hello', 'Hello from the offline transport.');
    else if (phase === 2) {
      app.emit({ type: 'response.created', response: { id: 'planner' } });
      const call = { type: 'response.function_call_arguments.done', response_id: 'planner',
        call_id: 'planner_call', name: 'get_demo_planner', arguments: '{"day":"today"}' };
      app.emit(call); app.emit(call);
      app.emit({ type: 'response.done', response: { id: 'planner', status: 'completed', output: [{ ...call, type: 'function_call' }] } });
    } else audioResponse(app.emit, 'answer', 'Copper Otter rehearsal at 10:17. Code NEBULA-742.');
  });
  await connect(page);
  await expect(page.locator('#acknowledged')).toHaveText(PREVIEW_MODEL);
  await page.getByPlaceholder('Type a message…').fill('Hi there');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.locator('#transcript')).toContainText('Hello from the offline transport.');
  await page.getByRole('button', { name: /Try:/ }).click();
  await expect(page.locator('#tools')).toContainText('completed');
  await expect(page.locator('#tools')).toContainText('NEBULA-742');
  await expect(page.locator('#transcript')).toContainText('Code NEBULA-742');
  expect(app.sent.filter(e => (e.item as any)?.type === 'function_call_output')).toHaveLength(1);
  expect(app.sent.filter(e => e.type === 'response.create')).toHaveLength(3);
  expect(app.sent[0]).toMatchObject({ type: 'session.update', session: { model: PREVIEW_MODEL } });
  expect(app.browserErrors).toEqual([]);
});

for (const model of ['higgs-realtime', null]) {
  test(`unaccepted acknowledged model ${model} stops connection and disables input`, async ({ page }) => {
    await mock(page, model);
    await page.getByRole('button', { name: 'Connect to preview' }).click();
    await expect(page.locator('#status')).toHaveText('Connection failed');
    await expect(page.locator('#acknowledged')).toHaveText(model ?? '(missing)');
    await expect(page.locator('#notice')).toContainText('no fallback');
    await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeDisabled();
  });
}

test('microphone streams PCM; VAD interruption cuts queued playback at heard position', async ({ page }) => {
  const app = await mock(page);
  await connect(page);
  await page.getByRole('button', { name: 'Start microphone' }).click();
  await expect(page.getByRole('button', { name: 'Stop microphone' })).toBeEnabled();
  await expect.poll(() => app.sent.filter(e => e.type === 'input_audio_buffer.append').length).toBeGreaterThan(1);
  const frame = app.sent.find(e => e.type === 'input_audio_buffer.append')!;
  expect(Buffer.from(String(frame.audio), 'base64').length).toBe(4800);
  audioResponse(app.emit, 'long', 'Generated words that may never reach the speaker.', 24000 * 5);
  app.emit({ type: 'input_audio_buffer.speech_started', item_id: 'user_voice' });
  await expect(page.locator('.turn.interrupted')).toContainText('Interrupted after approximately');
  await expect.poll(() => app.sent.filter(e => e.type === 'conversation.item.truncate').length).toBe(1);
  const cut = app.sent.find(e => e.type === 'conversation.item.truncate')!;
  expect(cut.item_id).toBe('long_item');
  expect(Number(cut.audio_end_ms)).toBeLessThan(5000);
  expect(app.sent.some(e => e.type === 'response.cancel')).toBe(false);
  await expect(page.locator('.turn.interrupted details')).not.toHaveAttribute('hidden', '');
  app.emit({ type: 'input_audio_buffer.speech_stopped' });
  await page.getByRole('button', { name: 'Stop microphone' }).click();
  await expect(page.getByRole('button', { name: 'Start microphone' })).toBeEnabled();
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
  await expect(page.locator('#status')).toHaveText('Disconnected');
  expect(app.browserErrors).toEqual([]);
});

test('native web search is logged separately and never dispatched as a local function', async ({ page }) => {
  const app = await mock(page);
  await page.getByLabel('Native web search').check();
  await connect(page);
  expect((app.sent[0].session as any).tools).toContainEqual({ type: 'web_search' });
  app.emit({ type: 'response.created', response: { id: 's1' } });
  app.emit({ type: 'response.output_item.added', item: { type: 'function_call', name: 'web_search', call_id: 'search1', executor: 'server' } });
  app.emit({ type: 'response.function_call_arguments.done', name: 'web_search', call_id: 'search1', arguments: '{"queries":["news"]}' });
  app.emit({ type: 'response.done', response: { id: 's1', output: [] } });
  await expect(page.locator('#tools')).toContainText('SERVER web_search');
  expect(app.sent.some(e => e.type === 'response.create' || (e.item as any)?.type === 'function_call_output')).toBe(false);
  expect(app.browserErrors).toEqual([]);
});

test('missing API key is visible, and mobile layout has no horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.locator('#notice')).toContainText('Offline mode');
  await page.getByRole('button', { name: 'Connect to preview' }).click();
  await expect(page.locator('#notice')).toContainText('BOSON_API_KEY is missing');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('API error closes the session visibly without a fallback or retry loop', async ({ page }) => {
  const app = await mock(page);
  await connect(page);
  app.emit({ type: 'error', error: { code: 'invalid_voice', message: 'Unknown voice ID' } });
  await expect(page.locator('#notice')).toContainText('invalid_voice');
  await expect(page.locator('#status')).toHaveText('Connection failed');
  await expect(page.getByRole('button', { name: 'Start microphone' })).toBeDisabled();
});
