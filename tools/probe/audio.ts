import { CHUNK_SAMPLES } from "../../src/audio/pcm";
import { BOSON_BASE_URL } from "../../src/audio/tts";
import { inputAudioAppend } from "../../src/realtime/events";
import type { ProbeSession } from "./session";

export { CHUNK_SAMPLES, decodeWav, msOf, writeWav } from "../../src/audio/pcm";
export { loadApiKey, synthesize } from "../../src/audio/tts";

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function bosonGet(apiKey: string, path: string): Promise<{ status: number; body: unknown }> {
  const res = await fetch(`${BOSON_BASE_URL}${path}`, { headers: { Authorization: `Bearer ${apiKey}` } });
  const text = await res.text();
  let body: unknown = text;
  try {
    body = JSON.parse(text);
  } catch {
    /* keep text */
  }
  return { status: res.status, body };
}

export function appendChunk(session: ProbeSession, chunk: Int16Array): void {
  const bytes = Buffer.from(chunk.buffer, chunk.byteOffset, chunk.length * 2);
  session.send(inputAudioAppend(bytes.toString("base64")));
}

/**
 * Stream samples in ~100 ms chunks. `paced` sleeps 100 ms per chunk, like a
 * live microphone; unpaced sends everything at once (fine with manual turns).
 */
export async function streamAudio(session: ProbeSession, samples: Int16Array, paced: boolean): Promise<void> {
  for (let i = 0; i < samples.length; i += CHUNK_SAMPLES) {
    appendChunk(session, samples.subarray(i, Math.min(i + CHUNK_SAMPLES, samples.length)));
    if (paced) await sleep(100);
  }
}
