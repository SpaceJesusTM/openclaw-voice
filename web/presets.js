// Starting points for the setup screen. Each agent's `model` is "1.5", "1" or
// "hybrid" (1.5 voice, 1 mind); see docs/model-comparison.md for the tradeoff.
export const PRESETS = [
  {
    id: "planner-worker",
    label: "Planner + worker (they go off and do the job)",
    scene: "A quick call to plan your week. Once the agents know what they need, they leave to do their part.",
    agents: [
      {
        name: "Planner",
        voice: "nora",
        model: "hybrid",
        role: "a planning assistant who turns what you need into a schedule.",
        goal: "Find out your priorities and deadlines, then leave to write the week's plan.",
        facts: "",
      },
      {
        name: "Worker",
        voice: "jake",
        model: "hybrid",
        role: "an assistant who does tasks: drafting emails, notes and checklists.",
        goal: "Get a clear task from you, then leave to do it and report back.",
        facts: "",
      },
    ],
  },
  {
    id: "ship-or-slip",
    label: "Ship or slip (product vs engineering)",
    scene: "Two weeks before launch. The new checkout flow works, but its automated test suite isn't finished.",
    agents: [
      {
        name: "Maya",
        voice: "chloe",
        model: "hybrid",
        role: "the product lead.",
        goal: "Launch on the date with the smallest safe scope; slipping loses the partner deal.",
        facts: "a major partner's promotion is tied to the launch date.",
      },
      {
        name: "Theo",
        voice: "marcus",
        model: "hybrid",
        role: "the senior engineer who owns checkout.",
        goal: "Don't ship checkout without automated tests on the payment paths.",
        facts: "last quarter an untested checkout change double-charged 300 customers.",
      },
    ],
  },
  {
    id: "standup",
    label: "Standup with a scheduling conflict",
    scene: "A quick sync before the design review.",
    agents: [
      {
        name: "Alice",
        voice: "nora",
        model: "hybrid",
        role: "the project manager, who owns the schedule.",
        goal: "Make sure everyone has the right date and the demo is ready for it.",
        facts: "the design review is on Thursday at 3 pm, and launch is the Monday after.",
      },
      {
        name: "Bob",
        voice: "jake",
        model: "hybrid",
        role: "the lead engineer. You think the design review is on Friday, but you are not sure; Alice keeps the schedule.",
        goal: "Get the time you need to fix the last bug.",
        facts: "the demo build has one bug left, a login crash, that needs about one more day.",
      },
    ],
  },
  {
    id: "dinner",
    label: "Where to eat tonight (three friends)",
    scene: "Three friends and you are picking where to have dinner tonight. It's Monday.",
    agents: [
      { name: "Sam", voice: "oliver", model: "hybrid", role: "a friend who has been craving sushi all week.", goal: "Get everyone to sushi.", facts: "" },
      {
        name: "Priya",
        voice: "eleanor",
        model: "hybrid",
        role: "a vegetarian friend.",
        goal: "Somewhere with real vegetarian options, not just a side salad.",
        facts: "the Thai place on Main Street is closed on Mondays.",
      },
      { name: "Leo", voice: "jake", model: "1", role: "a friend saving money this month.", goal: "Keep it under twenty dollars a person.", facts: "" },
    ],
  },
  {
    id: "blank",
    label: "Blank",
    scene: "",
    agents: [
      { name: "Agent A", voice: "chloe", model: "hybrid", role: "", goal: "", facts: "" },
      { name: "Agent B", voice: "marcus", model: "hybrid", role: "", goal: "", facts: "" },
    ],
  },
];
