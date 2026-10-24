/**
 * What does response.output_audio_transcript.length's length_ms mean: where
 * the fragment ends in the reply's audio, or the fragment's own duration?
 *   npx tsx tools/probe/transcriptLength.ts
 */
import "dotenv/config";
import { b64Samples, msOf } from "../../src/audio/pcm";
import { loadApiKey } from "../../src/audio/tts";
import { MODEL, responseCreate, textItem } from "../../src/realtime/events";
import { RealtimeSession } from "../../src/realtime/RealtimeSession";
import { buildSessionConfig } from "../../src/realtime/sessionConfig";

const s = await RealtimeSession.connect("voice", loadApiKey(), buildSessionConfig({ instructions: "Answer in three sentences.", modality: "audio", voice: "marcus" }), () => undefined, MODEL);
let audioSamples = 0;
s.on((e) => {
  if (e.type === "response.output_audio.delta") audioSamples += b64Samples(String(e.delta ?? ""));
  if (e.type === "response.output_audio_transcript.length") console.log(`length_ms=${e.length_ms} audio so far=${msOf(audioSamples)} ms delta=${JSON.stringify(e.delta)}`);
});
s.send(textItem("Describe the moon."));
const done = s.waitFor((e) => e.type === "response.done", 30_000, "done");
s.send(responseCreate());
await done;
console.log(`total audio ${msOf(audioSamples)} ms`);
await s.close();
process.exit(0);
