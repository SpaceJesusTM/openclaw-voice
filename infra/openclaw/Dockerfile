# Derived from SpaceJesusTM/tari-public (MIT); keep the OpenClaw compatibility pin deliberate.
FROM ghcr.io/openclaw/openclaw:2026.9.9
USER root
# python3 runs the workspace's bin/day-planner.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates python3     && rm -rf /var/lib/apt/lists/*
USER node
