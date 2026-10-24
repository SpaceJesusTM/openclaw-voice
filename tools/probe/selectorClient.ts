/**
 * Talks to the agent selector demo the way the page does, with TTS lines as
 * the mic: a stream of 100 ms chunks, silence between lines. Prints what the
 * page would show. The web server must be running.
 *
 *   npx tsx tools/probe/selectorClient.ts [port]
 */
import "dotenv/config";
import WebSocket from "ws";
import { CHUNK_SAMPLES } from "../../src/audio/pcm";
import { loadApiKey, synthesize } from "../../src/audio/tts";

const port = Number(process.argv[2] ?? 3000);
const LINES = [
  "Hey, can I talk to the coder? My Python loop prints the same number every time.",
  "Got it. And what should I check first?",
  "Okay, that makes sense. Now tell me a joke about that bug.",
  "Ha, nice one. Honestly my day has been pretty long.",
  "Ha. Coder, quick one: what did I say my loop was doing?",
];

const apiKey = loadApiKey();
const speech = await Promise.all(LINES.map((l) => synthesize(apiKey, l, "nora")));
const t0 = Date.now();
const say = (m: string) => console.log(`${((Date.now() - t0) / 1000).toFixed(1).padStart(5)}s ${m}`);

const ws = new WebSocket(`ws://localhost:${port}/selector`);
let speaking: string | null = null;
let ready = false;
let saids = 0;
let lastQuiet = Date.now();
let mic: Int16Array = new Int16Array(0);
ws.on("open", () => ws.send(JSON.stringify({ type: "start" })));
ws.on("message", (data, binary) => {
  if (binary) return;
  const m = JSON.parse(String(data));
  if (m.type === "ready") ready = true;
  if (m.type === "said" && !m.cut) saids++;
  if (m.type === "speaking") {
    speaking = m.name;
    if (!m.name) lastQuiet = Date.now();
  }
  if (["active", "user", "said", "overseer", "error"].includes(m.type)) say(JSON.stringify(m));
});

// The mic: always streaming, like the page.
const timer = setInterval(() => {
  if (!ready || ws.readyState !== WebSocket.OPEN) return;
  const chunk = mic.length ? mic.subarray(0, CHUNK_SAMPLES) : new Int16Array(CHUNK_SAMPLES);
  mic = mic.subarray(chunk.length);
  ws.send(Buffer.from(chunk.buffer, chunk.byteOffset, chunk.byteLength));
}, 100);

const until = async (pred: () => boolean, ms: number) => {
  const end = Date.now() + ms;
  while (!pred() && Date.now() < end) await new Promise((r) => setTimeout(r, 100));
};

await until(() => ready, 60_000);
for (const [i, pcm] of speech.entries()) {
  // Wait for the agent to finish and a beat of quiet, then speak.
  await new Promise((r) => setTimeout(r, 1500));
  await until(() => speaking === null && Date.now() - lastQuiet > 1200, 40_000);
  say(`>>> speaking line ${i + 1}: ${LINES[i]}`);
  const before = saids;
  mic = pcm;
  await until(() => mic.length === 0, 30_000);
  // Then wait for someone to answer it.
  await until(() => saids > before, 40_000);
}
await new Promise((r) => setTimeout(r, 2000));
clearInterval(timer);
ws.close();
process.exit(0);
