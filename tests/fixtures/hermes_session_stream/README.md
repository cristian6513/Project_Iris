# Session stream recorded from the pinned Hermes

Raw `POST /api/sessions/{id}/chat/stream` responses from Hermes 0.21.4, the runtime of the `qa8` installers. They were captured on 2026-10-07 by `artifacts/f01/capture_sse.py`, which is not part of the repository.

- **Setup.** Two fake OpenAI-compatible providers on `127.0.0.1` and a throwaway `HERMES_HOME`. No real account or network was involved.
- **Fake providers.** `fake-a` serves `alpha-1` and `alpha-2`; `fake-b` serves `beta-1`.
- **Session id.** The recordings use `IRIS-fixture`. The tests substitute the requested session id.

| File | Request | What Hermes did |
|---|---|---|
| `locked_completed.sse` | `fake-b` / `beta-1`, `require_model_lock` | answered, `model_lock: confirmed` |
| `default_locked_completed.sse` | `alpha-1` only, `require_model_lock` | answered with the global provider, confirmed |
| `refused_model_failed.sse` | `fake-a` / `alpha-9` (the provider returns 404) | `run.failed`, `completed: false` |
| `unknown_provider_accepted.sse` | `fake-z` / `zeta` | `run.completed` with a diagnostic text; the lock stays `accepted`, so no model ran |
| `stopped_cancelled.sse` | stopped with `POST /v1/runs/{id}/stop` | `run.cancelled`, `interrupted: true` |
| `lock_without_model.json` | `require_model_lock` with no model on a new session | 400 `missing_model` |
