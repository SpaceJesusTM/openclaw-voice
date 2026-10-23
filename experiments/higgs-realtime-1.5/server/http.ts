import type { IncomingMessage, ServerResponse } from 'node:http';
import { mintSecret, type Config } from './config';

export function apiHandler(config: Config, fetcher: typeof fetch = fetch) {
  return async (req: IncomingMessage, res: ServerResponse): Promise<boolean> => {
    const host = req.headers.host ?? '';
    if (!/^(localhost|127\.0\.0\.1)(:\d+)?$/.test(host)) {
      res.writeHead(403).end('Localhost only'); return true;
    }
    const path = new URL(req.url ?? '/', `http://${host}`).pathname;
    if (!path.startsWith('/api/')) return false;
    const json = (status: number, data: unknown) => {
      res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff' });
      res.end(JSON.stringify(data));
    };
    if ((req.headers.origin && req.headers.origin !== `http://${host}`) || req.headers['sec-fetch-site'] === 'cross-site') {
      json(403, { error: 'Same-origin requests only' }); return true;
    }
    if (path === '/api/config' && req.method === 'GET') {
      json(200, { model: config.model, wsUrl: config.wsUrl, voice: config.voice, keyAvailable: Boolean(config.apiKey) });
    } else if (path === '/api/health' && req.method === 'GET') {
      json(200, { ok: true, keyAvailable: Boolean(config.apiKey) });
    } else if (path === '/api/token' && req.method === 'POST') {
      try { json(200, await mintSecret(config, fetcher)); }
      catch (error) {
        // Only our own safe errors are shown. Network exceptions can include request details.
        const message = error instanceof Error && /^(BOSON_API_KEY|Boson )/.test(error.message)
          ? error.message : 'Could not reach Boson to mint a client secret';
        json(config.apiKey ? 502 : 503, { error: message });
      }
    } else json(404, { error: 'Unknown API endpoint or HTTP method' });
    return true;
  };
}
