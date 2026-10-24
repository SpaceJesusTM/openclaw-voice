import { CHUNK_SAMPLES, msOf } from "../audio/pcm";

/** Anything that can be played into the room: a generated Utterance, or a live mic. */
export interface AudioSource {
  /** No more audio will come; a source that is done and drained ends its stream. */
  readonly done: boolean;
  /** Samples ready to play now. */
  readonly available: number;
  read(n: number): Int16Array;
}

/** What one participant hears in one 100 ms tick. */
export interface Tick {
  /** Milliseconds since the bus started. */
  at: number;
  /** Everyone else's audio, mixed. What this participant's ears receive. */
  others: Int16Array;
  /** Everyone's audio, this participant included. */
  all: Int16Array;
  /** Who was audible this tick. Perception, like seeing whose lips move. */
  active: string[];
}

export interface BusListener {
  readonly name: string;
  onTick(tick: Tick): void;
}

/** One participant's speech being played into the room. */
export class Stream {
  played = 0;
  stopped = false;
  /** performance.now() of the first tick that played this stream, for latency measurement. */
  firstPlayedAt: number | null = null;
  readonly ended: Promise<void>;
  private resolveEnded!: () => void;

  constructor(
    readonly name: string,
    readonly source: AudioSource,
  ) {
    this.ended = new Promise((r) => (this.resolveEnded = r));
  }

  get playedMs(): number {
    return msOf(this.played);
  }

  /** The speaker stops talking. Takes effect on the next tick. */
  stop(): void {
    this.stopped = true;
  }

  end(): void {
    this.resolveEnded();
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * The shared air, and nothing more. Higgs sessions cannot hear each other, so
 * something has to carry audio between them; this does, and decides nothing.
 *
 * Every 100 ms it takes the next chunk of every stream that is playing (speech
 * arrives ~10x faster than real time, so streams are buffers drained at real
 * time), mixes them, and gives each listener the mix with its own voice left
 * out. Anyone may talk at any time, including over each other.
 */
export class AudioBus {
  private listeners: BusListener[] = [];
  private streams = new Set<Stream>();
  private recording: Int16Array[] = [];
  private running = false;
  private stopped!: Promise<void>;
  private t0 = 0;

  join(listener: BusListener): void {
    this.listeners.push(listener);
  }

  /** Start playing `source` as `name`. Resolves `ended` when drained or stopped. */
  play(name: string, source: AudioSource): Stream {
    const s = new Stream(name, source);
    this.streams.add(s);
    return s;
  }

  elapsedMs(): number {
    return this.t0 ? performance.now() - this.t0 : 0;
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.t0 = performance.now();
    this.stopped = this.loop();
  }

  /** Stop the clock; anything still playing ends where it is. */
  async stop(): Promise<void> {
    this.running = false;
    await this.stopped;
    for (const s of this.streams) s.end();
    this.streams.clear();
  }

  /** The room as a listener heard it, silences included. */
  mixdown(): Int16Array {
    const out = new Int16Array(this.recording.length * CHUNK_SAMPLES);
    this.recording.forEach((c, i) => out.set(c, i * CHUNK_SAMPLES));
    return out;
  }

  private async loop(): Promise<void> {
    for (let tick = 1; this.running; tick++) {
      const chunks = new Map<string, Int16Array>();
      for (const s of [...this.streams]) {
        const u = s.source;
        if (s.stopped || (u.done && u.available === 0)) {
          this.streams.delete(s);
          s.end();
        } else if (u.available >= CHUNK_SAMPLES || (u.done && u.available > 0)) {
          const c = u.read(CHUNK_SAMPLES);
          s.played += c.length;
          s.firstPlayedAt ??= performance.now();
          // One participant can have two streams at once (a mic and a typed line).
          const prev = chunks.get(s.name);
          chunks.set(s.name, prev ? mix([prev, c]) : c);
        }
        // else: still being generated (or nobody at the mic); silent this tick.
      }

      const all = mix([...chunks.values()]);
      this.recording.push(all);
      const tickInfo = { at: Math.round(this.elapsedMs()), active: [...chunks.keys()], all };
      for (const l of this.listeners) {
        const others = chunks.has(l.name) ? mix([...chunks].filter(([n]) => n !== l.name).map(([, c]) => c)) : all;
        try {
          l.onTick({ ...tickInfo, others });
        } catch (e) {
          console.error(`bus: ${l.name} onTick threw`, e);
        }
      }
      await sleep(Math.max(0, this.t0 + tick * 100 - performance.now()));
    }
  }
}

function mix(chunks: Int16Array[]): Int16Array {
  const out = new Int16Array(CHUNK_SAMPLES);
  if (chunks.length === 1) {
    out.set(chunks[0].subarray(0, CHUNK_SAMPLES));
    return out;
  }
  for (let i = 0; i < CHUNK_SAMPLES; i++) {
    let v = 0;
    for (const c of chunks) v += c[i] ?? 0;
    out[i] = Math.max(-32768, Math.min(32767, v));
  }
  return out;
}
