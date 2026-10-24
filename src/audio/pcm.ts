import { readFileSync, writeFileSync } from "node:fs";
import { AUDIO_RATE } from "../realtime/events";

/** 100 ms at 24 kHz: the chunk size we stream and play in. */
export const CHUNK_SAMPLES = AUDIO_RATE / 10;

export const msOf = (samples: number) => Math.round((samples / AUDIO_RATE) * 1000);
export const samplesOf = (ms: number) => Math.round((ms / 1000) * AUDIO_RATE);

/** Samples in a base64 PCM16 chunk, without decoding it. */
export const b64Samples = (b64: string) => Math.round((b64.length * 0.75) / 2);

export function decodeB64Pcm(b64: string): Int16Array {
  const buf = Buffer.from(b64, "base64");
  // Copy so the samples are 2-byte aligned regardless of the Buffer's offset.
  const copy = new Uint8Array(buf.length - (buf.length % 2));
  copy.set(buf.subarray(0, copy.length));
  return new Int16Array(copy.buffer);
}

export function concat(chunks: Int16Array[]): Int16Array {
  const out = new Int16Array(chunks.reduce((n, c) => n + c.length, 0));
  let pos = 0;
  for (const c of chunks) {
    out.set(c, pos);
    pos += c.length;
  }
  return out;
}

/** Walk the RIFF chunks rather than assuming a 44-byte header. */
export function decodeWav(buf: Buffer): { samples: Int16Array; rate: number } {
  if (buf.toString("ascii", 0, 4) !== "RIFF") throw new Error("not a WAV file");
  let rate = AUDIO_RATE;
  let pos = 12;
  while (pos + 8 <= buf.length) {
    const id = buf.toString("ascii", pos, pos + 4);
    const size = buf.readUInt32LE(pos + 4);
    const body = pos + 8;
    if (id === "fmt ") {
      const channels = buf.readUInt16LE(body + 2);
      rate = buf.readUInt32LE(body + 4);
      const bits = buf.readUInt16LE(body + 14);
      if (bits !== 16 || channels !== 1) throw new Error(`need 16-bit mono, got ${bits}-bit ${channels}-channel`);
    } else if (id === "data") {
      const end = Math.min(body + size, buf.length);
      const copy = new Uint8Array(end - body - ((end - body) % 2));
      copy.set(buf.subarray(body, body + copy.length));
      return { samples: new Int16Array(copy.buffer), rate };
    }
    pos = body + size + (size % 2);
  }
  throw new Error("no data chunk in WAV");
}

export function readWav(path: string) {
  return decodeWav(readFileSync(path));
}

export function writeWav(path: string, samples: Int16Array, rate = AUDIO_RATE): void {
  const header = Buffer.alloc(44);
  const dataBytes = samples.length * 2;
  header.write("RIFF", 0, "ascii");
  header.writeUInt32LE(36 + dataBytes, 4);
  header.write("WAVE", 8, "ascii");
  header.write("fmt ", 12, "ascii");
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20); // PCM
  header.writeUInt16LE(1, 22); // mono
  header.writeUInt32LE(rate, 24);
  header.writeUInt32LE(rate * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36, "ascii");
  header.writeUInt32LE(dataBytes, 40);
  writeFileSync(path, Buffer.concat([header, Buffer.from(samples.buffer, samples.byteOffset, dataBytes)]));
}
