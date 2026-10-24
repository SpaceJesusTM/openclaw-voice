/**
 * Talks to the agent selector the way the page does, with TTS lines as the
 * mic: a stream of 100 ms chunks, silence between lines. Prints what the page
 * would show. The web server must be running.
 *
 *   npx tsx tools/probe/selectorClient.ts [port] [routing|tasks]
 *
 * "tasks" starts a background task, swaps agents, and waits for the agent
 * you're talking to to announce it. Start the server with a short
 * SELECTOR_TASK_SCALE (e.g. 0.3) so it doesn't take minutes.
 */
import "dotenv/config";
import WebSocket from "ws";
import { CHUNK_SAMPLES } from "../../src/audio/pcm";
import { loadApiKey, synthesize } from "../../src/audio/tts";

/** Say a line, or wait for a task to reach a status and then for its announcement. */
type Step = { say: string } | { await: "needs_approval" | "done" };

const SCENARIOS: Record<string, Step[]> = {
  routing: [
    { say: "Hey, can I talk to the coder? My Python loop prints the same number every time." },
    { say: "Got it. And what should I check first?" },
    { say: "Okay, that makes sense. Now tell me a joke about that bug." },
    { say: "Ha, nice one. Honestly my day has been pretty long." },
    { say: "Ha. Coder, quick one: what did I say my loop was doing?" },
  ],
  tasks: [
    { say: "Coder, can you code up a login page for me in the background?" },
    { say: "Thanks. Planner, let's plan the launch for next week." },
    { await: "needs_approval" },
    { say: "Yes, approve it, go ahead." },
    { await: "done" },
  ],
};

const port = Number(process.argv[2] ?? 3000);
const scenario = process.argv[3] ?? "routing";
const steps = SCENARIOS[scenario];
if (!steps) throw new Error(`unknown scenario ${scenario}; known: ${Object.keys(SCENARIOS).join(", ")}`);

const apiKey = loadApiKey();
const speech = new Map<string, Int16Array>();
for (const s of steps) if ("say" in s) speech.set(s.say, await synthesize(apiKey, s.say, "nora"));
const t0 = Date.now();
const say = (m: string) => console.log(`${((Date.now() - t0) / 1000).toFixed(1).padStart(5)}s ${m}`);

const ws = new WebSocket(`ws://localhost:${port}/selector`);
let speaking: string | null = null;
let ready = false;
let saids = 0;
let lastQuiet = Date.now();
let mic: Int16Array = new Int16Array(0);
let tasks: { id: number; status: string }[] = [];
let lastTasks = "";
/** Announcements started, and the replies (said) that finished after the latest one began. */
let announces = 0;
let saidSinceAnnounce = 0;
ws.on("open", () => ws.send(JSON.stringify({ type: "start" })));
ws.on("message", (data, binary) => {
  if (binary) return;
  const m = JSON.parse(String(data));
  if (m.type === "ready") ready = true;
  if (m.type === "said" && !m.cut) {
    saids++;
    saidSinceAnnounce++;
  }
  if (m.type === "announce") {
    announces++;
    saidSinceAnnounce = 0;
    say(`announce ${JSON.stringify(m)}`);
  }
  if (m.type === "speaking") {
    speaking = m.name;
    if (!m.name) lastQuiet = Date.now();
  }
  if (m.type === "tasks") {
    tasks = m.tasks;
    const summary = JSON.stringify(m.tasks.map((t: { id: number; owner: string; status: string }) => `#${t.id} ${t.owner} ${t.status}`));
    if (summary !== lastTasks) say(`tasks ${(lastTasks = summary)}`);
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
for (const step of steps) {
  if ("await" in step) {
    say(`>>> waiting for a task to be ${step.await}, then for its announcement to finish`);
    await until(() => tasks.some((t) => t.status === step.await), 45_000);
    const before = announces;
    // The announcement starts at the next natural gap; wait for it, then for it to be heard out.
    await until(() => announces > before, 30_000);
    await until(() => saidSinceAnnounce > 0 && speaking === null, 30_000);
    continue;
  }
  // Wait for the agent to finish and a beat of quiet, then speak.
  await new Promise((r) => setTimeout(r, 1500));
  await until(() => speaking === null && Date.now() - lastQuiet > 1200, 40_000);
  say(`>>> speaking: ${step.say}`);
  const before = saids;
  mic = speech.get(step.say)!;
  await until(() => mic.length === 0, 30_000);
  // Then wait for someone to answer it.
  await until(() => saids > before, 40_000);
}
await new Promise((r) => setTimeout(r, 2000));
clearInterval(timer);
ws.close();
process.exit(0);
