---
status: unshaped
kind: feature
appetite: big
lane: public
---

# Deploy on an HPC cluster: `module load`, mount, run

> **Candidate, not a contract.** `code terrace feature` promotes this into a `spec/{slug}/GOAL.md`,
> and that promotion is where appetite, non-goals and the R-IDs get negotiated. `status: unshaped`
> means none of that has happened yet.

## Problem

The goal: a researcher on an HPC cluster runs `module load terrace`, mounts at the top of a batch
script, and it works — free to the researcher and maintained by the center, like any other module.
Nothing in the tree or in `PLAN.md` describes how the product reaches that researcher. `PLAN.md`
§ *Deferred commit* (`PLAN.md:300-313`) shows the job-script pattern for the end of a job, and the
whole lifecycle is designed around batch allocations, but no one has checked that a mount can run on
a compute node at all, and every question below is **unverified** — recorded so that shaping starts
from the list rather than rediscovering it.

1. **FUSE on compute nodes.** An unprivileged mount needs `/dev/fuse` present and accessible, and a
   setuid `fusermount3` (or `fusermount`) for the unmount path. `fuse/mount.py:65-71` already tries
   `fusermount3 -u`, then `fusermount -u`, then `umount`. Whether a typical center's compute nodes
   expose either to ordinary users is unverified, and it is the question that decides whether the
   rest of this seed is a deployment problem or a policy conversation with the center.
2. **Python 3.15 runtime delivery.** `requires-python = ">=3.15"` (`pyproject.toml:11`) and the
   free-threaded build is a first-class target. A center's module tree may not carry 3.15 for a
   while. The two candidate shapes are a uv-managed interpreter installed alongside the tool, or a
   site Python module the tool depends on. Which one a center would maintain is unverified.
3. **How the modulefile is shipped.** Whether the project carries a modulefile template, documents
   one, or leaves it to each center, and what it sets (the entry point on `PATH`, a default state
   directory on node-local or scratch storage, a site config file), is undecided.
4. **The batch scheduler's time-limit sequence mapped onto the lifecycle.** At a job's time limit a
   batch scheduler typically sends SIGTERM and then, after a site-configured grace period, SIGKILL.
   The mount already has the matching shape: SIGTERM selects `Shutdown.DETACH`
   (`fuse/mount.py:278-283`), which rescues node-local dirty data one hop to the nearest shared tier
   within `drain_timeout` and settles `detached` (`fuse/mount.py:255-257`), and `PLAN.md`'s Shutdown
   table says so explicitly ("schedulers send SIGKILL shortly after", `PLAN.md:286`). What is
   unverified: that the scheduler's SIGTERM reaches the mount daemon at all, given that the default
   mount daemonizes (`_daemonize_and_serve`, `fuse/mount.py:366`); that the rescue finishes inside the
   grace period, when the default `drain_timeout` is five minutes (`PLAN.md:388`) and grace periods
   are commonly far shorter; and what the next job sees when SIGKILL lands mid-rescue. **This mapping
   is the product's differentiator** — a job that hits its wall clock loses nothing it could have
   saved, and pays nothing for data movement — and it needs its own R-ID rather than being inferred
   from the signal handlers.
5. **The job-script usage pattern.** Mount at the top, run, then either `umount` (drain), `umount
   --detach` (rescue and stop), or let the time limit do it, with `sosfs commit` from a cheap job or
   a login node afterwards. Whether the mount runs in the foreground under the script's control or
   daemonized, and where its state directory lives so that the later commit can see it, is unwritten.

## Why it was deferred

