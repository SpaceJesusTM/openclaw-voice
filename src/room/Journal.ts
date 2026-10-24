/**
 * A shared notebook for the operator: what each participant said and thought.
 * Write-only from the participants' side; nobody in the room reads it.
 */
export interface Entry {
  at: number;
  who: string;
  /** "work": a deliverable an agent produced after leaving to do its job. */
  kind: "said" | "thought" | "note" | "work";
  text: string;
  /** Structured detail, e.g. a thought's { decision, thought, detail, context }. */
  data?: Record<string, unknown>;
}

export class Journal {
  readonly entries: Entry[] = [];

  constructor(
    private clock: () => number,
    private log: (line: string) => void,
    /** Called with every entry as it is added (e.g. to stream it to a web page). */
    private onEntry?: (entry: Entry) => void,
  ) {}

  /** `at` defaults to now; pass when something started earlier (a turn logged when it ends). */
  add(who: string, kind: Entry["kind"], text: string, at = this.clock(), data?: Entry["data"]): void {
    const entry: Entry = { at: Math.round(at), who, kind, text, ...(data ? { data } : {}) };
    this.entries.push(entry);
    this.onEntry?.(entry);
    this.log(kind === "said" ? `${who}: ${text}` : kind === "thought" ? `   (${who} ${text})` : `-- ${who}: ${text}`);
  }

  markdown(title: string): string {
    const out = [`# ${title}`, ""];
    for (const e of [...this.entries].sort((a, b) => a.at - b.at)) {
      const at = `${(e.at / 1000).toFixed(1)} s`;
      if (e.kind === "said") out.push("", `**${e.who}** (${at}): ${e.text}`);
      else if (e.kind === "thought") out.push(`> ${at} · ${e.who} ${e.text}`);
      else if (e.kind === "work") out.push("", `#### ${e.who}'s work (${at}): ${String(e.data?.task ?? "")}`, "", e.text, "");
      else out.push(`*${at} · ${e.who}: ${e.text}*`);
    }
    return out.join("\n") + "\n";
  }
}

export const listNames = (names: string[]) =>
  names.length <= 1 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
