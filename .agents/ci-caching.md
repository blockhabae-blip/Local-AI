# CI Build Caching

Container builds — both the root LocalAI image (`Dockerfile`) and the per-backend images (`backend/Dockerfile.*`) — share a registry-backed BuildKit cache. This file explains how that cache is laid out, what invalidates it, and how to bypass it.

## Cache layout

- **Cache registry**: `quay.io/go-skynet/ci-cache`
- **One tag per matrix entry**, derived from the existing `tag-suffix`:
  - Backend builds (`backend_build.yml`): `cache<tag-suffix>`
    - e.g. `cache-gpu-nvidia-cuda-12-llama-cpp`, `cache-cpu-vllm`, `cache-nvidia-l4t-cuda-13-arm64-vllm`
  - Root image builds (`image_build.yml`): `cache-localai<tag-suffix>`
    - e.g. `cache-localai-gpu-nvidia-cuda-12`, `cache-localai-gpu-vulkan`
- Each tag stores a multi-arch BuildKit cache manifest (`mode=max`), so every intermediate stage is re-usable, not just the final image.

## Read/write semantics

| Trigger | `cache-from` | `cache-to` |
|---|---|---|
| `push` to `master` / tag | yes | yes (`mode=max,ignore-error=true`) |
| `pull_request` | yes | **no** |

PR builds read master's warm cache but never write — this prevents PRs from polluting the shared cache with their experimental state. After merge, the master build for that matrix entry refreshes the cache.

`ignore-error=true` on the write side means a transient quay push failure does not fail the build; the next master push retries.

## Self-warming, no separate populator

There is no cron job that pre-warms the cache. The production builds *are* the populator. The first master build of a given matrix entry pays the cold cost; subsequent same-entry master builds reuse everything that hasn't changed (apt installs, gRPC compile in `Dockerfile.{llama-cpp,ik-llama-cpp,turboquant}`, Python wheel installs, etc.).

Historically there was a `generate_grpc_cache.yaml` cron that targeted a `grpc` stage in the root Dockerfile. That stage was removed in July 2025 and the cron silently failed every night for 9 months without writing anything. It was deleted along with the registry-cache rollout.

## The `DEPS_REFRESH` cache-buster (Python backends)

Every Python backend goes through the shared `backend/Dockerfile.python`, which ends with:

```dockerfile
ARG DEPS_REFRESH=initial
RUN cd /${BACKEND} && PORTABLE_PYTHON=true make
```

Most Python backends ship `requirements*.txt` files that **do not pin every transitive dep** (`torch`, `transformers`, `vllm`, `diffusers`, etc. are listed without a `==` pin, or with `>=` lower bounds only). With a warm BuildKit cache, the `make` layer hashes only on Dockerfile instructions + COPYed source — not on what `pip install` resolves at runtime. So a warm cache would ship the *first* version of `vllm` ever cached and never pick up upstream releases.

`DEPS_REFRESH` defends against that:

- `backend_build.yml` computes `date -u +%Y-W%V` (ISO week, e.g. `2026-W17`) before each build and passes it as a build-arg.
- The `RUN ... make` layer's BuildKit hash now includes that string, so the layer invalidates **at most once per week**, automatically picking up newer wheels.
- Within a week, builds stay warm.

This applies only to `Dockerfile.python` because:
- Go (`Dockerfile.golang`) pins versions in `go.mod` / `go.sum`.
- Rust (`Dockerfile.rust`) pins via `Cargo.lock`.
- C++ backends (`Dockerfile.{llama-cpp,ik-llama-cpp,turboquant}`) clone gRPC at a pinned tag (`v1.65.0`) and llama.cpp at a pinned commit; their inputs don't drift between rebuilds.

### Adjusting the cadence

If you need a faster refresh (e.g. while debugging an upstream flake), bump the format to daily (`+%Y-%m-%d`) or hourly (`+%Y-%m-%d-%H`). If you need a one-shot rebuild for a specific backend without changing the schedule, append a marker to the tag-suffix in the matrix or temporarily delete that backend's cache tag in quay.

## Manually evicting cache

To force a fully cold build for one backend or the whole image:

```bash
# Delete a single tag (requires quay credentials with admin on the repo)
curl -X DELETE \
  -H "Authorization: Bearer ${QUAY_TOKEN}" \
  https://quay.io/api/v1/repository/go-skynet/ci-cache/tag/cache-gpu-nvidia-cuda-12-vllm

# List all tags
curl -s -H "Authorization: Bearer ${QUAY_TOKEN}" \
  "https://quay.io/api/v1/repository/go-skynet/ci-cache/tag/?limit=100" | jq '.tags[].name'
```

Eviction is rarely needed in normal operation — `DEPS_REFRESH` handles weekly drift, source changes invalidate naturally, and `mode=max` keeps the cache scoped per matrix entry so a stale tag never bleeds into a different build.

## What the cache **does not** cover

- The "Free Disk Space" / "Release space from worker" steps run on every job — these reclaim ~6 GB on `ubuntu-latest` runners. They are runner-state cleanup, not Docker, and BuildKit caches don't apply.
- Intermediate artifacts of `Build and push (PR)` are not pushed anywhere — PRs only build for verification.

## Touching the cache pipeline

When changing `image_build.yml`, `backend_build.yml`, or any of the `backend/Dockerfile.*` files:

1. **Don't drop `DEPS_REFRESH=...` from the build-args** without a replacement strategy (lockfiles, pinned requirements). Otherwise master will silently freeze on whichever versions were cached at the time.
2. **Keep `tag-suffix` unique per matrix entry** — it's the cache namespace. Two matrix entries sharing a tag-suffix would clobber each other's cache.
3. **Keep `cache-to` gated on `github.event_name != 'pull_request'`** — PRs must not write.
4. **Keep `ignore-error=true` on `cache-to`** — quay registry hiccups must not fail builds.