Never scheduled: neither `PLAN.md` § *Phase outline* nor the original phase checklist carries a
deployment item, and the prerequisites are not built. The offline finalizer the pattern ends with is
a stub ([`issues/offline-commit-command.md`](offline-commit-command.md)); the default daemonized mount
fails on a first-ever mount ([`issues/daemonized-mount-first-run.md`](daemonized-mount-first-run.md));
a second SIGINT strands node-local data and `umount --detach` is bounded where `PLAN.md` says it
completes ([`issues/shutdown-rescue-gaps.md`](shutdown-rescue-gaps.md)); and the mount lifecycle runs
on no automated machine ([`issues/ci-integration-lane.md`](ci-integration-lane.md)). Filed on
2026-11-04 from the owner's statement of the goal. Pre-existing as a gap; nothing was introduced.

## Outcome / vision

On a cluster whose center has installed it, a researcher's batch script reads:

```bash
module load terrace
sosfs mount spec.toml "$MOUNT"
python train.py --data "$MOUNT/dataset"
sosfs umount --detach spec.toml
```

(the entry point under its current name; [`issues/rename-to-terrace.md`](rename-to-terrace.md)
changes it) and works the first time. If the job hits its time limit instead of reaching the last line, the
scheduler's SIGTERM rescues node-local dirty data to shared storage before SIGKILL, and a later
`sosfs commit` finalizes the source off the allocation. The center maintains one module; nobody files
a ticket to make it work.

## Sketch of the acceptance criteria

Draft R-IDs, to be firmed up at promotion. See the KB's EARS notes. All of these need a real cluster
or a faithful stand-in; none is checkable in CI as it stands.

- **R1** — WHEN an unprivileged user on a compute node runs `module load terrace` and then mounts a
  spec whose tiers are a shared source and node-local storage, the mount SHALL succeed and SHALL
  unmount cleanly with `umount`, with no privileged step at job time.
- **R2** — WHEN the batch scheduler sends SIGTERM at a job's time limit, the mount SHALL complete the
  rescue of node-local dirty data to the nearest shared tier and settle `shutdown_state = detached`
  before the site's grace period expires, with `drain_timeout` derived from or bounded by that grace
  period; and WHEN SIGKILL lands before the rescue completes, the next `sosfs commit` SHALL report
  exactly which files were not rescued.
- **R3** — WHEN a job that detached is followed by `sosfs commit` from a different host that sees the
  shared tiers and the state directory, the source SHALL hold every byte the job wrote.
- **R4** — THE documentation SHALL carry a job-script example and a modulefile example that run
  verbatim on a reference cluster, naming no site-specific path.

## Notes

- **Generic names only.** This repository may go public: write no cluster names, account names,
  internal hostnames or site tool names into this seed or the cycle that adopts it. "HPC center" and
  "batch scheduler" are the vocabulary.
- Open question — **foreground or daemonized in a job.** A foreground mount backgrounded by the job
  script (`sosfs mount -f … &`) keeps the daemon in the job's process tree, where the scheduler's
  signal can reach it; the daemonized default may escape it. Which one the documentation recommends
  follows from finding 4.
- Open question — **where the state directory lives.** It must survive the job for `sosfs commit` to
  run later, so it cannot sit on node-local storage alone, and `begin_mount` records the host so a
  crash report can name the node (`AGENTS.md` § *Invariants* § 8).
- Related: [`issues/toolchain-repair.md`](toolchain-repair.md) — setgid project directories, the
  normal layout of HPC shared spaces, break one test today;
  [`issues/sharded-archive-sources.md`](sharded-archive-sources.md) is the use case that brings the
  researcher here; [`issues/offline-commit-command.md`](offline-commit-command.md),
  [`issues/shutdown-rescue-gaps.md`](shutdown-rescue-gaps.md) and
  [`issues/daemonized-mount-first-run.md`](daemonized-mount-first-run.md) are prerequisites;
  [`issues/documentation-and-v0.1.0.md`](documentation-and-v0.1.0.md) owns the operations guide this
  extends. Root `PLAN.md` → § *Lifecycle and recovery* (§ *Shutdown*, § *Deferred commit*).
- Found by: the owner's statement of the deployment goal (2026-11-04). Nothing here has been checked
  on a cluster.
