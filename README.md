Terrace
=======

Managed data tiering for research computing: a FUSE mount presents one logical namespace
over an ordered stack of storage tiers — e.g., `/project` → `/scratch` → `/tmp` →
`/dev/shm`, with S3 and TAR/ZIP archives available as tiers. Files promote toward fast
tiers on access, write back toward the authoritative source on policy clocks, and evict
least-recently-used when per-tier quotas hit. A detach/commit lifecycle lets expensive
compute allocations stop immediately and finalize the source later, from any host.

**Status: pre-release** — `0.1.0` in development. See [PLAN.md](PLAN.md) for the design
and [ROADMAP.md](ROADMAP.md) for progress.

Requires Python 3.15+.

```bash
uv sync                                    # development install
uv run terrace --help                      # CLI surface
uv run terrace mount spec.toml /mnt/space  # mount an overlay (foreground: -f)
```

Limitations
-----------

- Hard links are not supported: `link` on a mount fails with `EPERM`.

License: Apache-2.0
