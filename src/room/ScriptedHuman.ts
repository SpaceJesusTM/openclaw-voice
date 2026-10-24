import type { AudioBus, BusListener, Stream, Tick } from "./AudioBus";
import type { Journal } from "./Journal";
import { Utterance } from "./Utterance";

export interface HumanLine {
  text: string;
  samples: Int16Array;
}

export interface HumanOptions {
  /** After someone answers, how much silence before saying the next line. */
  replyAfterMs: number;
  /** If nobody says anything, move on (or finish) after this much silence. */
  giveUpAfterMs: number;
}

/**
 * A stand-in for a person: speaks pre-voiced lines into the room, each once
 * the room has gone quiet. Like the agents, it decides for itself.
 */
export class ScriptedHuman implements BusListener {
  readonly done: Promise<void>;
  private resolveDone!: () => void;
  private lines: HumanLine[];
  private speaking: Stream | null = null;
  private silenceMs = 0;
  private answered = false;

  constructor(
    readonly name: string,
    private bus: AudioBus,
    lines: HumanLine[],
    private opts: HumanOptions,
    private journal: Journal,
  ) {
    this.lines = [...lines];
    this.done = new Promise((r) => (this.resolveDone = r));
  }

  start(): void {
    this.sayNext();
  }

  onTick(t: Tick): void {
    if (t.active.length) {
      this.silenceMs = 0;
      if (t.active.some((n) => n !== this.name)) this.answered = true;
      return;
    }
    if (this.speaking) return;
    this.silenceMs += 100;
    const ready = (this.answered && this.silenceMs >= this.opts.replyAfterMs) || this.silenceMs >= this.opts.giveUpAfterMs;
    if (!ready) return;
    if (this.lines.length) this.sayNext();
    else this.resolveDone();
  }

  private sayNext(): void {
    const line = this.lines.shift();
    if (!line) return;
    this.answered = false;
    this.silenceMs = 0;
    this.journal.add(this.name, "said", line.text);
    const s = this.bus.play(this.name, Utterance.fromSamples(this.name, line.samples, line.text));
    this.speaking = s;
    void s.ended.then(() => {
      this.speaking = null;
      this.silenceMs = 0;
    });
  }
}
