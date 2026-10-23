/** Explicit little-endian PCM16 conversion, independent of host endianness. */
export function encodePcm(samples: Float32Array): string {
  const buffer = new Uint8Array(samples.length * 2);
  const view = new DataView(buffer.buffer);
  for (let i = 0; i < samples.length; i++) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
  }
  let binary = '';
  for (const byte of buffer) binary += String.fromCharCode(byte);
  return btoa(binary);
}
export function decodePcm(base64: string): Float32Array<ArrayBuffer> {
  const raw = atob(base64);
  if (raw.length % 2) throw new Error('PCM16 chunk has an odd byte count');
  const bytes = Uint8Array.from(raw, c => c.charCodeAt(0));
  const view = new DataView(bytes.buffer);
  const samples = new Float32Array(raw.length / 2);
  for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
  return samples;
}

export interface Segment { start: number; length: number }
export function heardMilliseconds(segments: Segment[], time: number, rate: number): number {
  return segments.reduce((sum, s) => sum + Math.max(0, Math.min(s.length / rate, time - s.start)), 0) * 1000;
}
