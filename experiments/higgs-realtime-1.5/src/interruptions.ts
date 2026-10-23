import { truncate, type WireEvent } from './protocol';
import type { InterruptedItem } from './audio';

/** Preserve per-item playback positions even when generation finishes ahead of playback. */
export class Interruptions {
  private activeResponses = new Set<string>();
  private blockedResponses = new Set<string>();
  private items = new Map<string, { responseId: string; index: number; done: boolean }>();
  private blockedItems = new Set<string>();
  private pending = new Map<string, InterruptedItem>();
  constructor(private send: (event: WireEvent) => void,
    private mark: (item: InterruptedItem) => void) {}

  responseStarted(id: string): void { this.activeResponses.add(id); }
  observe(id: string, responseId: string, index = 0): boolean {
    if (!this.items.has(id)) this.items.set(id, { responseId, index, done: false });
    if (this.blockedResponses.has(responseId) && !this.blockedItems.has(id)) {
      this.blockedItems.add(id);
      const item = { id, index, heardMs: 0 };
      this.pending.set(id, item); this.mark(item);
    }
    return !this.blockedItems.has(id);
  }
  stop(positions: InterruptedItem[]): void {
    for (const response of this.activeResponses) this.blockedResponses.add(response);
    const snapshots = new Map(positions.map(item => [item.id, item]));
    for (const [id, item] of this.items) {
      if (this.activeResponses.has(item.responseId) && !item.done && !snapshots.has(id)) {
        snapshots.set(id, { id, index: item.index, heardMs: 0 });
      }
    }
    for (const item of snapshots.values()) {
      if (this.blockedItems.has(item.id)) continue;
      this.blockedItems.add(item.id); this.pending.set(item.id, item); this.mark(item);
      if (this.items.get(item.id)?.done) this.flush(item.id);
    }
  }
  audioDone(id: string): void {
    const item = this.items.get(id);
    if (item) item.done = true;
    this.flush(id);
  }
  responseDone(id: string): void {
    this.activeResponses.delete(id);
    for (const [itemId, item] of this.items) if (item.responseId === id) this.audioDone(itemId);
  }
  private flush(id: string): void {
    const item = this.pending.get(id);
    if (!item) return;
    this.pending.delete(id);
    this.send(truncate(item.id, item.heardMs, item.index));
  }
}
