# Execution amendment: bounded cloud worker

Recorded on 24 October 2026 after the scheduler audit at 11:11:19 UTC
(16:11:19 Astana). This changes execution reliability, not the registered
research question, expected results, route register, provider choice or study end.

## Evidence and reason

The repository had one successful manual cloud verification at 02:51:30 UTC
and one successful `schedule` run at 07:50:02 UTC. No other queued, running,
cancelled or failed runs were visible at the audit. The latter job started two
seconds after run creation and recorded four successful ORS responses. Actions
and the workflow were enabled on the default branch. The collector had no halt
and had reserved 70 of its 900 permitted requests, including two diagnostics.
The exact reason for missing schedule events is not observable from these logs.
GitHub documents that schedule events can be delayed or dropped under load.
This is not evidence of a local computer, key, quota or collection-code failure.

The detailed evidence and source URLs are in `docs/scheduler_audit.json`.
Observed timestamps and all missing coverage remain part of the record.

## Prospective execution change

`collect_resilient.yml` runs a Python worker on a standard public GitHub-hosted
runner for at most 270 minutes, within a 300-minute job timeout. The worker
collects the same four rotating routes, with at least 900 seconds between the
starts of successive scheduled/manual batch reservations. It checks fresh remote
state before each batch. A delayed start shifts the next observation forward;
the worker never sends a burst to recreate earlier time slots or rewrites dates.

Every batch reserves its full quota and commits that reservation to the remote
repository before any routing request. Its observations are committed afterwards.
The existing ORS-only collection approval, 900-request ceiling, credential/rate
halt and original end of **26 October 2026 at 02:32:16 UTC / 07:32:16 Astana**
remain in force. The child collector is stopped at the earlier worker or study
deadline; any partial data and the conservative reservation are preserved.

Before its bounded runtime ends, a worker may dispatch one successor through
the repository's existing `GITHUB_TOKEN`, which already has `actions: write`.
It first checks for a queued/pending successor, retries a failed handoff no more
than three times and reports failure explicitly. Both collector workflows share
the same concurrency group. An existing original-cron pending run must be
cancelled after the original schedule is disabled, so it cannot later insert
an unintended extra batch between worker cycles.

During migration, the original `collect.yml` schedule was temporarily disabled
after confirming that no collector run was active. This prevents a separately
reserved local exploratory test from racing the shared quota state. After that
test's observations are pushed, publish and dispatch the resilient workflow and
verify its first remote reservation and observation commits. Keep the original
workflow disabled after successful verification; if activation fails, re-enable
the original schedule as the fallback. Cancel any pending original-collector run
before the new worker is activated. Do not cancel the active resilient worker.

The first new worker cycle and the subsequent self-dispatch are separate
verification points. At authoring, the new workflow was prepared but had not
been activated; its first successor handoff remains planned until a later real
run verifies it. Passing unit tests does not establish live handoff success.

The workflow also offers a 120-second bootstrap for live handoff verification.
After the first long worker's observation commit is confirmed and it is waiting
between cycles, that waiting run may be cancelled and replaced with the short
bootstrap. If the next permitted batch is more than 120 seconds away, the
bootstrap requests no routing data and immediately dispatches a successor.
The successor uses the normal 16,200-second default because the handoff supplies
no input override. This verifies the existing token's dispatch permission and
concurrency transition without waiting 4.5 hours. This live bootstrap check was
still pending when the option was added; its result must be recorded separately.

## Limits and interpretation

This reduces dependence on repeated cron delivery, but GitHub can still delay
or fail a successor, runner, network request or Git push. Git pushes retry at
most three times; a merge conflict fails visibly rather than guessing how to
combine quota state. Failure artifacts preserve current raw evidence without
credentials. No new account, token, payment or paid runner is introduced.

The unchanged ER8 threshold cannot be restored by inventing missed samples.
At the audit, 158 original quarter-hour slots remained, yielding at most 632
future requests at four per slot. Including eight already recorded cloud-phase
requests gives an upper bound of 640, below the registered minimum of 692,
unless additional nonstandard observations were introduced. Such observations
are not used to repair the expected result. ORS remains a nontraffic control;
better collection reliability does not resolve the blocked traffic comparison.

Official references:

- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- https://docs.github.com/en/actions/reference/limits
- https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow
