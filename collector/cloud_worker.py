"""Bounded cloud collection with an internal timer and a controlled successor.

The worker never reconstructs missed observations. It retains the original four
routes per fifteen minutes, fixed end time, ORS-only gate and request budget.
It must run on the public GitHub-hosted workflow with the existing token.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
HARD_END = datetime(2026, 10, 26, 2, 32, 16, 123968, tzinfo=timezone.utc)
WORKFLOW = "collect_resilient.yml"
INTERVAL_SECONDS = 900
ROUTES_PER_CYCLE = 4
HARD_BUDGET = 900
DEFAULT_RUNTIME_SECONDS = 270 * 60
MIN_CYCLE_WINDOW_SECONDS = 60


class WorkerError(RuntimeError):
    """Contains a fixed, nonsecret diagnostic code only."""


class Clock:
    def now(self):
        return datetime.now(timezone.utc)

    def monotonic(self):
        return time.monotonic()

    def sleep(self, seconds):
        time.sleep(seconds)


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def aware_time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise WorkerError("timestamp_without_timezone")
    return parsed.astimezone(timezone.utc)


def inspect_protocol(config, state):
    if config.get("active_providers") != ["ors"]:
        raise WorkerError("worker_requires_ors_only_protocol")
    if config.get("routes_per_scheduled_run") != ROUTES_PER_CYCLE:
        raise WorkerError("worker_requires_four_route_batches")
    if config.get("scheduled_interval_minutes") != 15:
        raise WorkerError("worker_requires_fifteen_minute_interval")
    provider = config["providers"]["ors"]
    if provider.get("collection_approved") is not True:
        raise WorkerError("ors_collection_not_approved")
    end = min(aware_time(config["collection_end_utc"]), HARD_END)
    budget = min(int(provider["study_request_budget"]), HARD_BUDGET)
    reserved = int(state.get("counts", {}).get("ors", 0))
    candidates = []
    for reservation in state.get("reservations", {}).values():
        if reservation.get("mode") in ("scheduled", "manual"):
            value = reservation.get("started_at_utc") or reservation.get("reserved_at_utc")
            if value:
                candidates.append(aware_time(value))
    return {"end": end, "reserved": reserved, "budget": budget,
            "halted": bool(state.get("halted_reason")),
            "last_batch": max(candidates) if candidates else None}


def stop_reason(snapshot, now):
    if now >= snapshot["end"]:
        return "fixed_collection_window_ended"
    if snapshot["halted"]:
        return "credential_billing_or_rate_limit_halt"
    if snapshot["reserved"] + ROUTES_PER_CYCLE > snapshot["budget"]:
        return "request_budget_exhausted"
    return None


def next_due(snapshot, now):
    previous = snapshot["last_batch"]
    return max(now, previous + timedelta(seconds=INTERVAL_SECONDS)) if previous else now


def wait_until(clock, target, monotonic_deadline):
    # Small sleeps allow timely fixed-window checks; no elapsed slots are replayed.
    while clock.now() < target and clock.monotonic() < monotonic_deadline:
        seconds = min(30, (target - clock.now()).total_seconds(), monotonic_deadline-clock.monotonic())
        if seconds > 0:
            clock.sleep(seconds)


def run_worker(backend, clock, run_id, max_runtime_seconds=DEFAULT_RUNTIME_SECONDS):
    started = clock.now()
    monotonic_deadline = clock.monotonic() + max_runtime_seconds
    runtime_end = started + timedelta(seconds=max_runtime_seconds)
    completed = 0
    while True:
        snapshot = backend.inspect()
        reason = stop_reason(snapshot, clock.now())
        if reason:
            return {"reason": reason, "cycles": completed, "handoff": False}
        if clock.monotonic() >= monotonic_deadline or clock.now() >= runtime_end:
            return {"reason": "bounded_worker_runtime_complete", "cycles": completed, "handoff": True}
        cutoff = min(runtime_end, snapshot["end"])
        if backend.process_followup(cutoff):
            # Follow-ups have independent windows and retain the ORS cursor.
            # Re-read their published shared budget/halt before an ordinary batch.
            continue
        due = next_due(snapshot, clock.now())
        if (cutoff-due).total_seconds() < MIN_CYCLE_WINDOW_SECONDS:
            # The successor, if needed, waits for the same next due time.
            if snapshot["end"] <= runtime_end:
                return {"reason": "fixed_window_too_short_for_next_batch", "cycles": completed, "handoff": False}
            return {"reason": "bounded_worker_runtime_complete", "cycles": completed, "handoff": True}
        wait_until(clock, due, monotonic_deadline)
        if clock.monotonic() >= monotonic_deadline:
            return {"reason": "bounded_worker_runtime_complete", "cycles": completed, "handoff": clock.now() < snapshot["end"]}
        # Pull fresh state after waiting, so an earlier worker/reservation cannot
        # be repeated and an explicit halt remains effective.
        snapshot = backend.inspect()
        reason = stop_reason(snapshot, clock.now())
        if reason:
            return {"reason": reason, "cycles": completed, "handoff": False}
        cutoff = min(runtime_end, snapshot["end"])
        if backend.process_followup(cutoff):
            continue
        if next_due(snapshot, clock.now()) > clock.now():
            continue
        cutoff = min(runtime_end, snapshot["end"])
        if (cutoff-clock.now()).total_seconds() < MIN_CYCLE_WINDOW_SECONDS:
            continue
        cycle = f"worker-{run_id}-{clock.now():%Y%m%dT%H%M%SZ}"
        result = backend.collect_cycle(cycle, cutoff)
        completed += 1
        emit("worker_cycle_finished", cycle=cycle, result=result, completed_cycles=completed)
        if result == "deadline_reached":
            return {"reason": "collector_deadline_reached", "cycles": completed,
                    "handoff": runtime_end < snapshot["end"] and clock.now() < snapshot["end"]}


class RepositoryBackend:
    def __init__(self, root, clock, run_id):
        self.root = Path(root)
        self.clock = clock
        self.run_id = str(run_id)

    def command(self, arguments, stage, timeout=120, allow_failure=False):
        try:
            result = subprocess.run(arguments, cwd=self.root, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise WorkerError(f"{stage}_timeout") from error
        if result.returncode and not allow_failure:
            # Never print captured exception bodies, headers, command environments
            # or credentials. GitHub exposes the failed stage and exit code.
            raise WorkerError(f"{stage}_exit_{result.returncode}")
        return result

    def inspect(self):
        dirty = self.command(["git", "status", "--porcelain"], "git_status").stdout.strip()
        if dirty:
            raise WorkerError("working_tree_not_clean_before_sync")
        self.command(["git", "pull", "--ff-only", "origin", "main"], "git_sync")
        config = json.loads((self.root/"config/study.json").read_text(encoding="utf-8-sig"))
        state = json.loads((self.root/"data/collection_state.json").read_text(encoding="utf-8-sig"))
        return inspect_protocol(config, state)

    def commit_and_push(self, paths, message):
        self.command(["git", "add", "--all", "--", *paths], "git_stage")
        changed = self.command(["git", "diff", "--cached", "--quiet"], "git_diff", allow_failure=True)
        if changed.returncode not in (0, 1):
            raise WorkerError("git_diff_failed")
        if changed.returncode == 1:
            self.command(["git", "commit", "-m", message], "git_commit")
        for attempt in range(1, 4):
            try:
                result = self.command(["git", "push", "origin", "HEAD:main"], "git_push", allow_failure=True)
            except WorkerError:
                result = None
            if result is not None and result.returncode == 0:
                return
            emit("git_push_retry", attempt=attempt, maximum_attempts=3)
            if attempt == 3:
                raise WorkerError("git_push_failed_after_three_attempts")
            self.clock.sleep(2*attempt)
            self.command(["git", "fetch", "origin", "main"], "git_fetch_before_retry")
            rebase = self.command(["git", "rebase", "origin/main"], "git_rebase", allow_failure=True)
            if rebase.returncode:
                self.command(["git", "rebase", "--abort"], "git_rebase_abort", allow_failure=True)
                raise WorkerError("git_conflict_requires_inspection")

    def reserve(self, cycle):
        self.command([sys.executable, "collector/collect.py", "--mode", "scheduled", "--cycle-label", cycle, "--reserve-only"], "reserve_request_budget")

    def publish_reservation(self):
        self.commit_and_push(["data/collection_state.json"], "Reserve resilient worker request budget")

    def execute(self, cycle, cutoff):
        remaining = (cutoff-self.clock.now()).total_seconds()-1
        if remaining <= 0:
            return "deadline_reached"
        timeout = min(240, remaining)
        try:
            self.command([sys.executable, "collector/collect.py", "--mode", "scheduled", "--execute-reservation", cycle],
                         "execute_reserved_cycle", timeout=timeout)
        except WorkerError as error:
            if str(error) == "execute_reserved_cycle_timeout" and remaining <= 240:
                # subprocess.run kills and waits for the child. Its threads die
                # with the process; no subsequent route starts after the cutoff.
                return "deadline_reached"
            raise
        return "completed"

    def publish_observations(self):
        self.commit_and_push(["data/raw", "data/collection_state.json", "docs/research_log.md"],
                             "Record resilient worker observations")

    def collect_cycle(self, cycle, cutoff):
        self.reserve(cycle)
        self.publish_reservation()  # An unsuccessful push must prevent every API call.
        try:
            return self.execute(cycle, cutoff)
        finally:
            # Also preserve a partial attempt when the strict deadline kills a
            # collector. Reserved requests are never refunded automatically.
            self.publish_observations()

    def process_followup(self, cutoff):
        if (cutoff-self.clock.now()).total_seconds() < 90:
            return False
        if not (self.root/"docs/google_followup_protocol.json").exists():
            return False
        result = self.command([sys.executable, "-m", "collector.google_followup", "--reserve-due",
                               "--deadline", cutoff.isoformat()], "prepare_google_followup")
        action = json.loads(result.stdout)
        if action["changed"]:
            self.commit_and_push(["data/collection_state.json"], "Reserve or close prospective comparison window")
        cycle = action.get("cycle_id")
        emit("google_followup_checked", action="reserved" if cycle else "no_due_window_or_already_consumed")
        if not cycle:
            return False
        end = min(cutoff, aware_time(action["deadline_utc"]))
        try:
            # A window deadline stops new starts in the child; allow already
            # in-flight metadata to finish. The harder worker/study cutoff kills
            # the process if necessary and prevents another local cycle.
            remaining = (cutoff-self.clock.now()).total_seconds()-1
            if remaining > 0:
                try:
                    self.command([sys.executable, "-m", "collector.google_followup", "--execute", cycle,
                                  "--deadline", end.isoformat()], "execute_google_followup", timeout=min(900, remaining))
                except WorkerError as error:
                    if str(error) != "execute_google_followup_timeout" or remaining > 900:
                        raise
                    emit("google_followup_deadline_reached", cycle=cycle)
        finally:
            paths = [path for path in ("data/collection_state.json", "data/followup",
                                      "results/google_followup_benchmark.csv") if (self.root/path).exists()]
            self.commit_and_push(paths, "Record aggregate-only Google ORS comparison window")
        return True

    def queued_successor_exists(self):
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if not repo:
            raise WorkerError("github_repository_environment_missing")
        result = self.command(["gh", "api", f"repos/{repo}/actions/workflows/{WORKFLOW}/runs?per_page=100"], "check_queued_successor")
        rows = json.loads(result.stdout).get("workflow_runs", [])
        pending_states = {"queued", "pending", "waiting", "requested"}
        return any(str(row.get("id")) != self.run_id and row.get("status") in pending_states for row in rows)

    def dispatch_successor(self):
        self.command(["gh", "workflow", "run", WORKFLOW, "--ref", "main"], "dispatch_successor")


def handoff(backend, clock, end):
    for attempt in range(1, 4):
        if clock.now() >= end:
            return False
        try:
            if backend.queued_successor_exists():
                emit("successor_already_queued")
                return True
            backend.dispatch_successor()
            emit("successor_dispatched", attempt=attempt)
            return True
        except (WorkerError, ValueError, TypeError):
            emit("handoff_retry", attempt=attempt, maximum_attempts=3)
            if attempt < 3:
                clock.sleep(min(15*attempt, max(0, (end-clock.now()).total_seconds())))
    raise WorkerError("successor_handoff_failed_after_three_attempts")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-runtime-seconds", type=int, default=DEFAULT_RUNTIME_SECONDS)
    args = parser.parse_args()
    if not 60 <= args.max_runtime_seconds <= DEFAULT_RUNTIME_SECONDS:
        parser.error("Runtime must be between 60 and 16200 seconds.")
    if os.environ.get("GITHUB_ACTIONS") != "true" or not os.environ.get("GITHUB_RUN_ID"):
        raise SystemExit("This worker only runs inside the approved GitHub Actions workflow.")
    clock = Clock()
    backend = RepositoryBackend(ROOT, clock, os.environ["GITHUB_RUN_ID"])
    try:
        outcome = run_worker(backend, clock, os.environ["GITHUB_RUN_ID"], args.max_runtime_seconds)
        emit("worker_finished", **outcome)
        if outcome["handoff"]:
            snapshot = backend.inspect()
            if stop_reason(snapshot, clock.now()) is None:
                handoff(backend, clock, snapshot["end"])
    except (WorkerError, OSError, ValueError, KeyError, TypeError) as error:
        emit("worker_failed", error_code=str(error) if isinstance(error, WorkerError) else type(error).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
