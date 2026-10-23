// The graph is fixed at 24 kHz. The browser resamples the device at its input.
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frame = new Float32Array(2400);
    this.used = 0;
    this.port.onmessage = ({ data }) => {
      if (data === 'flush') {
        this.emit();
        this.port.postMessage('flushed');
      }
    };
  }
  emit() {
    if (!this.used) return;
    const frame = this.frame.slice(0, this.used);
    this.port.postMessage(frame.buffer, [frame.buffer]);
    this.used = 0;
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel) for (const sample of channel) {
      this.frame[this.used++] = sample;
      if (this.used === this.frame.length) this.emit();
    }
    return true;
  }
}
registerProcessor('higgs-capture', CaptureProcessor);
