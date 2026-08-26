"""The model each harness runs in the e2e suite, pinned, in one place.

These tests prove a harness can join the bus. That is a question about the
harness, not about the model, so the cheapest one that reliably calls a tool
is the right one. Left to their own defaults the five agents each reached for
their vendor's frontier model, which is a bill nobody sees until it arrives.

omp and pi take a `provider/id` and will run anything they are authed for, so
they get the cheapest model on offer anywhere. The other three are locked to
their own vendor, so cheapest-of-that-vendor is as far as it goes.

Override one with the matching variable below. An empty value counts as unset,
deliberately: compose passes `${VAR:-}` for a variable the shell has not set,
and `environ.get(name, default)` takes that empty string as an answer -- which
is how omp spent every containerised run being handed `--model ""`.
"""

from __future__ import annotations

import os

CLAUDE_MODEL = os.environ.get("AGENT_BUS_CLAUDE_MODEL") or "claude-haiku-4-5-20251001"
CODEX_MODEL = os.environ.get("AGENT_BUS_CODEX_MODEL") or "gpt-5.4-mini"
GROK_MODEL = os.environ.get("AGENT_BUS_GROK_MODEL") or "grok-4.6"
OMP_MODEL = os.environ.get("AGENT_BUS_OMP_MODEL") or "anthropic/claude-haiku-4-5"
PI_MODEL = os.environ.get("AGENT_BUS_PI_MODEL") or "anthropic/claude-haiku-4-5"
