/**
 * Web room: serves web/ and runs one BrowserRoom per WebSocket on /room.
 * The API key stays here; the page never sees it. Binds to localhost only.
 *
 *   npm run web          then open http://localhost:3000
 */
// First import, so .env is loaded before any module reads process.env.
import "dotenv/config";
import { appendFileSync, mkdirSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, join, normalize, resolve, sep } from "node:path";
import { WebSocketServer } from "ws";
import { loadApiKey } from "../src/audio/tts";
import { MODEL } from "../src/realtime/events";
import { BrowserRoom } from "./BrowserRoom";

const PORT = Number(process.env.PORT ?? 3000);
const WEB_DIR = resolve("web");
const TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
};

const apiKey = loadApiKey();

// Everything rooms log also goes to a file, so a session can be diagnosed afterwards.
mkdirSync("captures", { recursive: true });
const logPath = `captures/web-${new Date().toISOString().replace(/[:.]/g, "-")}.log`;
const log = (line: string) => {
  const out = `${new Date().toISOString().slice(11, 23)} ${line}`;
  console.log(out);
  appendFileSync(logPath, out + "\n");
};

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", "http://localhost");
  const rel = url.pathname === "/" ? "index.html" : decodeURIComponent(url.pathname.slice(1));
  const file = normalize(join(WEB_DIR, rel));
  if (!file.startsWith(WEB_DIR + sep)) {
    res.writeHead(403).end();
    return;
  }
  try {
    const body = await readFile(file);
    res.writeHead(200, { "Content-Type": TYPES[extname(file)] ?? "application/octet-stream", "Cache-Control": "no-store" });
    res.end(body);
  } catch {
    res.writeHead(404).end("not found");
  }
});

const wss = new WebSocketServer({ server, path: "/room" });
wss.on("connection", (ws) => {
  const room = new BrowserRoom(ws, apiKey, (line) => log(`[room] ${line}`));
  log("[web] page connected");
  const fail = (e: unknown) => {
    log(`[web] error: ${e instanceof Error ? e.message : String(e)}`);
    room.send({ type: "error", text: e instanceof Error ? e.message : String(e) });
  };
  ws.on("message", (data, isBinary) => {
    if (isBinary) return room.mic(data as Buffer);
    let msg: { type?: string; [k: string]: unknown };
    try {
      msg = JSON.parse(String(data));
    } catch {
      return;
    }
    if (msg.type === "start") room.start(msg as never).catch(fail);
    else if (msg.type === "say") room.say(String(msg.text ?? "")).catch(fail);
    else if (msg.type === "diag") room.diag(msg);
  });
  ws.on("close", () => {
    log("[web] page disconnected");
    void room.close().catch(() => undefined);
  });
});

server.listen(PORT, "127.0.0.1", () => {
  log(`web room on http://localhost:${PORT}  (model ${MODEL}); logging to ${logPath}`);
});
