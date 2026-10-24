/**
 * What does Higgs send when a response is cancelled early: (a) right after
 * response.create, before response.created; (b) right after response.created,
 * before any audio? And can the session commit afterwards?
 *
 *   npx tsx tools/probe/cancelEarly.ts
 */
import "dotenv/config";
import { loadApiKey } from "../../src/audio/tts";
import { MODEL, responseCancel, responseCreate, textItem } from "../../src/realtime/events";
import { RealtimeSession } from "../../src/realtime/RealtimeSession";
import { buildSessionConfig } from "../../src/realtime/sessionConfig";

const apiKey = loadApiKey();
const t0 = Date.now();
const log = (s: string, m: string) => console.log(`${String(Date.now() - t0).padStart(6)} ${s}: ${m}`);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const s = await RealtimeSession.connect("voice", apiKey, buildSessionConfig({ instructions: "Answer briefly.", modality: "audio", voice: "marcus" }), log, MODEL);
let deltas = 0;
s.on((e) => {
  if (e.type === "response.output_audio.delta") return void deltas++;
  if (e.type.includes("delta") || e.type.includes(".length")) return;
  const extra = e.type === "response.done" ? JSON.stringify((e.response as { status?: string })?.status) : e.type === "error" ? JSON.stringify(e.error) : "";
  log("evt", `${e.type} ${extra} (audio deltas so far ${deltas})`);
});

for (const when of ["before created", "on created"] as const) {
  log("step", `cancel ${when}`);
  s.send(textItem("Tell me about the moon in three sentences."));
  if (when === "on created") {
    const created = s.waitFor((e) => e.type === "response.created", 10_000, "created");
    s.send(responseCreate());
    await created;
  } else s.send(responseCreate());
  s.send(responseCancel());
  await sleep(6000);
  log("step", `generating=${s.generating}; trying a commit`);
  s.appendAudio(new Int16Array(4800));
  const t = Date.now();
  const id = await s.commit().catch((e) => `failed: ${e.message}`);
  log("step", `commit -> ${id} after ${Date.now() - t} ms`);
}
await s.close();
process.exit(0);
