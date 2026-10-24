/**
 * Probes for the agent selector demo (run 2026-10-24):
 *
 * 1. turn_detection cannot be toggled by session.update: the server answers
 *    "turn_detection cannot be changed after session start". So the agents
 *    run manual turns and the overseer alone runs server VAD.
 * 2. Server VAD on a text-mode overseer: does it emit speech_started /
 *    committed and auto-create a response that calls the tool?
 * 3. response.cancel on an audio session mid-reply.
 *
 *   npx tsx tools/probe/vadToggle.ts
 */
import "dotenv/config";
import { CHUNK_SAMPLES } from "../../src/audio/pcm";
import { loadApiKey, synthesize } from "../../src/audio/tts";
import { MODEL, responseCreate } from "../../src/realtime/events";
import { RealtimeSession } from "../../src/realtime/RealtimeSession";
import { buildSessionConfig } from "../../src/realtime/sessionConfig";

const apiKey = loadApiKey();
const t0 = Date.now();
const log = (s: string, m: string) => console.log(`${String(Date.now() - t0).padStart(6)} ${s}: ${m}`);
const VAD = { type: "server_vad" as const, threshold: 0.85, prefix_padding_ms: 300, silence_duration_ms: 500 };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function stream(s: RealtimeSession, samples: Int16Array, silenceMs: number): Promise<void> {
  const all = new Int16Array(samples.length + silenceMs * 24);
  all.set(samples);
  for (let i = 0; i < all.length; i += CHUNK_SAMPLES) {
    s.appendAudio(all.subarray(i, i + CHUNK_SAMPLES));
    await sleep(100);
  }
}

const watch = (s: RealtimeSession) =>
  s.on((e) => {
    if (e.type.includes("delta") || e.type.includes(".length")) return;
    let extra = "";
    if (e.type === "error") extra = JSON.stringify(e.error);
    if (e.type === "response.function_call_arguments.done") extra = `${e.name} ${e.arguments}`;
    if (e.type === "response.output_text.done") extra = JSON.stringify(e.text);
    if (e.type === "response.done") extra = JSON.stringify((e.response as { status?: string })?.status);
    if (e.type.startsWith("input_audio_buffer")) extra = JSON.stringify({ start: e.audio_start_ms, end: e.audio_end_ms, item: e.item_id });
    log(s.name, `${e.type} ${extra}`);
  });

const CALL_AGENT = {
  type: "function" as const,
  name: "call_agent",
  description: "Hand the conversation to planner, coder or jester.",
  parameters: {
    type: "object",
    properties: { agent: { type: "string", enum: ["planner", "coder", "jester"] }, reason: { type: "string" } },
    required: ["agent", "reason"],
  },
};

const speech = await synthesize(apiKey, "Okay, can I talk to the coder now? I have a bug in my Python script.", "nora");
const overseer = await RealtimeSession.connect(
  "overseer",
  apiKey,
  buildSessionConfig({
    instructions: "You silently route a voice conversation between planner, coder and jester. Planner is active. Call call_agent when someone else should take over; otherwise reply with an empty message.",
    modality: "text",
    turnDetection: VAD,
    tools: [CALL_AGENT],
    transcribe: true,
  }),
  log,
  "higgs-realtime",
);
log("step", `overseer turn_detection: ${JSON.stringify(overseer.resolved?.audio?.input?.turn_detection)}`);
watch(overseer);
log("step", "2. stream speech into the VAD overseer");
await stream(overseer, speech, 2000);
await sleep(4000);
await overseer.close();

log("step", "3. response.cancel on an audio session");
const voice = await RealtimeSession.connect("voice", apiKey, buildSessionConfig({ instructions: "You are chatty.", modality: "audio", voice: "marcus" }), log, MODEL);
watch(voice);
voice.send({ type: "conversation.item.create", item: { type: "message", role: "user", content: [{ type: "input_text", text: "Tell me a long story about a dragon." }] } });
voice.send(responseCreate());
await voice.waitFor((e) => e.type === "response.output_audio.delta", 10_000, "first audio");
await sleep(300);
voice.send({ type: "response.cancel" });
await sleep(3000);
log("step", `generating after cancel: ${voice.generating}`);
await voice.close();
process.exit(0);
