import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseEvent, PREVIEW_MODEL, requirePreview, resolvedModel, websocketUrl, debugEvent, truncate } from '../src/protocol';
import { sessionConfig } from '../src/sessionConfig';
import { decodePcm, encodePcm, heardMilliseconds } from '../src/pcm';
import { Interruptions } from '../src/interruptions';
import type { WireEvent } from '../src/protocol';

test('preview model must match in configuration, URL, session, and acknowledgement', () => {
  assert.equal(requirePreview(), PREVIEW_MODEL);
  assert.equal(websocketUrl(PREVIEW_MODEL), 'wss://api.boson.ai/v1/realtime?model=higgs-realtime-1.5-preview');
  const config = sessionConfig({ model: PREVIEW_MODEL, voice: 'example-voice', webSearch: false });
  assert.equal((config.session as Record<string, unknown>).model, PREVIEW_MODEL);
  assert.equal(resolvedModel({ type: 'session.created', session: { model: PREVIEW_MODEL } }), PREVIEW_MODEL);
  for (const model of ['higgs-realtime', '', 'unknown']) {
    assert.throws(() => requirePreview(model), /No fallback/);
    assert.throws(() => resolvedModel({ type: 'session.updated', session: { model } }), /no fallback/);
  }
  assert.throws(() => resolvedModel({ type: 'session.created', session: {} }), /missing/);
});

test('session config uses audio-only modality, PCM16 at 24 kHz, STT and explicit server VAD', () => {
  const session = sessionConfig({ model: PREVIEW_MODEL, voice: 'voice-123', webSearch: true }).session as any;
  assert.deepEqual(session.output_modalities, ['audio']);
  assert.deepEqual(session.audio.input.format, { type: 'audio/pcm', rate: 24000 });
  assert.deepEqual(session.audio.input.turn_detection, { type: 'server_vad' });
  assert.equal(session.audio.input.transcription.model, 'higgs-stt-3.1');
  assert.equal(session.audio.output.voice, 'voice-123');
  assert.deepEqual(session.tools[1], { type: 'web_search' });
  assert.equal(session.tools[0].name, 'get_demo_planner');
});

test('parser tolerates future event types and rejects malformed envelopes', () => {
  assert.deepEqual(parseEvent('{"type":"future.event","value":17}'), { type: 'future.event', value: 17 });
  for (const raw of ['{', 'null', '[]', '{}', '{"type":3}', '{"type":""}']) assert.throws(() => parseEvent(raw));
});

test('debug logs omit audio payloads and resolved session credentials', () => {
  assert.equal(JSON.stringify(debugEvent({ type: 'session.created', session: { model: PREVIEW_MODEL, client_secret: { value: 'secret-value' } } })).includes('secret-value'), false);
  assert.notEqual(debugEvent({ type: 'response.output_audio.delta', delta: 'AAAA' }).delta, 'AAAA');
  assert.notEqual(debugEvent({ type: 'input_audio_buffer.append', audio: 'AAAA' }).audio, 'AAAA');
});

test('PCM conversion clamps samples and uses signed little-endian values', () => {
  const encoded = encodePcm(new Float32Array([-2, -1, 0, 1, 2]));
  assert.deepEqual([...Buffer.from(encoded, 'base64')], [0, 128, 0, 128, 0, 0, 255, 127, 255, 127]);
  assert.deepEqual([...decodePcm(encoded)], [-1, -1, 0, 32767 / 32768, 32767 / 32768]);
  assert.throws(() => decodePcm('AA=='), /odd byte/);
});

test('heard time excludes gaps, future segments, and samples not rendered', () => {
  const segments = [{ start: 1, length: 2400 }, { start: 2, length: 4800 }];
  assert.equal(heardMilliseconds(segments, 0.5, 24000), 0);
  assert.ok(Math.abs(heardMilliseconds(segments, 2.05, 24000) - 150) < 0.001);
  assert.ok(Math.abs(heardMilliseconds(segments, 5, 24000) - 300) < 0.001);
  assert.equal(truncate('item', 12.9).audio_end_ms, 12);
  assert.equal(truncate('item', -1).audio_end_ms, 0);
});

test('interrupt before first audio blocks late chunks and truncates zero only on completion', () => {
  const sent: WireEvent[] = [], marked: unknown[] = [];
  const state = new Interruptions(e => sent.push(e), item => marked.push(item));
  state.responseStarted('r1'); state.stop([]);
  assert.equal(state.observe('a1', 'r1'), false);
  assert.equal(sent.length, 0);
  state.responseDone('r1');
  assert.deepEqual(sent, [truncate('a1', 0)]);
  state.responseDone('r1'); assert.equal(sent.length, 1);
  assert.equal(marked.length, 1);
  state.responseStarted('r2'); assert.equal(state.observe('a2', 'r2'), true);
});

test('interrupt queued playback after response.done preserves per-item heard position', () => {
  const sent: WireEvent[] = [];
  const state = new Interruptions(e => sent.push(e), () => {});
  state.responseStarted('r1'); state.observe('a1', 'r1'); state.responseDone('r1');
  state.responseStarted('r2'); state.observe('a2', 'r2');
  state.stop([{ id: 'a1', index: 0, heardMs: 157.9 }, { id: 'a2', index: 0, heardMs: 0 }]);
  assert.deepEqual(sent, [truncate('a1', 157.9)]);
  state.responseDone('r2'); assert.deepEqual(sent[1], truncate('a2', 0));
  assert.equal(sent.some(e => e.type === 'response.cancel'), false);
});

test('a fully heard completed preamble is not truncated to zero during its response', () => {
  const sent: WireEvent[] = [];
  const state = new Interruptions(e => sent.push(e), () => {});
  state.responseStarted('r'); state.observe('preamble', 'r'); state.audioDone('preamble');
  state.stop([]); state.responseDone('r');
  assert.deepEqual(sent, []);
});
