/**
 * Which response events carry the response id? The agent selector matches
 * events to replies by it.  npx tsx tools/probe/responseIds.ts
 */
import "dotenv/config";
import { loadApiKey } from "../../src/audio/tts";
import { MODEL, responseCreate, textItem } from "../../src/realtime/events";
import { RealtimeSession } from "../../src/realtime/RealtimeSession";
import { buildSessionConfig } from "../../src/realtime/sessionConfig";

const s = await RealtimeSession.connect("voice", loadApiKey(), buildSessionConfig({ instructions: "Answer in one short sentence.", modality: "audio", voice: "marcus" }), () => undefined, MODEL);
const seen = new Set<string>();
s.on((e) => {
  if (seen.has(e.type)) return;
  seen.add(e.type);
  const r = e.response as { id?: string; status?: string } | undefined;
  console.log(e.type.padEnd(44), JSON.stringify({ response_id: e.response_id, "response.id": r?.id, item_id: e.item_id, status: r?.status }));
});
s.send(textItem("Say hello."));
const done = s.waitFor((e) => e.type === "response.done", 20_000, "done");
s.send(responseCreate());
await done;
await s.close();
process.exit(0);
