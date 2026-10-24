---
name: astra-coding
description: "Create, modify, validate, and preview local coding projects in projects/ through bin/astra-project."
user-invocable: false
---

# Astra Coding & Website Workflow

Use this skill for all coding, website creation, file modification, build/test validation, and preview requests.

## Creating a Website or Project

1. Choose a clean slug (or reuse the active project slug when modifying the current project).
2. Run:
   ```sh
   ./bin/astra-project init <slug> --name "<Name>" --description "<Description>"
   ```
3. Write the project files under `projects/<slug>/` (e.g., `projects/<slug>/index.html`, `projects/<slug>/styles.css`).
   - Ensure `index.html` has an explicit `<title>...</title>` and `<h1>...</h1>`.
4. Run validation and start the local preview server:
   ```sh
   ./bin/astra-project check <slug> --start-preview --action created --files "index.html,styles.css" --summary "Created <Name> website" --json
   ```
5. In your spoken summary, mention what you built and that the local preview is running on localhost port 18950.
6. In `CLAWVOICE_RECEIPT`, include the `receipt_change` object from `./bin/astra-project check`.

## Modifying an Existing Website or Project

1. Check the active project if needed:
   ```sh
   ./bin/astra-project status --json
   ```
2. Read and edit the real file(s) in `projects/<slug>/` (e.g. `projects/<slug>/index.html`).
   - If asked to change the **title**, update `<title>New Title</title>` in `index.html`.
   - If asked to change the **heading**, update `<h1>New Heading</h1>` in `index.html`.
3. Validate the modification and verify the live preview:
   ```sh
   ./bin/astra-project check <slug> --start-preview --action updated --files "index.html" --summary "Updated <what changed>" --json
   ```
4. Include the returned `receipt_change` in your `CLAWVOICE_RECEIPT` line.

