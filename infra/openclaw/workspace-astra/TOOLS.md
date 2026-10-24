# Workspace Tools

## Project & Preview Helper (`./bin/astra-project`)

Always use `./bin/astra-project` in `/home/node/.openclaw/workspace` to initialize projects, run validation checks, manage the local preview server on port `18950`, and record updates for the ClawVoice hot-context cache and receipt verifier.

### Commands

```sh
# Inspect active project, recent projects, or latest update
./bin/astra-project status --json
./bin/astra-project recent --json
./bin/astra-project latest-update --json

# Initialize a new project directory under projects/<slug>/
./bin/astra-project init coffee-shop --name "Moonlight Coffee" --description "Fictional coffee shop website" --json

# Validate project files, optionally run a build/test command, start/verify local preview on port 18950, and record the update
./bin/astra-project check coffee-shop --start-preview --action created --files "index.html,styles.css" --summary "Created coffee shop website" --json

# Validate an update to an existing project
./bin/astra-project check coffee-shop --start-preview --action updated --files "index.html" --summary "Changed website title to Moonlight Coffee" --json

# Manage the local preview server on http://127.0.0.1:18950
./bin/astra-project preview start coffee-shop --json
./bin/astra-project preview status --json
./bin/astra-project preview stop --json
```

### Important Notes
- `./bin/astra-project check --start-preview` starts the background HTTP server in a detached session with `Cache-Control: no-store` on port `18950` (exposed to the host at `http://127.0.0.1:18950`) and returns immediately once HTTP 200 is verified.
- Do not run foreground blocking servers (`python3 -m http.server` without daemonizing) directly in `exec`, as that will hang your command turn. Always use `./bin/astra-project check <slug> --start-preview` or `./bin/astra-project preview start <slug>`.

