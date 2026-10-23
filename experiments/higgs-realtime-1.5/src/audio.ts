import { AUDIO_RATE } from './protocol';
import { decodePcm, encodePcm, heardMilliseconds, type Segment } from './pcm';

interface PlayingItem { id: string; index: number; segments: Segment[]; done: boolean }
export interface InterruptedItem { id: string; index: number; heardMs: number }

export class AudioEngine {
  private context: AudioContext | null = null;
  private sources = new Set<AudioBufferSourceNode>();
  private items = new Map<string, PlayingItem>();
  private nextTime = 0;
  private stream: MediaStream | null = null;
  private capture: AudioWorkletNode | null = null;
  private input: MediaStreamAudioSourceNode | null = null;
  private muted: GainNode | null = null;
  private generation = 0;
  private workletLoaded = false;
  private stopping: Promise<void> | null = null;
  onLevel?: (rms: number) => void;
  onChunk?: (base64: string) => void;
  onMicEnded?: () => void;
  get micActive(): boolean { return Boolean(this.capture); }
  get queuedMs(): number { return this.context ? Math.max(0, this.nextTime - this.context.currentTime) * 1000 : 0; }

  async resume(): Promise<void> {
    this.context ??= new AudioContext({ sampleRate: AUDIO_RATE });
    if (this.context.sampleRate !== AUDIO_RATE) throw new Error('Browser could not open 24 kHz audio. Try Chrome or another output device.');
    await this.context.resume();
  }

  enqueue(id: string, index: number, base64: string): void {
    const ctx = this.context;
    if (!ctx || ctx.state !== 'running') throw new Error('Audio output is suspended. Reconnect using the Connect button.');
    if (this.queuedMs > 60_000) throw new Error('Playback queue exceeded 60 seconds; reconnect and request shorter replies.');
    const samples = decodePcm(base64);
    if (!samples.length) return;
    // Retire completely heard items; retain per-item timing across response boundaries.
    for (const [key, item] of this.items) {
      if (item.done && item.segments.every(s => s.start + s.length / AUDIO_RATE <= ctx.currentTime - ctx.baseLatency)) this.items.delete(key);
    }
    const key = `${id}:${index}`;
    let item = this.items.get(key);
    if (!item) { item = { id, index, segments: [], done: false }; this.items.set(key, item); }
    const buffer = ctx.createBuffer(1, samples.length, AUDIO_RATE);
    buffer.copyToChannel(samples, 0);
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);
    const start = Math.max(ctx.currentTime + 0.015, this.nextTime);
    item.segments.push({ start, length: samples.length });
    this.nextTime = start + samples.length / AUDIO_RATE;
    this.sources.add(source);
    source.onended = () => { this.sources.delete(source); source.disconnect(); };
    source.start(start);
  }

  markDone(id: string, index: number): void {
    const item = this.items.get(`${id}:${index}`);
    if (item) item.done = true;
  }

  interrupt(): InterruptedItem[] {
    const ctx = this.context;
    // A conservative estimate of rendered audio reaching the device, including reported latency.
    const heardTime = ctx ? Math.max(0, ctx.currentTime - ctx.baseLatency - (ctx.outputLatency || 0)) : 0;
    const interrupted = [...this.items.values()].filter(item => !item.done || item.segments.some(s => s.start + s.length / AUDIO_RATE > heardTime));
    for (const source of this.sources) { source.stop(); source.disconnect(); }
    this.sources.clear(); this.items.clear(); this.nextTime = 0;
    return interrupted.map(item => ({ id: item.id, index: item.index,
      heardMs: heardMilliseconds(item.segments, heardTime, AUDIO_RATE) }));
  }

  async startMic(): Promise<void> {
    if (this.capture) return;
    if (this.stopping) await this.stopping;
    const generation = ++this.generation;
    let stream: MediaStream | null = null;
    try {
      await this.resume();
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('Microphone requires localhost/HTTPS and a supported browser.');
      stream = await navigator.mediaDevices.getUserMedia({ audio: {
        echoCancellation: true, noiseSuppression: false, autoGainControl: true, channelCount: 1,
      } });
      if (generation !== this.generation) { stream.getTracks().forEach(t => t.stop()); return; }
      const ctx = this.context!;
      if (!this.workletLoaded) { await ctx.audioWorklet.addModule('/capture-worklet.js'); this.workletLoaded = true; }
      if (generation !== this.generation) { stream.getTracks().forEach(t => t.stop()); return; }
      this.stream = stream;
      this.input = ctx.createMediaStreamSource(stream);
      this.capture = new AudioWorkletNode(ctx, 'higgs-capture');
      this.muted = ctx.createGain(); this.muted.gain.value = 0;
      this.capture.port.onmessage = ({ data }: MessageEvent<ArrayBuffer | string>) => {
        if (typeof data === 'string') return;
        const samples = new Float32Array(data);
        this.onLevel?.(Math.sqrt(samples.reduce((sum, s) => sum + s * s, 0) / samples.length));
        this.onChunk?.(encodePcm(samples));
      };
      this.input.connect(this.capture).connect(this.muted).connect(ctx.destination);
      stream.getTracks().forEach(track => { track.onended = () => this.onMicEnded?.(); });
    } catch (error) {
      stream?.getTracks().forEach(t => t.stop());
      await this.stopMic();
      throw error;
    }
  }

  async stopMic(): Promise<void> {
    this.generation++;
    if (this.stopping) return this.stopping;
    const capture = this.capture;
    this.stopping = (async () => {
      if (capture && this.context?.state === 'running') {
        await new Promise<void>(resolve => {
          const listener = ({ data }: MessageEvent) => { if (data === 'flushed') done(); };
          const timer = setTimeout(() => done(), 250);
          const done = () => { clearTimeout(timer); capture.port.removeEventListener('message', listener); resolve(); };
          capture.port.addEventListener('message', listener);
          capture.port.postMessage('flush');
        });
      }
      this.stream?.getTracks().forEach(t => { t.onended = null; t.stop(); });
      this.capture?.disconnect(); this.input?.disconnect(); this.muted?.disconnect();
      this.capture?.port.close();
      this.stream = null; this.capture = null; this.input = null; this.muted = null;
      this.onLevel?.(0);
    })();
    try { await this.stopping; } finally { this.stopping = null; }
  }

  async close(): Promise<void> {
    this.interrupt(); await this.stopMic();
    await this.context?.close(); this.context = null; this.workletLoaded = false;
  }
}
