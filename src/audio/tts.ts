import { readFileSync } from "node:fs";
import { AUDIO_RATE } from "../realtime/events";
import { decodeWav } from "./pcm";

export const BOSON_BASE_URL = process.env.BOSON_BASE_URL ?? "https://api.boson.ai";

export function loadApiKey(): string {
  const inline = process.env.BOSON_API_KEY?.trim();
  if (inline) return inline;
  const keyFile = process.env.BOSON_API_KEY_FILE;
  if (keyFile) {
    const fromFile = readFileSync(keyFile, "utf8").trim();
    if (fromFile) return fromFile;
  }
  throw new Error("No API key. Copy .env.example to .env and set BOSON_API_KEY (or BOSON_API_KEY_FILE).");
}

/** Text-to-speech at 24 kHz PCM16, e.g. to give a scripted human a voice. */
export async function synthesize(apiKey: string, text: string, voice = "default"): Promise<Int16Array> {
  const res = await fetch(`${BOSON_BASE_URL}/v1/audio/speech`, {
    method: "POST",
    headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
    body: JSON.stringify({ model: "higgs-tts-3", input: text, voice, response_format: "wav" }),
  });
  if (!res.ok) throw new Error(`speech ${res.status}: ${await res.text()}`);
  const { samples, rate } = decodeWav(Buffer.from(await res.arrayBuffer()));
  if (rate !== AUDIO_RATE) throw new Error(`TTS returned ${rate} Hz, expected ${AUDIO_RATE} Hz`);
  return samples;
}
