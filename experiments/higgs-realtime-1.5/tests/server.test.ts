import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer, get } from 'node:http';
import type { AddressInfo } from 'node:net';
import { mintSecret, readConfig } from '../server/config';
import { apiHandler } from '../server/http';
import { PREVIEW_MODEL } from '../src/protocol';

test('server defaults to preview and rejects legacy model or unsafe API host', () => {
  assert.equal(readConfig({}).model, PREVIEW_MODEL);
  assert.throws(() => readConfig({ BOSON_REALTIME_MODEL: 'higgs-realtime' }), /No fallback/);
  for (const base of ['http://api.boson.ai', 'https://user:pass@api.boson.ai', 'https://api.boson.ai/path']) {
    assert.throws(() => readConfig({ BOSON_BASE_URL: base }), /HTTPS origin/);
  }
});

test('secret minting uses bearer key server-side, documented expiry, and forwards only short-lived value', async () => {
  const config = readConfig({ BOSON_API_KEY: 'private-test-key' });
  const fake = (async (url, init) => {
    assert.equal(url, 'https://api.boson.ai/v1/realtime/client_secrets');
    assert.equal(new Headers(init?.headers).get('Authorization'), 'Bearer private-test-key');
    assert.deepEqual(JSON.parse(String(init?.body)), { expires_after: { seconds: 600 } });
    return Response.json({ value: 'bai-eph-test', expires_at: Date.now() / 1000 + 600,
      session: { client_secret: 'private-test-key' } });
  }) as typeof fetch;
  const result = await mintSecret(config, fake);
  assert.deepEqual(Object.keys(result), ['value', 'expires_at']);
  assert.equal(JSON.stringify(result).includes('private-test-key'), false);
});

test('secret failures never expose upstream bodies and reject root key/expired secrets', async () => {
  await assert.rejects(mintSecret(readConfig({})), /BOSON_API_KEY is missing/);
  const config = readConfig({ BOSON_API_KEY: 'private-test-key' });
  const upstreamFailure = (async () => new Response('private-test-key', { status: 401 })) as typeof fetch;
  await assert.rejects(mintSecret(config, upstreamFailure), e => {
    assert.match(String(e), /HTTP 401/); assert.doesNotMatch(String(e), /private-test-key/); return true;
  });
  for (const value of [{ value: config.apiKey, expires_at: Date.now() / 1000 + 600 }, { value: 'expired', expires_at: 1 }, {}]) {
    await assert.rejects(mintSecret(config, (async () => Response.json(value)) as typeof fetch), /invalid or expired/);
  }
});

test('HTTP API preserves secret boundary, same-origin protection, and useful missing-key errors', async () => {
  const handler = apiHandler(readConfig({}));
  const server = createServer(async (req, res) => { if (!await handler(req, res)) res.writeHead(404).end(); });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  try {
    const config = await fetch(`${base}/api/config`).then(r => r.json()) as Record<string, unknown>;
    assert.equal(config.model, PREVIEW_MODEL); assert.equal('apiKey' in config, false);
    const missing = await fetch(`${base}/api/token`, { method: 'POST' });
    assert.equal(missing.status, 503); assert.match(await missing.text(), /BOSON_API_KEY is missing/);
    assert.equal(missing.headers.get('cache-control'), 'no-store');
    const foreign = await fetch(`${base}/api/token`, { method: 'POST', headers: { Origin: 'https://untrusted.example' } });
    assert.equal(foreign.status, 403);
    assert.equal((await fetch(`${base}/api/token`)).status, 404);
    const rebound = await new Promise<number | undefined>((resolve, reject) => {
      get(`${base}/api/config`, { headers: { Host: 'evil.example' } }, res => {
        res.resume(); resolve(res.statusCode);
      }).on('error', reject);
    });
    assert.equal(rebound, 403);
  } finally { await new Promise<void>(resolve => server.close(() => resolve())); }
});
