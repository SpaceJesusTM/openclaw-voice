import type { AgentName } from "../src/agent/selectorAgents";

/**
 * Background tasks, faked with timers for now: the point is the alert
 * plumbing, not the work. A task runs for a while, then either finishes or
 * stops to ask for approval; approved, it finishes a little later, denied,
 * it stops. Every change becomes an alert for the room to announce.
 */
export interface Task {
  id: number;
  owner: AgentName;
  title: string;
  status: "running" | "needs_approval" | "done" | "denied";
  /** What it's asking approval for. */
  question?: string;
  result?: string;
  startedAt: number;
}

export interface TaskAlert {
  task: Task;
  kind: "approval" | "done" | "denied";
}

/** SELECTOR_TASK_SCALE shortens (or stretches) every timing, for tests. */
const SCALE = Number(process.env.SELECTOR_TASK_SCALE) > 0 ? Number(process.env.SELECTOR_TASK_SCALE) : 1;
/** How long a task works before it finishes or asks for approval. */
const WORK_MS = 25_000 * SCALE;
/** How long it takes to finish after an approval. */
const AFTER_APPROVAL_MS = 15_000 * SCALE;

export class TaskBoard {
  private tasks: Task[] = [];
  private timers = new Map<number, NodeJS.Timeout>();
  private nextId = 1;

  constructor(
    /** A task needs attention: it finished, needs approval, or was denied. */
    private onAlert: (a: TaskAlert) => void,
    /** Anything changed; for the page's Tasks panel. */
    private onChange: (tasks: Task[]) => void,
  ) {}

  list(): Task[] {
    return this.tasks.map((t) => ({ ...t }));
  }

  get(id: number): Task | undefined {
    return this.tasks.find((t) => t.id === id);
  }

  /** Tasks waiting on the user. */
  pending(): Task[] {
    return this.tasks.filter((t) => t.status === "needs_approval");
  }

  /**
   * Start a task, unless the same owner already has one with the same title
   * running or waiting: agents asked the same thing (planner, then coder once
   * swapped in) both called start_task for it. Then that task is returned.
   */
  start(owner: AgentName, title: string): { task: Task; created: boolean } {
    const clean = title.trim().slice(0, 120) || "a task";
    const same = this.tasks.find((t) => t.owner === owner && norm(t.title) === norm(clean) && (t.status === "running" || t.status === "needs_approval"));
    if (same) return { task: same, created: false };
    const task: Task = { id: this.nextId++, owner, title: clean, status: "running", startedAt: Date.now() };
    this.tasks.push(task);
    // Every other task asks for approval, so both paths come up in a demo.
    const asks = task.id % 2 === 1;
    this.after(task, WORK_MS, () => {
      if (asks) {
        task.status = "needs_approval";
        task.question = `It's ready to apply the changes for "${task.title}". OK to go ahead?`;
        this.emit(task, "approval");
      } else this.finish(task);
    });
    this.onChange(this.list());
    return { task, created: true };
  }

  /** The user's answer to a pending approval. False if there's nothing to answer. */
  answer(id: number, approve: boolean): boolean {
    const task = this.get(id);
    if (!task || task.status !== "needs_approval") return false;
    if (approve) {
      task.status = "running";
      task.question = undefined;
      this.after(task, AFTER_APPROVAL_MS, () => this.finish(task));
      this.onChange(this.list());
    } else {
      task.status = "denied";
      task.question = undefined;
      this.emit(task, "denied");
    }
    return true;
  }

  close(): void {
    for (const t of this.timers.values()) clearTimeout(t);
    this.timers.clear();
  }

  private finish(task: Task): void {
    task.status = "done";
    task.result = `Finished "${task.title}" (simulated: no real work was done).`;
    this.emit(task, "done");
  }

  private emit(task: Task, kind: TaskAlert["kind"]): void {
    this.onChange(this.list());
    this.onAlert({ task: { ...task }, kind });
  }

  private after(task: Task, ms: number, fn: () => void): void {
    clearTimeout(this.timers.get(task.id));
    this.timers.set(
      task.id,
      setTimeout(() => {
        this.timers.delete(task.id);
        fn();
      }, ms),
    );
  }
}

const norm = (title: string) => title.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
