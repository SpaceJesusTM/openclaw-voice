import { existsSync } from 'node:fs';
import { loadEnvFile } from 'node:process';
import { fileURLToPath } from 'node:url';
import { PREVIEW_MODEL, requirePreview, websocketUrl } from '../src/protocol';

export const root = fileURLToPath(new URL('../', import.meta.url));
export function loadLocalEnv(): void {
  const path = `${root}.env`;
  if (existsSync(path)) loadEnvFile(path);
}
export function readConfig(env: NodeJS.ProcessEnv = process.env) {
  const model = requirePreview(env.BOSON_REALTIME_MODEL ?? PREVIEW_MODEL);
  const base = new URL(env.BOSON_BASE_URL ?? 'https://api.boson.ai');
  if (base.protocol !== 'https:' || base.username || base.password || base.search || base.hash || base.pathname !== '/') {
    throw new Error('BOSON_BASE_URL must be an HTTPS origin');
  }
  return { model, wsUrl: websocketUrl(model), voice: env.BOSON_VOICE?.trim() || 'default',
    baseUrl: base.origin, apiKey: env.BOSON_API_KEY?.trim() || '' };
}
export type Config = ReturnType<typeof readConfig>;

export async function mintSecret(config: Config, fetcher: typeof fetch = fetch) {
  if (!config.apiKey) throw new Error('BOSON_API_KEY is missing. Create .env from .env.example and restart the server.');
  const response = await fetcher(`${config.baseUrl}/v1/realtime/client_secrets`, {
    method: 'POST', redirect: 'error', signal: AbortSignal.timeout(15_000),
    headers: { Authorization: `Bearer ${config.apiKey}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ expires_after: { seconds: 600 } }),
  });
  // Never return upstream bodies: an error or session object can carry credentials.
  if (!response.ok) throw new Error(`Boson client-secret minting failed (HTTP ${response.status}). Check key, billing, and API host.`);
  const data = await response.json() as { value?: unknown; expires_at?: unknown };
  if (typeof data.value !== 'string' || !data.value || data.value === config.apiKey ||
      typeof data.expires_at !== 'number' || data.expires_at <= Date.now() / 1000) {
    throw new Error('Boson returned an invalid or expired client-secret response');
  }
  return { value: data.value, expires_at: data.expires_at };
}
