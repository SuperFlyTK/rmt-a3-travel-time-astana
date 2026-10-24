"""Prospective Google/ORS windows; Google route values exist only in memory.

The worker publishes --reserve-due state before invoking --execute. A consumed
window is never replayed after interruption, even if its requests were unused.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import build_opener

from collector import collect
from collector.google_control_test import aggregate

ROOT = collect.ROOT
HARD_END = datetime(2026, 10, 26, 2, 32, 16, 123968, tzinfo=timezone.utc)
PROTOCOL = "docs/google_followup_protocol.json"
STATUS_FIELDS = ["cycle_id", "window_id", "timestamp_utc", "route_id", "provider", "http_code", "status"]
HALT_CODES = (401, 402, 403, 429)
MINIMUM_START_WINDOW_SECONDS = 90


def stamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timezone_required")
    return parsed.astimezone(timezone.utc)


def route_hash(data):
    # Git checkout may normalize Windows CRLF to LF on the Linux runner.
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def load_protocol(root):
    data = (root / PROTOCOL).read_bytes()
    protocol = json.loads(data)
    if (protocol["routes_per_window"] != 20 or not 1 <= len(protocol["windows"]) <= 5
            or protocol["publish_google_raw"] is not False
            or protocol["google_secret_env"] != "GOOGLE_MAPS_DEMO_KEY"
            or protocol["google_routing_preference"] != "TRAFFIC_AWARE_OPTIMAL"
            or protocol["minimum_pairs_for_numeric_aggregates"] < 5):
        raise ValueError("invalid_followup_protocol")
    approved = stamp(protocol["approved_at_utc"])
    ids = set()
    for window in protocol["windows"]:
        identifier = window["id"]
        if (identifier in ids or not identifier or not all(c.isalnum() or c in "-_" for c in identifier)
                or not approved < stamp(window["start_utc"]) < stamp(window["end_utc"]) <= min(HARD_END, stamp(protocol["collection_end_utc"]))):
            raise ValueError("invalid_followup_window")
        ids.add(identifier)
    return protocol, hashlib.sha256(data).hexdigest()


def prepare_window(state, config, config_hash, protocol, protocol_hash, routes, now, deadline):
    """Pure state transition: no network, file access, or quota refunds."""
    followup = state.setdefault("google_followup", {"windows": {}})
    windows = followup.setdefault("windows", {})
    changed = False
    hard_end = min(HARD_END, stamp(protocol["collection_end_utc"]), stamp(config["collection_end_utc"]))
    for window in protocol["windows"]:
        identifier = window["id"]
        previous = windows.get(identifier)
        if previous:
            if previous["status"] in ("reserved", "started"):
                previous.update(status="interrupted_no_replay", inspected_at_utc=now.isoformat())
                changed = True
            continue
        start, end = stamp(window["start_utc"]), min(stamp(window["end_utc"]), hard_end)
        if now >= end:
            windows[identifier] = {"status": "missed", "recorded_at_utc": now.isoformat()}
            changed = True
            continue
        if now < start:
            continue
        reason = None
        if state.get("halted_reason"):
            reason = "global_ors_halt"
        elif followup.get("halted_reason"):
            reason = "google_followup_halt"
        counts = state.get("counts", {})
        prior_google = max(int(counts.get("google_demo", 0)), int(protocol["google_already_reserved"]), 21)
        consumed = sum(int(p.get("google_followup_reserved_requests", 0)) for p in state.get("reservations", {}).values())
        if (prior_google + 20 > min(int(protocol["google_total_request_budget"]), 121)
                or consumed + 20 > min(int(protocol["future_google_request_budget"]), 100)
                or consumed + 20 > min(int(protocol["future_ors_request_budget"]), 100)
                or int(counts.get("ors", 0)) + 20 > min(int(config["providers"]["ors"]["study_request_budget"]), 900)):
            reason = "request_budget_exhausted"
        if reason:
            windows[identifier] = {"status": "skipped", "reason": reason, "recorded_at_utc": now.isoformat()}
            changed = True
            continue
        cutoff = min(end, deadline)
        if (cutoff - now).total_seconds() < MINIMUM_START_WINDOW_SECONDS:
            continue  # A successor can use the still-open window; never squeeze a new batch in.
        if config["active_providers"] != ["ors"] or len(routes) != 20:
            raise ValueError("followup_requires_twenty_routes_and_ors_only_shared_config")
        if identifier in state.get("reservations", {}):
            windows[identifier] = {"status": "interrupted_no_replay", "inspected_at_utc": now.isoformat()}
            changed = True
            continue
        plan = collect.reserve_plan(state, config, routes, "exploratory", identifier, config_hash)
        plan.update(google_followup_reserved_requests=20, protocol_sha256=protocol_hash,
                    window_start_utc=window["start_utc"], window_end_utc=window["end_utc"],
                    period=window["period"])
        state["counts"]["google_demo"] = prior_google + 20
        windows[identifier] = {"status": "reserved", "cycle_id": identifier, "reserved_at_utc": now.isoformat()}
        return {"changed": True, "cycle_id": identifier, "deadline_utc": cutoff.isoformat()}
    return {"changed": changed, "cycle_id": None}


def google_request(route, key):
    record = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "route_id": route["route_id"],
              "provider": "google_demo", "http_code": "", "status": "transport_error"}
    try:
        request = collect.build_request("google", route, key)
        request.add_header("X-Goog-FieldMask", "routes.duration,routes.distanceMeters")
        with build_opener(collect.NoRedirect()).open(request, timeout=30) as response:
            payload = json.loads(response.read(1_000_000))
            record["http_code"] = response.status
        first = payload["routes"][0]
        if not isinstance(first["duration"], str):
            raise ValueError("unexpected_duration_type")
        duration, distance = float(first["duration"].removesuffix("s")), float(first["distanceMeters"])
        if not all(math.isfinite(v) and v > 0 for v in (duration, distance)):
            raise ValueError("unexpected_measurement")
        record["status"] = "ok"
        return record, (duration, distance)
    except HTTPError as error:
        record.update(http_code=error.code, status="http_error")
        error.close()
    except (URLError, TimeoutError, OSError, HTTPException):
        pass
    except (KeyError, IndexError, ValueError, TypeError):
        record["status"] = "invalid_response"
    return record, None


def append_row(path, row, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if handle.tell() == 0:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fields})
        handle.flush()
        os.fsync(handle.fileno())


def valid_pair(google, values, ors, start, end, max_gap):
    if not values or google["status"] != "ok" or ors["status"] != "ok":
        return None
    gtime, otime = stamp(google["timestamp_utc"]), stamp(ors["timestamp_utc"])
    if not (start <= gtime < end and start <= otime < end) or abs((gtime-otime).total_seconds()) > max_gap:
        return None
    try:
        pair = (*values, float(ors["duration_s"]), float(ors["distance_m"]))
    except (ValueError, TypeError, KeyError):
        return None
    if not all(math.isfinite(v) and v > 0 for v in pair):
        return None
    if not all(2 <= distance/duration*3.6 <= 200 for duration, distance in ((pair[0],pair[1]), (pair[2],pair[3]))):
        return None
    return pair


def execute_window(root, state, config, protocol, protocol_hash, cycle, deadline,
                   google_key, ors_key, now=lambda: datetime.now(timezone.utc), sleep=time.sleep,
                   google_fetch=google_request, ors_fetch=collect.fetch):
    """Only ORS evidence/status metadata and >=5-pair aggregates reach disk."""
    plan = state["reservations"][cycle]
    record = state["google_followup"]["windows"][cycle]
    if plan["status"] != "reserved" or record["status"] != "reserved":
        raise ValueError("consumed_window_must_not_replay")
    if plan["protocol_sha256"] != protocol_hash:
        raise ValueError("followup_protocol_changed_after_reservation")
    if route_hash((root/"data/routes.csv").read_bytes()) != protocol["routes_sha256"]:
        raise ValueError("route_register_changed_after_reservation")
    config_hash = hashlib.sha256((root/"config/study.json").read_bytes()).hexdigest()
    if plan["config_sha256"] != config_hash or config["active_providers"] != ["ors"]:
        raise ValueError("shared_config_changed_after_reservation")
    if state.get("halted_reason") or state["google_followup"].get("halted_reason"):
        raise ValueError("collection_halted")
    start = stamp(plan["window_start_utc"])
    end = min(stamp(plan["window_end_utc"]), HARD_END, stamp(config["collection_end_utc"]), deadline)
    statepath = root/"data/collection_state.json"
    if not start <= now() < end:
        plan["status"] = record["status"] = "expired_without_requests"
        collect.atomic_state(statepath, state)
        return {"cycle_id": cycle, "n_attempted_pairs": 0, "status": "expired_without_requests"}
    if not google_key or not ors_key:
        state["google_followup"]["halted_reason"] = "missing_followup_credential"
        plan["status"] = record["status"] = "skipped_missing_credential"
        collect.atomic_state(statepath, state)
        return {"cycle_id": cycle, "n_attempted_pairs": 0, "status": "skipped_missing_credential"}
    plan.update(status="started", started_at_utc=now().isoformat())
    record.update(status="started", started_at_utc=plan["started_at_utc"])
    collect.atomic_state(statepath, state)
    pairs, gaps = [], []
    attempted = google_ok = ors_ok = 0
    directory = root/"data/followup/ors"
    directory.mkdir(parents=True, exist_ok=True)
    for route in plan["routes"]:
        if now() >= end:
            break
        tick = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as executor:
            g = executor.submit(google_fetch, route, google_key)
            o = executor.submit(ors_fetch, "ors", route, ors_key, cycle, "followup", directory, [google_key, ors_key])
            google, values = g.result()
            ors = o.result()
        attempted += 1
        google_ok += google["status"] == "ok"
        ors_ok += ors["status"] == "ok"
        append_row(root/"data/followup/google_request_status.csv",
                   {**google, "cycle_id": cycle, "window_id": cycle}, STATUS_FIELDS)
        append_row(directory/"observations.csv", ors, collect.FIELDS)
        gap = abs((stamp(google["timestamp_utc"])-stamp(ors["timestamp_utc"])).total_seconds())
        gaps.append(gap)
        pair = valid_pair(google, values, ors, start, min(stamp(plan["window_end_utc"]), HARD_END),
                          min(float(protocol["max_pair_start_gap_seconds"]), 60))
        if pair:
            pairs.append(pair)
        values = pair = None
        plan["recorded_attempts"] = record["recorded_attempts"] = attempted
        if google["http_code"] in HALT_CODES:
            state["google_followup"]["halted_reason"] = "google_credential_billing_or_rate_limit_response"
        if ors["http_code"] in HALT_CODES:
            state["halted_reason"] = "credential_billing_or_rate_limit_response"
        collect.atomic_state(statepath, state)
        if state.get("halted_reason") or state["google_followup"].get("halted_reason"):
            break
        sleep(min(max(0, max(float(protocol["minimum_pair_interval_seconds"]), 2.1)-(time.monotonic()-tick)),
                  max(0, (end-now()).total_seconds())))
    metrics = aggregate(pairs) if len(pairs) >= max(int(protocol["minimum_pairs_for_numeric_aggregates"]), 5) else aggregate([])
    metrics["n_valid_pairs"] = len(pairs)
    result = {"cycle_id": cycle, "period": plan["period"], "window_start_utc": plan["window_start_utc"],
              "window_end_utc": plan["window_end_utc"], "start_utc": plan["started_at_utc"], "end_utc": now().isoformat(),
              "n_attempted_pairs": attempted, "n_google_ok": google_ok, "n_ors_ok": ors_ok,
              "max_request_start_gap_s": max(gaps, default=0), **metrics}
    result["completion_reason"] = ("global_ors_halt" if state.get("halted_reason") else
                                   "google_followup_halt" if state["google_followup"].get("halted_reason") else
                                   "all_reserved_routes_attempted" if attempted == len(plan["routes"]) else "window_or_runtime_ended")
    append_row(root/"results/google_followup_benchmark.csv", result, list(result))
    pairs.clear()
    plan.update(status="completed", completed_at_utc=now().isoformat())
    record.update(status="completed", completed_at_utc=plan["completed_at_utc"], n_valid_pairs=result["n_valid_pairs"])
    state.setdefault("completed_cycles", []).append(cycle)
    collect.atomic_state(statepath, state)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--reserve-due", action="store_true")
    modes.add_argument("--execute")
    parser.add_argument("--deadline", required=True)
    args = parser.parse_args()
    protocol, protocol_hash = load_protocol(ROOT)
    config_bytes = (ROOT/"config/study.json").read_bytes()
    config = json.loads(config_bytes)
    with collect.collection_lock():
        statepath = ROOT/"data/collection_state.json"
        state = json.loads(statepath.read_text(encoding="utf-8-sig"))
        if args.reserve_due:
            route_bytes = (ROOT/"data/routes.csv").read_bytes()
            if route_hash(route_bytes) != protocol["routes_sha256"]:
                raise ValueError("route_register_changed")
            routes = list(csv.DictReader(route_bytes.decode("utf-8-sig").splitlines()))
            result = prepare_window(state, config, hashlib.sha256(config_bytes).hexdigest(), protocol, protocol_hash,
                                    routes, datetime.now(timezone.utc), stamp(args.deadline))
            if result["changed"]:
                collect.atomic_state(statepath, state)
        else:
            result = execute_window(ROOT, state, config, protocol, protocol_hash, args.execute, stamp(args.deadline),
                                    os.environ.get("GOOGLE_MAPS_DEMO_KEY", "").strip(), os.environ.get("ORS_KEY", "").strip())
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({"error": "followup_validation_or_io_failure", "type": type(error).__name__}), flush=True)
        raise SystemExit(1)
