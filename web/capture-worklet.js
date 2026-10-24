// Mic capture. The AudioContext runs at 24 kHz, so the browser has already
// resampled the mic to the rate Higgs expects; this just hands frames over.
class Capture extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("capture", Capture);
