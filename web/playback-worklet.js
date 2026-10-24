// Room playback: a ring buffer drained at exactly 24 kHz. The server sends one
// 100 ms chunk per tick at real time; a short prime absorbs network jitter.
const RATE = 24000;
const PRIME = RATE * 0.1;

class Playback extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(RATE * 10);
    this.w = 0;
    this.r = 0;
    this.count = 0;
    this.playing = false;
    this.port.onmessage = (e) => {
      const d = e.data;
      // "clear": drop everything buffered, e.g. when the speaker is cut off.
      if (d === "clear") {
        this.r = this.w;
        this.count = 0;
        this.playing = false;
        return;
      }
      for (let i = 0; i < d.length; i++) {
        this.buf[this.w] = d[i];
        this.w = (this.w + 1) % this.buf.length;
      }
      this.count = Math.min(this.buf.length, this.count + d.length);
    };
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!this.playing && this.count >= PRIME) this.playing = true;
    for (let i = 0; i < out.length; i++) {
      if (this.playing && this.count > 0) {
        out[i] = this.buf[this.r];
        this.r = (this.r + 1) % this.buf.length;
        this.count--;
      } else {
        out[i] = 0;
      }
    }
    if (this.count === 0) this.playing = false;
    for (let c = 1; c < outputs[0].length; c++) outputs[0][c].set(out);
    return true;
  }
}
registerProcessor("playback", Playback);
