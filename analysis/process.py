"""Validate recorded requests and measure agreement, never ground-truth accuracy.

Run from the repository: python analysis/process.py
Only top-level data/raw/*.csv files are inputs. Raw files are never modified.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import importlib.metadata
import json
import math
import platform
import random
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

RAW_FIELDS = "timestamp_utc timestamp_astana cycle_id route_id provider duration_s duration_no_traffic_s distance_m status http_code error request_id latency_s geometry_file raw_file phase".split()
PROVIDER_ORDER = {"tomtom": 0, "dgis": 1, "ors": 2}
PAIR_FIELDS = "timestamp_utc timestamp_astana date_astana phase cycle_id route_id provider_a provider_b comparison_role period time_gap_s duration_a_s duration_b_s distance_a_m distance_b_m mean_duration_s signed_diff_s abs_diff_s relative_diff_pct smape_pct length_diff_pct length_comparable".split()
METRIC_FIELDS = "phase provider_a provider_b comparison_role period length_group n_pairs n_routes n_dates mean_signed_diff_s mae_s rmse_s median_abs_diff_s q1_abs_diff_s q3_abs_diff_s iqr_abs_diff_s smape_pct median_time_gap_s max_time_gap_s median_length_diff_pct median_abs_ci_low_s median_abs_ci_high_s mae_ci_low_s mae_ci_high_s ci_status ci_method bootstrap_replicates bootstrap_seed".split()
OBS_FIELDS = RAW_FIELDS + "source_file source_line period date_astana speed_kmh usable exclusion_reason duplicate_kind".split()
try:
    ASTANA = ZoneInfo("Asia/Almaty")
except ZoneInfoNotFoundError as exc:
    raise SystemExit("Timezone data unavailable. Install the pinned tzdata dependency.") from exc


def parse_time(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timezone offset is required")
    return parsed.astimezone(timezone.utc)


def classify_period(instant):
    local = instant.astimezone(ASTANA)
    minute = local.hour * 60 + local.minute + local.second / 60
    if 17 * 60 + 30 <= minute < 19 * 60 + 30:
        return "peak"
    if 10 * 60 <= minute < 12 * 60 or 22 * 60 <= minute < 24 * 60:
        return "offpeak"
    return "other"


def number(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def quantile(values, probability):
    """Linear interpolation, equivalent to the common type-7 quantile."""
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * probability
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def read_raw(raw_dir, root=None):
    observations = []
    manifest = []
    for path in sorted(raw_dir.glob("*.csv")):
        try:
            source_path = path.resolve().relative_to(root.resolve()).as_posix() if root else path.name
        except ValueError:
            source_path = path.resolve().as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        count = 0
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            missing = set(RAW_FIELDS) - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"{path.name}: missing required columns {sorted(missing)}")
            for line, item in enumerate(reader, start=2):
                row = {field: (item.get(field) or "").strip() for field in RAW_FIELDS}
                row.update(source_file=source_path, source_line=line)
                observations.append(row)
                count += 1
        manifest.append({"file": source_path, "sha256": digest, "rows": count})
    return observations, manifest


def validate_rows(raw_rows):
    """Keep an audit row for every input; only usable unique rows enter pairs."""
    rows = []
    fingerprints = set()
    for source in raw_rows:
        row = dict(source)
        row.update(period="", date_astana="", speed_kmh=None, usable=False,
                   exclusion_reason="", duplicate_kind="")
        reasons = []
        fingerprint = tuple(row.get(field, "") for field in RAW_FIELDS)
        if fingerprint in fingerprints:
            row["duplicate_kind"] = "exact"
            reasons.append("exact_duplicate")
        fingerprints.add(fingerprint)
        try:
            instant = parse_time(row["timestamp_utc"])
            local_raw = parse_time(row["timestamp_astana"])
            if abs((instant - local_raw).total_seconds()) > 1:
                reasons.append("timestamp_disagreement")
            row["_time"] = instant
            row["period"] = classify_period(instant)
            row["date_astana"] = instant.astimezone(ASTANA).date().isoformat()
        except (ValueError, TypeError, OverflowError):
            reasons.append("invalid_timestamp")
        if row["provider"] not in PROVIDER_ORDER:
            reasons.append("unknown_provider")
        if not row["cycle_id"] or not row["route_id"] or not row["phase"]:
            reasons.append("missing_identity")
        if row["phase"] not in ("pilot", "scheduled", "manual"):
            reasons.append("unknown_phase")
        if row["status"].lower() != "ok" or str(row["http_code"]) != "200":
            reasons.append("request_failed")
        duration, distance = number(row["duration_s"]), number(row["distance_m"])
        if duration is None or duration <= 0:
            reasons.append("invalid_duration")
        if distance is None or distance <= 0:
            reasons.append("invalid_distance")
        if duration is not None and duration > 0 and distance is not None and distance > 0:
            speed = distance / duration * 3.6
            row["speed_kmh"] = speed
            if not 2 <= speed <= 200:
                reasons.append("speed_outside_2_200_kmh")
        row["_duration"], row["_distance"] = duration, distance
        baseline = number(row["duration_no_traffic_s"])
        if row["duration_no_traffic_s"] and (baseline is None or baseline <= 0):
            # Optional baseline is not used in agreement; log its invalidity separately.
            row["_invalid_baseline"] = True
        latency = number(row["latency_s"])
        if row["latency_s"] and (latency is None or latency < 0):
            reasons.append("invalid_latency")
        row["_reasons"] = reasons
        rows.append(row)
    identities = defaultdict(list)
    request_ids = defaultdict(list)
    for row in rows:
        if row["duplicate_kind"] != "exact":
            identities[(row["phase"], row["cycle_id"], row["route_id"], row["provider"])].append(row)
            if row["request_id"]:
                request_ids[row["request_id"]].append(row)
    for group in identities.values():
        if len(group) > 1:
            for row in group:
                row["duplicate_kind"] = "conflicting_identity"
                row["_reasons"].append("conflicting_duplicate_identity")
    for group in request_ids.values():
        if len(group) > 1:
            for row in group:
                row["_reasons"].append("duplicate_request_id")
    for row in rows:
        row["usable"] = not row["_reasons"]
        row["exclusion_reason"] = ";".join(row["_reasons"])
    remaining = len(rows)
    cleaning = [{"step": 0, "rule": "Read all top-level raw CSV files; originals unchanged", "rows_before": remaining,
                 "rows_after": remaining, "affected_rows": 0, "finding": "All exclusions remain visible in observations.csv"}]
    removed = set()
    rules = ["exact_duplicate", "conflicting_duplicate_identity", "duplicate_request_id", "invalid_timestamp", "timestamp_disagreement", "unknown_provider", "missing_identity", "unknown_phase", "request_failed", "invalid_duration", "invalid_distance", "speed_outside_2_200_kmh", "invalid_latency"]
    for step, rule in enumerate(rules, start=1):
        flagged = {i for i, row in enumerate(rows) if rule in row["_reasons"]}
        newly_removed = flagged - removed
        cleaning.append({"step": step, "rule": rule, "rows_before": remaining, "rows_after": remaining - len(newly_removed),
                         "affected_rows": len(flagged), "finding": f"{len(flagged)} flagged; {len(newly_removed)} newly excluded from usable observations"})
        remaining -= len(newly_removed)
        removed |= flagged
    optional_bad = sum(bool(row.get("_invalid_baseline")) for row in rows)
    cleaning.append({"step": len(cleaning), "rule": "Optional no-traffic duration must be positive and finite when supplied", "rows_before": remaining, "rows_after": remaining,
                     "affected_rows": optional_bad, "finding": "Baseline is not used in paired agreement metrics; raw value retained"})
    return rows, cleaning


def build_pairs(rows, max_gap=60):
    groups = defaultdict(list)
    for row in rows:
        if row["usable"]:
            groups[(row["phase"], row["cycle_id"], row["route_id"])].append(row)
    pairs, exclusions = [], []
    for (phase, cycle, route), group in sorted(groups.items()):
        for a, b in itertools.combinations(sorted(group, key=lambda r: PROVIDER_ORDER[r["provider"]]), 2):
            gap = abs((a["_time"] - b["_time"]).total_seconds())
            reason = ""
            if gap > max_gap:
                reason = "time_gap_over_60s"
            elif a["period"] != b["period"] or a["date_astana"] != b["date_astana"]:
                reason = "period_or_date_boundary"
            if reason:
                exclusions.append({"phase": phase, "cycle_id": cycle, "route_id": route,
                                   "provider_a": a["provider"], "provider_b": b["provider"], "time_gap_s": gap, "reason": reason})
                continue
            instant = a["_time"] + (b["_time"] - a["_time"]) / 2
            mean = (a["_duration"] + b["_duration"]) / 2
            diff = a["_duration"] - b["_duration"]
            length_diff = 100 * abs(a["_distance"] - b["_distance"]) / ((a["_distance"] + b["_distance"]) / 2)
            pairs.append(dict(zip(PAIR_FIELDS, [
                instant.isoformat(), instant.astimezone(ASTANA).isoformat(), a["date_astana"], phase, cycle, route,
                a["provider"], b["provider"], "control" if "ors" in (a["provider"], b["provider"]) else "primary",
                a["period"], gap, a["_duration"], b["_duration"], a["_distance"], b["_distance"], mean,
                diff, abs(diff), 100 * diff / mean, 100 * abs(diff) / mean, length_diff, length_diff <= 5 + 1e-12,
            ])))
    return pairs, exclusions


def basic_metrics(pairs):
    absolute = [p["abs_diff_s"] for p in pairs]
    return {"mean_signed_diff_s": statistics.mean(p["signed_diff_s"] for p in pairs),
            "mae_s": statistics.mean(absolute), "rmse_s": math.sqrt(statistics.mean(x * x for x in absolute)),
            "median_abs_diff_s": statistics.median(absolute), "q1_abs_diff_s": quantile(absolute, .25),
            "q3_abs_diff_s": quantile(absolute, .75), "iqr_abs_diff_s": quantile(absolute, .75) - quantile(absolute, .25),
            "smape_pct": statistics.mean(p["smape_pct"] for p in pairs)}


def clustered_ci(pairs, seed=2424, replicates=2000):
    """Percentile bootstrap of routes, preserving every cycle/day within route."""
    base = {"median_abs_ci_low_s": None, "median_abs_ci_high_s": None, "mae_ci_low_s": None, "mae_ci_high_s": None,
            "ci_method": "route_cluster_percentile_95", "bootstrap_replicates": 0, "bootstrap_seed": seed}
    routes = sorted({p["route_id"] for p in pairs})
    dates = {p["date_astana"] for p in pairs}
    if any(p["phase"] == "pilot" for p in pairs):
        return dict(base, ci_status="not_estimated_pilot")
    if len(routes) < 8 or len(dates) < 2:
        return dict(base, ci_status="not_estimated_need_8_routes_and_2_dates")
    grouped = {route: [p["abs_diff_s"] for p in pairs if p["route_id"] == route] for route in routes}
    rng = random.Random(seed)
    medians, means = [], []
    for _ in range(replicates):
        sampled = list(itertools.chain.from_iterable(grouped[rng.choice(routes)] for _ in routes))
        medians.append(statistics.median(sampled))
        means.append(statistics.mean(sampled))
    return dict(base, ci_status="estimated_conditional_on_sampled_routes", bootstrap_replicates=replicates,
                median_abs_ci_low_s=quantile(medians, .025), median_abs_ci_high_s=quantile(medians, .975),
                mae_ci_low_s=quantile(means, .025), mae_ci_high_s=quantile(means, .975))


def metric_table(pairs, seed=2424, replicates=2000):
    output = []
    identities = sorted({(p["phase"], p["provider_a"], p["provider_b"]) for p in pairs})
    for phase, a, b in identities:
        relevant = [p for p in pairs if (p["phase"], p["provider_a"], p["provider_b"]) == (phase, a, b)]
        for period in ["all", "peak", "offpeak", "other"]:
            for length_group in ["all", "comparable", "noncomparable"]:
                group = [p for p in relevant if (period == "all" or p["period"] == period) and
                         (length_group == "all" or p["length_comparable"] == (length_group == "comparable"))]
                if not group:
                    continue
                row = dict(phase=phase, provider_a=a, provider_b=b, comparison_role=group[0]["comparison_role"],
                           period=period, length_group=length_group, n_pairs=len(group),
                           n_routes=len({p["route_id"] for p in group}), n_dates=len({p["date_astana"] for p in group}),
                           median_time_gap_s=statistics.median(p["time_gap_s"] for p in group),
                           max_time_gap_s=max(p["time_gap_s"] for p in group),
                           median_length_diff_pct=statistics.median(p["length_diff_pct"] for p in group))
                row.update(basic_metrics(group))
                row.update(clustered_ci(group, seed, replicates))
                output.append(row)
    return output


def exact_signed_rank(differences):
    """Two-sided exact sign-permutation signed-rank test; zero contrasts omitted.

    Ties receive average ranks. Integer doubled ranks avoid rounding errors.
    Symmetry and independent route-level contrasts are assumptions, not verified facts.
    """
    nonzero = [d for d in differences if d != 0]
    if not nonzero:
        return {"p_value": 1.0, "rank_biserial": 0.0, "n_nonzero": 0, "w_plus": 0.0, "w_minus": 0.0}
    absolute = sorted(abs(d) for d in nonzero)
    doubled_ranks = {v: (absolute.index(v) + 1) + (len(absolute) - absolute[::-1].index(v)) for v in set(absolute)}
    ranks = [doubled_ranks[abs(d)] for d in nonzero]
    observed = abs(sum(rank if d > 0 else -rank for rank, d in zip(ranks, nonzero)))
    distribution = {0: 1}
    for rank in ranks:
        updated = defaultdict(int)
        for total, count in distribution.items():
            updated[total + rank] += count
            updated[total - rank] += count
        distribution = updated
    p_value = sum(count for total, count in distribution.items() if abs(total) >= observed) / 2 ** len(ranks)
    w_plus = sum(rank / 2 for rank, d in zip(ranks, nonzero) if d > 0)
    w_minus = sum(rank / 2 for rank, d in zip(ranks, nonzero) if d < 0)
    return {"p_value": p_value, "rank_biserial": (w_plus - w_minus) / (w_plus + w_minus),
            "n_nonzero": len(nonzero), "w_plus": w_plus, "w_minus": w_minus}


def inference_table(pairs):
    # Only the pre-specified traffic-aware primary comparison is tested.
    primary = [p for p in pairs if p["comparison_role"] == "primary"]
    contrasts, outcomes = [], []
    for subset in ("all", "comparable", "noncomparable"):
        eligible = [p for p in primary if p["phase"] != "pilot" and p["period"] in ("peak", "offpeak") and
                    (subset == "all" or p["length_comparable"] == (subset == "comparable"))]
        grouped = defaultdict(lambda: defaultdict(list))
        for pair in eligible:
            grouped[(pair["route_id"], pair["date_astana"])][pair["period"]].append(pair["abs_diff_s"])
        subset_contrasts = []
        for (route, day), periods in sorted(grouped.items()):
            if not periods.get("peak") or not periods.get("offpeak"):
                continue
            peak, offpeak = statistics.median(periods["peak"]), statistics.median(periods["offpeak"])
            item = dict(length_group=subset, route_id=route, date_astana=day, n_peak=len(periods["peak"]),
                        n_offpeak=len(periods["offpeak"]), peak_median_abs_s=peak, offpeak_median_abs_s=offpeak,
                        peak_minus_offpeak_s=peak-offpeak)
            subset_contrasts.append(item)
            contrasts.append(item)
        by_route = defaultdict(list)
        for item in subset_contrasts:
            by_route[item["route_id"]].append(item["peak_minus_offpeak_s"])
        deltas = [statistics.median(items) for items in by_route.values()]
        dates = {p["date_astana"] for p in subset_contrasts}
        result = dict(comparison="tomtom-dgis", length_group=subset, status="not_run_insufficient_matched_routes_or_dates",
                      method="exact_two_sided_signed_rank_of_route_median_route_day_contrasts", n_route_days=len(subset_contrasts),
                      n_routes=len(by_route), n_dates=len(dates), median_peak_minus_offpeak_s=statistics.median(deltas) if deltas else None,
                      p_value=None, rank_biserial=None, n_nonzero=None, w_plus=None, w_minus=None,
                      interpretation="Primary subset=all; length subsets exploratory. No ground-truth accuracy claim. No pilot inference.")
        if not primary:
            result["status"] = "not_run_no_primary_comparison_pairs"
        elif all(p["phase"] == "pilot" for p in primary):
            result["status"] = "not_run_pilot_only"
        elif len(by_route) >= 8 and len(dates) >= 2:
            result.update(exact_signed_rank(deltas))
            result["status"] = "estimated_conditional_on_selected_routes_and_dates"
        outcomes.append(result)
    return contrasts, outcomes


def dictionary_table(raw_rows, observations, pairs):
    info = {
        "timestamp_utc": ("interval", "ISO 8601 UTC", "Timezone-aware request start"),
        "timestamp_astana": ("interval", "ISO 8601 Asia/Almaty", "Same instant in Astana; UTC+05 for study dates"),
        "cycle_id": ("nominal", "identifier", "One collection cycle"), "route_id": ("nominal", "identifier", "Fixed OD route register"),
        "provider": ("nominal", "category", "tomtom | dgis | ors"), "duration_s": ("ratio", "seconds", ">0 finite; estimated travel time"),
        "duration_no_traffic_s": ("ratio", "seconds", ">0 finite when returned; provider-specific baseline, not interchangeable"),
        "distance_m": ("ratio", "metres", ">0 finite; provider returned route length"), "status": ("nominal", "category", "ok | http_error | transport_error | invalid_response"),
        "http_code": ("nominal", "HTTP status", "100..599 or missing if transport failed"), "error": ("nominal", "text", "Sanitized collector error; blank on success"),
        "request_id": ("nominal", "identifier", "Unique collector request ID"), "latency_s": ("ratio", "seconds", ">=0 finite"),
        "geometry_file": ("nominal", "relative path", "Saved geometry; optional, absence not a synthetic geometry"),
        "raw_file": ("nominal", "relative path", "Saved sanitized provider response"), "phase": ("nominal", "category", "pilot | scheduled | manual"),
        "source_file": ("nominal", "path", "Actual input CSV, repository-relative when inside the root"), "source_line": ("ratio", "line", ">=2, header is line 1"),
        "period": ("nominal", "category", "peak [17:30,19:30); offpeak [10:00,12:00) or [22:00,24:00); other"),
        "date_astana": ("interval", "local date", "Calendar date in Asia/Almaty"), "speed_kmh": ("ratio", "km/h", "distance_m/duration_s*3.6; valid 2..200 inclusive"),
        "usable": ("nominal", "boolean", "Passed all rules needed for pairing"), "exclusion_reason": ("nominal", "codes", "Semicolon-separated validation flags; blank if usable"),
        "duplicate_kind": ("nominal", "category", "exact | conflicting_identity | blank"),
        "provider_a": ("nominal", "category", "Order: tomtom, dgis, ors"), "provider_b": ("nominal", "category", "Order: tomtom, dgis, ors"),
        "comparison_role": ("nominal", "category", "primary = tomtom-dgis; control = any comparison with ORS"),
        "time_gap_s": ("ratio", "seconds", "Absolute request-start separation; paired <=60"),
        "duration_a_s": ("ratio", "seconds", ">0"), "duration_b_s": ("ratio", "seconds", ">0"),
        "distance_a_m": ("ratio", "metres", ">0"), "distance_b_m": ("ratio", "metres", ">0"),
        "mean_duration_s": ("ratio", "seconds", "(A+B)/2"), "signed_diff_s": ("interval", "seconds", "A-B; positive means A estimates longer"),
        "abs_diff_s": ("ratio", "seconds", "|A-B|; lower indicates closer agreement"),
        "relative_diff_pct": ("interval", "%", "100*(A-B)/((A+B)/2); -200..200"),
        "smape_pct": ("ratio", "%", "100*|A-B|/((A+B)/2); 0..200, lower is closer agreement"),
        "length_diff_pct": ("ratio", "%", "100*|lengthA-lengthB|/((lengthA+lengthB)/2)"),
        "length_comparable": ("nominal", "boolean", "Length difference <=5%; proxy only, does not prove identical paths"),
    }
    output = []
    for dataset, data, fields in [("raw", raw_rows, RAW_FIELDS), ("observations", observations, OBS_FIELDS), ("pairs", pairs, PAIR_FIELDS)]:
        for field in fields:
            missing = sum(row.get(field) is None or row.get(field) == "" for row in data)
            kind, unit, considered = info[field]
            output.append(dict(dataset=dataset, column=field, type=kind, unit=unit, considered_values_or_range=considered,
                               n_rows=len(data), missing_count=missing, missing_pct=100*missing/len(data) if data else None))
    return output


def write_csv(path, rows, fieldnames):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run(root, seed=2424, replicates=2000, raw_dir=None):
    root = Path(root).resolve()
    chosen_raw = Path(raw_dir) if raw_dir is not None else Path("data/raw")
    if not chosen_raw.is_absolute():
        chosen_raw = root / chosen_raw
    chosen_raw = chosen_raw.resolve()
    if raw_dir is not None and not chosen_raw.is_dir():
        raise FileNotFoundError(f"Requested raw input directory does not exist: {chosen_raw}")
    raw_rows, manifest = read_raw(chosen_raw, root)
    observations, cleaning = validate_rows(raw_rows)
    pairs, pair_exclusions = build_pairs(observations)
    metrics = metric_table(pairs, seed, replicates)
    contrasts, inference = inference_table(pairs)
    unique = [r for r in observations if r["duplicate_kind"] != "exact"]
    quality = []
    for phase, provider in sorted({(r["phase"], r["provider"]) for r in unique}):
        group = [r for r in unique if r["phase"] == phase and r["provider"] == provider]
        failed = sum("request_failed" in r["_reasons"] for r in group)
        quality.append(dict(phase=phase, provider=provider, n_requests=len(group), n_http_ok=len(group)-failed,
                            n_failed=failed, failure_pct=100*failed/len(group), n_usable=sum(r["usable"] for r in group)))
    coverage_counts = Counter((r["phase"], r["date_astana"], r["period"], r["provider"]) for r in observations if r["usable"])
    coverage = [dict(phase=k[0], date_astana=k[1], period=k[2], provider=k[3], n_usable=v) for k, v in sorted(coverage_counts.items())]
    output = root / "results"
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "observations.csv", observations, OBS_FIELDS)
    write_csv(output / "pairs.csv", pairs, PAIR_FIELDS)
    write_csv(output / "metrics.csv", metrics, METRIC_FIELDS)
    write_csv(output / "cleaning_log.csv", cleaning, ["step", "rule", "rows_before", "rows_after", "affected_rows", "finding"])
    write_csv(output / "data_dictionary.csv", dictionary_table(raw_rows, observations, pairs), ["dataset", "column", "type", "unit", "considered_values_or_range", "n_rows", "missing_count", "missing_pct"])
    write_csv(output / "pair_exclusions.csv", pair_exclusions, ["phase", "cycle_id", "route_id", "provider_a", "provider_b", "time_gap_s", "reason"])
    write_csv(output / "request_quality.csv", quality, ["phase", "provider", "n_requests", "n_http_ok", "n_failed", "failure_pct", "n_usable"])
    write_csv(output / "coverage.csv", coverage, ["phase", "date_astana", "period", "provider", "n_usable"])
    write_csv(output / "route_day_contrasts.csv", contrasts, ["length_group", "route_id", "date_astana", "n_peak", "n_offpeak", "peak_median_abs_s", "offpeak_median_abs_s", "peak_minus_offpeak_s"])
    write_csv(output / "inference.csv", inference, list(inference[0]))
    primary = [p for p in pairs if p["comparison_role"] == "primary"]
    primary_periods = Counter(p["period"] for p in primary)
    times = [r["_time"] for r in observations if "_time" in r]
    summary = dict(schema_version="1.0", generated_at_utc=datetime.now(timezone.utc).isoformat(),
                   input_files=manifest, n_raw_rows=len(raw_rows), n_exact_duplicates=sum(r["duplicate_kind"] == "exact" for r in observations),
                   n_unique_requests=len(unique), n_usable_observations=sum(r["usable"] for r in observations),
                   n_failed_requests=sum("request_failed" in r["_reasons"] for r in unique), n_pairs=len(pairs), n_primary_pairs=len(primary),
                   n_primary_length_comparable=sum(p["length_comparable"] for p in primary),
                   primary_length_comparable_pct=100*sum(p["length_comparable"] for p in primary)/len(primary) if primary else None,
                   primary_pairs_by_period={period: primary_periods[period] for period in ["peak", "offpeak", "other"]},
                   primary_pairs_by_phase=dict(Counter(p["phase"] for p in primary)),
                   n_routes=len({r["route_id"] for r in unique if r["route_id"]}), n_cycles=len({(r["phase"], r["cycle_id"]) for r in unique if r["cycle_id"]}),
                   first_request_utc=min(times).isoformat() if times else None, last_request_utc=max(times).isoformat() if times else None,
                   max_primary_time_gap_s=max((p["time_gap_s"] for p in primary), default=None),
                   n_pair_time_exclusions=sum(p["reason"] == "time_gap_over_60s" for p in pair_exclusions),
                   n_pair_boundary_exclusions=sum(p["reason"] == "period_or_date_boundary" for p in pair_exclusions),
                   primary_metrics=[m for m in metrics if m["comparison_role"] == "primary" and m["length_group"] == "all"],
                   request_quality=quality, inference=inference,
                   rules={"timezone": "Asia/Almaty", "period_intervals": {"peak": [["17:30", "19:30"]], "offpeak": [["10:00", "12:00"], ["22:00", "24:00"]]},
                          "period_end_exclusive": True, "max_pair_gap_s": 60, "length_difference_denominator": "pair_mean", "length_comparable_max_pct": 5,
                          "length_comparability_proves_same_geometry": False, "min_speed_kmh": 2, "max_speed_kmh": 200,
                          "bootstrap_unit": "route", "bootstrap_replicates": replicates, "bootstrap_seed": seed,
                          "bootstrap_min_routes": 8, "bootstrap_min_dates": 2, "pilot_inference": False,
                          "accuracy_estimable": False, "ors_is_traffic_aware": False},
                   versions={"python": platform.python_version(), "platform": platform.platform(),
                             "process_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    try:
        summary["versions"]["tzdata"] = importlib.metadata.version("tzdata")
    except importlib.metadata.PackageNotFoundError:
        summary["versions"]["tzdata"] = "operating_system_timezone_database"
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (output / "version_manifest.json").write_text(json.dumps({"generated_at_utc": summary["generated_at_utc"], "inputs": manifest, "versions": summary["versions"], "rules": summary["rules"]}, indent=2), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--raw-dir", type=Path, default=None,
                        help="Read only this CSV directory; relative paths resolve under --root. Default: data/raw")
    parser.add_argument("--seed", type=int, default=2424)
    parser.add_argument("--bootstrap-replicates", type=int, default=2000)
    args = parser.parse_args()
    if args.bootstrap_replicates < 100:
        parser.error("Use at least 100 bootstrap replicates.")
    result = run(args.root, args.seed, args.bootstrap_replicates, raw_dir=args.raw_dir)
    print(json.dumps({k: result[k] for k in ["n_raw_rows", "n_usable_observations", "n_pairs", "n_primary_pairs", "primary_pairs_by_period"]}))


if __name__ == "__main__":
    main()
