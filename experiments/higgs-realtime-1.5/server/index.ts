import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, resolve, sep } from 'node:path';
import { loadLocalEnv, readConfig, root } from './config';
import { apiHandler } from './http';

loadLocalEnv();
const config = readConfig();
const production = process.argv.includes('--production');
const vite = production ? null : await (await import('vite')).createServer({
  root, server: { middlewareMode: true }, appType: 'spa',
});
const api = apiHandler(config);
const staticRoot = resolve(root, 'dist');
const mime: Record<string, string> = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' };
const server = createServer(async (req, res) => {
  try {
    if (await api(req, res)) return;
    if (vite) { vite.middlewares(req, res); return; }
    const pathname = decodeURIComponent(new URL(req.url ?? '/', 'http://localhost').pathname);
    const file = resolve(staticRoot, `.${pathname === '/' ? '/index.html' : pathname}`);
    if (!file.startsWith(staticRoot + sep) || pathname.split('/').some(p => p.startsWith('.'))) {
      res.writeHead(403).end('Forbidden'); return;
    }
    const data = await readFile(file);
    res.writeHead(200, { 'Content-Type': mime[extname(file)] ?? 'application/octet-stream', 'X-Content-Type-Options': 'nosniff' });
    res.end(data);
  } catch { res.writeHead(404).end('Not found'); }
});
const port = Number(process.env.PORT ?? 3000);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('PORT must be between 1 and 65535');
server.listen(port, '127.0.0.1', () => {
  console.log(`Higgs preview playground: http://127.0.0.1:${port}`);
  console.log(`Configured model: ${config.model}. API key: ${config.apiKey ? 'available' : 'missing (offline mode)'}.`);
});
for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.once(signal, () => { void vite?.close(); server.close(() => process.exit(0)); });
}
