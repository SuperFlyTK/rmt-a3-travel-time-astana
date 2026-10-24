#!/usr/bin/env python3
"""Export the study's data-only public dataset without network or git access."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ASTANA = timezone(timedelta(hours=5))  # Correct for every date in this study.
RAW_FIELDS = "timestamp_utc timestamp_astana cycle_id route_id provider duration_s duration_no_traffic_s distance_m status http_code error request_id latency_s geometry_file raw_file phase".split()
OBS_FIELDS = [field for field in RAW_FIELDS if field not in {"geometry_file", "raw_file"}]
DERIVED_FIELDS = "period date_astana speed_kmh usable exclusion_reason duplicate_kind".split()
GOOGLE_FIELDS = "cycle_id window_id phase period timestamp_utc timestamp_astana route_id provider http_code status".split()
METRIC_FIELDS = "n_attempted_pairs n_google_ok n_ors_ok max_request_start_gap_s n_valid_pairs median_signed_difference_s median_absolute_difference_s mae_s rmse_s smape_pct iqr_signed_difference_s n_length_comparable length_comparable_pct".split()
SUMMARY_FIELDS = "cycle_id window_id phase period window_start_utc window_end_utc start_utc end_utc".split() + METRIC_FIELDS + ["completion_reason"]
WINDOW_FIELDS = "window_id period window_start_utc window_end_utc window_start_astana window_end_astana planned_pairs status actual_start_utc actual_end_utc n_attempted_pairs n_google_ok n_ors_ok n_valid_pairs completion_reason".split()
DICTIONARY_FIELDS = ["file", "column", "type", "unit", "description"]
OUTPUT_NAMES = {
    "routes.csv", "ors_observations.csv", "ors_processed.csv", "google_request_status.csv",
    "google_ors_summary.csv", "collection_windows.csv", "selected_endpoints.csv",
    "ors_pilot_routes.geojson", "data_dictionary.csv", "README.md",
}


def stamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Timestamp lacks a UTC offset")
    return result.astimezone(timezone.utc)


def period_at(instant):
    local = instant.astimezone(ASTANA)
    minute = local.hour * 60 + local.minute + local.second / 60
    if 1050 <= minute < 1170:
        return "peak"
    if 600 <= minute < 720 or 1320 <= minute < 1440:
        return "offpeak"
    return "other"


def finite(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class Source:
    def __init__(self, root):
        self.root = root
        self.hashes = {}

    def read(self, relative):
        path = self.root / relative
        payload = path.read_bytes()
        self.hashes[path] = hashlib.sha256(payload).digest()
        return payload

    def table(self, relative, required=()):
        reader = csv.DictReader(io.StringIO(self.read(relative).decode("utf-8-sig")))
        fields = reader.fieldnames or []
        if len(set(fields)) != len(fields) or set(required) - set(fields):
            raise ValueError(f"Invalid columns in {relative}")
        rows = list(reader)
        if any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError(f"Incomplete CSV row in {relative}")
        return fields, rows

    def verify_unchanged(self):
        for path, digest in self.hashes.items():
            if hashlib.sha256(path.read_bytes()).digest() != digest:
                raise ValueError(f"Source changed during export: {path.name}; retry the export")


def csv_bytes(rows, fields):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows({field: row.get(field, "") for field in fields} for row in rows)
    return stream.getvalue().encode("utf-8")


def process_ors(raw):
    """Apply the recorded ORS screening rules to every retained study phase."""
    rows, fingerprints = [], set()
    for original in raw:
        row = {field: original[field] for field in OBS_FIELDS}
        row.update(period="", date_astana="", speed_kmh="", usable="false", exclusion_reason="", duplicate_kind="")
        reasons = []
        fingerprint = tuple(original[field] for field in RAW_FIELDS)
        if fingerprint in fingerprints:
            row["duplicate_kind"] = "exact"
            reasons.append("exact_duplicate")
        fingerprints.add(fingerprint)
        try:
            instant = stamp(row["timestamp_utc"])
            if abs((instant - stamp(row["timestamp_astana"])).total_seconds()) > 1:
                reasons.append("timestamp_disagreement")
            row.update(period=period_at(instant), date_astana=instant.astimezone(ASTANA).date().isoformat())
        except (ValueError, TypeError, OverflowError):
            reasons.append("invalid_timestamp")
        if row["provider"] != "ors":
            reasons.append("unknown_provider")
        if not all(row[field] for field in ("cycle_id", "route_id", "phase")):
            reasons.append("missing_identity")
        if row["phase"] not in {"pilot", "scheduled", "manual", "exploratory", "followup"}:
            reasons.append("unknown_phase")
        if row["status"].lower() != "ok" or row["http_code"] != "200":
            reasons.append("request_failed")
        duration, distance = finite(row["duration_s"]), finite(row["distance_m"])
        if duration is None or duration <= 0:
            reasons.append("invalid_duration")
        if distance is None or distance <= 0:
            reasons.append("invalid_distance")
        if duration is not None and duration > 0 and distance is not None and distance > 0:
            speed = distance / duration * 3.6
            row["speed_kmh"] = repr(speed)
            if not 2 <= speed <= 200:
                reasons.append("speed_outside_2_200_kmh")
        latency = finite(row["latency_s"])
        if row["latency_s"] and (latency is None or latency < 0):
            reasons.append("invalid_latency")
        row["_reasons"] = reasons
        rows.append(row)
    identities, requests = defaultdict(list), defaultdict(list)
    for row in rows:
        if row["duplicate_kind"] != "exact":
            identities[tuple(row[field] for field in ("phase", "cycle_id", "route_id", "provider"))].append(row)
            if row["request_id"]:
                requests[row["request_id"]].append(row)
    for group in identities.values():
        if len(group) > 1:
            for row in group:
                row["duplicate_kind"] = "conflicting_identity"
                row["_reasons"].append("conflicting_duplicate_identity")
    for group in requests.values():
        if len(group) > 1:
            for row in group:
                row["_reasons"].append("duplicate_request_id")
    for row in rows:
        reasons = row.pop("_reasons")
        row["usable"] = "false" if reasons else "true"
        row["exclusion_reason"] = ";".join(reasons)
    return rows


def google_data(source, protocol, ors):
    windows = {item["id"]: item for item in protocol["windows"]}
    summaries = []
    for relative, phase in (("results/google_control_benchmark.csv", "exploratory"),
                            ("results/google_followup_benchmark.csv", "followup")):
        _, records = source.table(relative, ["cycle_id", "start_utc", "end_utc"] + METRIC_FIELDS)
        for original in records:
            row = dict(original)
            cycle = row["cycle_id"]
            row["phase"] = phase
            row["window_id"] = cycle if phase == "followup" else ""
            row.setdefault("period", period_at(stamp(row["start_utc"])))
            if phase == "followup":
                window = windows[cycle]
                if row["period"] != window["period"] or any(row[f"window_{part}_utc"] != window[f"{part}_utc"] for part in ("start", "end")):
                    raise ValueError("Follow-up aggregate disagrees with recorded window")
            summaries.append(row)
    if len({row["cycle_id"] for row in summaries}) != len(summaries):
        raise ValueError("Duplicate Google aggregate cycle")
    exploratory = [row for row in summaries if row["phase"] == "exploratory"]
    if len(exploratory) != 1:
        raise ValueError("Exploratory status records require exactly one source aggregate")
    statuses = []
    for relative, phase in (("data/exploratory/google_request_status.csv", "exploratory"),
                            ("data/followup/google_request_status.csv", "followup")):
        _, records = source.table(relative, ["timestamp_utc", "route_id", "provider", "http_code", "status"])
        for original in records:
            row = dict(original)
            instant = stamp(row["timestamp_utc"])
            cycle = row.get("cycle_id", exploratory[0]["cycle_id"])
            row.update(cycle_id=cycle, phase=phase, period=period_at(instant),
                       timestamp_astana=instant.astimezone(ASTANA).isoformat())
            row.setdefault("window_id", "")
            if phase == "followup":
                window = windows[cycle]
                if row["window_id"] != cycle or row["period"] != window["period"]:
                    raise ValueError("Follow-up status disagrees with recorded window")
                if not stamp(window["start_utc"]) <= instant < stamp(window["end_utc"]):
                    raise ValueError("Google follow-up timestamp is outside its recorded window")
            statuses.append(row)
    identities = [(row["cycle_id"], row["route_id"], row["provider"]) for row in statuses]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate Google request status identity")
    for row in summaries:
        group_g = [item for item in statuses if item["cycle_id"] == row["cycle_id"]]
        group_o = [item for item in ors if item["cycle_id"] == row["cycle_id"]]
        if len(group_g) != int(row["n_attempted_pairs"]) or len(group_o) != int(row["n_attempted_pairs"]):
            raise ValueError("Aggregate attempted count disagrees with archived status/ORS rows")
        if sum(item["status"] == "ok" for item in group_g) != int(row["n_google_ok"]):
            raise ValueError("Aggregate Google success count disagrees with status rows")
        if sum(item["status"] == "ok" for item in group_o) != int(row["n_ors_ok"]):
            raise ValueError("Aggregate ORS success count disagrees with observations")
    return sorted(statuses, key=lambda row: (row["timestamp_utc"], row["route_id"])), summaries


def collection_windows(protocol, summaries, statuses, ors, as_of):
    lookup = {row["cycle_id"]: row for row in summaries if row["phase"] == "followup"}
    rows = []
    for window in protocol["windows"]:
        cycle = window["id"]
        summary = lookup.get(cycle)
        actual_g = [row for row in statuses if row["cycle_id"] == cycle]
        actual_o = [row for row in ors if row["cycle_id"] == cycle]
        if summary:
            status = "completed" if summary.get("completion_reason") == "all_reserved_routes_attempted" else "partial"
        elif actual_g or actual_o:
            status = "in_progress" if as_of < stamp(window["end_utc"]) else "partial"
        else:
            status = "missed" if as_of >= stamp(window["end_utc"]) else "planned"
        row = dict(window_id=cycle, period=window["period"], window_start_utc=window["start_utc"],
                   window_end_utc=window["end_utc"], window_start_astana=stamp(window["start_utc"]).astimezone(ASTANA).isoformat(),
                   window_end_astana=stamp(window["end_utc"]).astimezone(ASTANA).isoformat(),
                   planned_pairs=str(protocol["routes_per_window"]), status=status)
        if summary:
            row.update(actual_start_utc=summary["start_utc"], actual_end_utc=summary["end_utc"])
            row.update({field: summary[field] for field in ("n_attempted_pairs", "n_google_ok", "n_ors_ok", "n_valid_pairs", "completion_reason")})
        elif actual_g or actual_o:
            times = [item["timestamp_utc"] for item in actual_g + actual_o]
            row.update(actual_start_utc=min(times, key=stamp), actual_end_utc=max(times, key=stamp),
                       n_attempted_pairs=str(len(actual_g)), n_google_ok=str(sum(item["status"] == "ok" for item in actual_g)),
                       n_ors_ok=str(sum(item["status"] == "ok" for item in actual_o)))
        rows.append(row)
    return rows


def make_dictionary(schemas):
    descriptions = {
        "timestamp_utc": ("datetime", "ISO 8601 UTC", "Original API request start, with timezone offset."),
        "timestamp_astana": ("datetime", "ISO 8601 UTC+05:00", "Request start in Astana time; original for ORS and converted from timestamp_utc for Google status."),
        "cycle_id": ("string", "identifier", "Original collection batch identifier; retain for provenance and repeated-route grouping."),
        "route_id": ("string", "identifier", "Directed route identifier joining routes.csv; reverse directions have different identifiers."),
        "provider": ("category", "", "ors or google_demo, exactly as recorded by the collector."),
        "duration_s": ("number", "seconds", "ORS estimated driving duration returned by the API; ORS supplies no live-traffic model in this study."),
        "duration_no_traffic_s": ("number or empty", "seconds", "Optional separate traffic-off baseline; empty for ORS because no separate baseline was returned. Empty does not mean zero."),
        "distance_m": ("number", "metres", "ORS routed distance returned by the API."),
        "status": ("category", "", "API result: ok, http_error, transport_error, or invalid_response; retained failures remain visible."),
        "http_code": ("integer or empty", "HTTP status", "Original HTTP response code; empty if no response code was available."),
        "error": ("string or empty", "", "Recorded request error description; empty on success."),
        "request_id": ("string", "identifier", "Original collector request identifier; joins raw and processed ORS rows."),
        "latency_s": ("number or empty", "seconds", "Elapsed API request time measured by the collector."),
        "phase": ("category", "", "pilot and scheduled identify the main ORS series; exploratory identifies the initial Google comparison; followup identifies planned Google-ORS windows. manual is reserved for a recorded manual trigger."),
        "period": ("category", "local time window", "peak [17:30,19:30); offpeak [10:00,12:00) or [22:00,24:00); other otherwise. Endpoints are exclusive; timezone UTC+05:00."),
        "date_astana": ("date", "YYYY-MM-DD", "Local date calculated from timestamp_utc at UTC+05:00; source CSV filenames may use the UTC date."),
        "speed_kmh": ("number or empty", "km/h", "distance_m / duration_s * 3.6 when both values are positive and finite."),
        "usable": ("boolean", "true/false", "Passes timestamp, provider, identity, phase, request-success, positive finite duration/distance, speed 2..200 km/h inclusive, nonnegative finite latency if present, and duplicate checks. Does not establish travel-time accuracy."),
        "exclusion_reason": ("string or empty", "semicolon-separated codes", "Validation flags: exact_duplicate; conflicting_duplicate_identity; duplicate_request_id; invalid_timestamp; timestamp_disagreement (over one second); unknown_provider; missing_identity; unknown_phase; request_failed; invalid_duration; invalid_distance; speed_outside_2_200_kmh; invalid_latency. Empty means no exclusions."),
        "duplicate_kind": ("category or empty", "", "exact for repeated original raw fields; conflicting_identity for repeated phase/cycle/route/provider among nonexact rows; empty otherwise. Repeated request_id also creates an exclusion."),
        "window_id": ("string or empty", "identifier", "Planned follow-up window identifier; joins collection_windows.csv. Empty for the exploratory comparison, which preceded the window protocol."),
        "window_start_utc": ("datetime or empty", "ISO 8601 UTC", "Inclusive planned window start; empty for the exploratory comparison."),
        "window_end_utc": ("datetime or empty", "ISO 8601 UTC", "Exclusive planned window end; empty for the exploratory comparison."),
        "start_utc": ("datetime", "ISO 8601 UTC", "Original start of the aggregate comparison batch, before its first request."),
        "end_utc": ("datetime", "ISO 8601 UTC", "Original completion time of the aggregate comparison batch."),
        "n_attempted_pairs": ("integer or empty", "route pairs", "Number of paired Google/ORS attempts in the batch; empty for unstarted planned windows."),
        "n_google_ok": ("integer or empty", "requests", "Google requests recorded with status ok in this batch."),
        "n_ors_ok": ("integer or empty", "requests", "ORS requests recorded with status ok in this batch."),
        "max_request_start_gap_s": ("number", "seconds", "Maximum absolute difference between the Google and ORS request start times across attempted pairs."),
        "n_valid_pairs": ("integer or empty", "route pairs", "Pairs accepted by the original Google-ORS comparison; numeric aggregate statistics require at least five. Empty means no aggregate was recorded."),
        "median_signed_difference_s": ("number or empty", "seconds", "Median of Google duration minus ORS duration over valid pairs; positive means Google estimated longer."),
        "median_absolute_difference_s": ("number or empty", "seconds", "Median absolute Google-minus-ORS duration difference over valid pairs."),
        "mae_s": ("number or empty", "seconds", "Mean absolute Google-minus-ORS duration difference; measures disagreement, not error against observed journeys."),
        "rmse_s": ("number or empty", "seconds", "Square root of mean squared Google-minus-ORS duration difference."),
        "smape_pct": ("number or empty", "percent", "Mean of 100 * abs(Google duration - ORS duration) / ((Google duration + ORS duration) / 2)."),
        "iqr_signed_difference_s": ("number or empty", "seconds", "75th minus 25th percentile of signed Google-minus-ORS differences; inclusive linearly interpolated quartiles."),
        "n_length_comparable": ("integer or empty", "route pairs", "Valid pairs with 100 * abs(Google distance - ORS distance) / mean(Google distance, ORS distance) <= 5. Does not prove identical geometry."),
        "length_comparable_pct": ("number or empty", "percent", "100 * n_length_comparable / n_valid_pairs."),
        "completion_reason": ("category or empty", "", "Original follow-up stop reason: all_reserved_routes_attempted, global_ors_halt, google_followup_halt, or window_or_runtime_ended. Empty for the exploratory source and unstarted windows."),
        "window_start_astana": ("datetime", "ISO 8601 UTC+05:00", "Planned inclusive window start converted to local time."),
        "window_end_astana": ("datetime", "ISO 8601 UTC+05:00", "Planned exclusive window end converted to local time."),
        "planned_pairs": ("integer", "route pairs", "Number of route pairs planned for this window."),
        "actual_start_utc": ("datetime or empty", "ISO 8601 UTC", "Recorded aggregate start; earliest request start if a batch has records but no aggregate; empty for unstarted windows."),
        "actual_end_utc": ("datetime or empty", "ISO 8601 UTC", "Recorded aggregate end; latest recorded request start for an unfinished batch; empty for unstarted windows."),
        "name": ("string", "", "Route label describing the two public endpoint vicinities."),
        "origin_lat": ("number", "decimal degrees", "Origin latitude in WGS84 from the linked OpenStreetMap node."),
        "origin_lon": ("number", "decimal degrees", "Origin longitude in WGS84 from the linked OpenStreetMap node."),
        "destination_lat": ("number", "decimal degrees", "Destination latitude in WGS84 from the linked OpenStreetMap node."),
        "destination_lon": ("number", "decimal degrees", "Destination longitude in WGS84 from the linked OpenStreetMap node."),
        "expected_length_m": ("number", "metres", "Straight-line spherical haversine lower bound using Earth radius 6371008.8 m; not an observed or predicted road-route length."),
        "expected_length_basis": ("string", "", "Original formula description for expected_length_m."),
        "origin_source": ("URL", "", "Public OpenStreetMap node source for the origin coordinate."),
        "destination_source": ("URL", "", "Public OpenStreetMap node source for the destination coordinate."),
        "endpoint_id": ("string", "identifier", "Selected public endpoint identifier."),
        "label": ("string", "", "Endpoint vicinity label used in route names."),
        "lat": ("number", "decimal degrees", "Selected OpenStreetMap node latitude in WGS84."),
        "lon": ("number", "decimal degrees", "Selected OpenStreetMap node longitude in WGS84."),
        "source_url": ("URL", "", "OpenStreetMap node from which the endpoint coordinate was read."),
        "supporting_way_url": ("URL", "", "OpenStreetMap road way supporting the endpoint selection."),
        "osm_road_name": ("string", "", "Road name from the source OpenStreetMap tags."),
        "osm_highway": ("category", "OSM highway tag", "Road classification recorded in OpenStreetMap."),
        "selection_rule": ("string", "", "Recorded rule used to select the road node near the landmark or stop."),
        "landmark_source": ("URL", "", "Public OpenStreetMap feature used as the endpoint selection hint."),
        "distance_to_selection_hint_m": ("number", "metres", "Distance from selected road node to the original selection hint; not route distance."),
        "caution": ("string", "", "Original endpoint limitations; locations describe road vicinities rather than verified building entrances."),
        "file": ("string", "filename", "Data file to which the documented column belongs."),
        "column": ("string", "column or JSON path", "Exact CSV column name or GeoJSON property path."),
        "type": ("string", "", "Field value type and whether an empty value is allowed."),
        "unit": ("string or empty", "", "Measurement unit or identifier convention."),
        "description": ("string", "", "Definition, derivation, and interpretation of the field."),
    }
    rows = []
    for filename, fields in schemas.items():
        for field in fields:
            kind, unit, description = descriptions[field]
            if filename == "collection_windows.csv" and field == "status":
                description = "Status at the latest archived study timestamp: completed (all planned routes attempted); partial (stopped or incomplete); in_progress (records without aggregate before window end); missed (window elapsed without records); planned (unstarted and not elapsed)."
            rows.append(dict(file=filename, column=field, type=kind, unit=unit, description=description))
    for field, kind, unit, description in (
        ("type", "string", "GeoJSON type", "FeatureCollection containing the twenty pilot-1 ORS route geometries."),
        ("features[].type", "string", "GeoJSON type", "Feature."),
        ("features[].properties.route_id", "string", "identifier", "Directed route identifier joining routes.csv."),
        ("features[].properties.cycle_id", "string", "identifier", "Original cycle identifier pilot-1."),
        ("features[].properties.request_id", "string", "identifier", "Original ORS request identifier joining ors_observations.csv."),
        ("features[].properties.timestamp_utc", "datetime", "ISO 8601 UTC", "Original start time of the ORS request that returned this geometry."),
        ("features[].geometry.type", "string", "GeoJSON type", "LineString, exactly as returned by ORS."),
        ("features[].geometry.coordinates", "array", "WGS84 decimal degrees", "Original ORS [longitude, latitude] positions in route order; geometry is from the first pilot cycle only."),
    ):
        rows.append(dict(file="ors_pilot_routes.geojson", column=field, type=kind, unit=unit, description=description))
    return rows


def make_readme(ors, processed, statuses, summaries, windows, as_of, route_count, endpoint_count):
    phases = Counter(row["phase"] for row in ors)
    dates = sorted({stamp(row["timestamp_utc"]).astimezone(ASTANA).date().isoformat() for row in ors})
    google_dates = sorted({stamp(row["timestamp_utc"]).astimezone(ASTANA).date().isoformat() for row in statuses})
    first = min(stamp(row["timestamp_utc"]) for row in ors)
    completed = sum(row["status"] == "completed" for row in windows)
    rows = [
        "# Astana travel-time estimates",
        "",
        "RMT Assignment 3, group SE-2424. Yerdaulet Akhanov, Syrym Shadiyarbek and Akylbek Sabyrzhan.",
        "",
        f"Automated API requests sampled {route_count} directed routes between {endpoint_count} public locations in Astana. "
        "The comparison uses Google Routes with `TRAFFIC_AWARE_OPTIMAL` and openrouteservice (ORS) without live traffic. "
        "These are provider estimates; the study did not observe journeys on the road.",
        "",
        f"This dataset contains {len(ors)} ORS observations: {phases['pilot']} pilot, {phases['scheduled']} scheduled, "
        f"{phases['exploratory']} exploratory and {phases['followup']} follow-up records. "
        f"ORS local dates: {', '.join(dates)}. The first request started at {first.isoformat()}; "
        f"the latest archived study timestamp is {as_of.isoformat()} ({as_of.astimezone(ASTANA).isoformat()} in Astana). "
        "Window status describes the evidence available at that timestamp.",
        "",
        f"Google has {len(statuses)} request-status records and {len(summaries)} aggregate comparison batches, "
        f"covering {sum(int(row['n_valid_pairs']) for row in summaries)} valid route pairs on local dates {', '.join(google_dates)}. "
        f"The follow-up plan has {len(windows)} windows, of which {completed} have attempted every planned route. "
        "The exploratory batch has an empty window_id and remains separate from the planned comparisons.",
        "",
        "| File | Contents |",
        "| --- | --- |",
        f"| `routes.csv` | {route_count} directed routes, coordinates and OpenStreetMap source links |",
        f"| `ors_observations.csv` | {len(ors)} original ORS measurement records |",
        f"| `ors_processed.csv` | The same {len(processed)} records with local period/date and quality checks |",
        f"| `google_request_status.csv` | {len(statuses)} Google request outcomes and timestamps |",
        f"| `google_ors_summary.csv` | {len(summaries)} Google-ORS batches with all recorded aggregate statistics |",
        f"| `collection_windows.csv` | {len(windows)} planned follow-up windows and recorded completion status |",
        f"| `selected_endpoints.csv` | {endpoint_count} endpoint sources and selection rules |",
        f"| `ors_pilot_routes.geojson` | {route_count} route geometries from the first ORS pilot cycle |",
        "| `data_dictionary.csv` | Definitions and units for every CSV column and GeoJSON field |",
        "",
        "Raw ORS measurement values, timestamps and collection identifiers retain their original text. "
        "The compact raw table omits file-path fields for response and geometry files that are not part of this release. "
        "Processed records add derived fields without replacing any raw measurement. "
        f"{sum(row['usable'] == 'true' for row in processed)} of {len(processed)} ORS rows pass the published checks. "
        "Blank optional values mean unavailable, never zero. All CSV files use UTF-8 and comma delimiters.",
        "",
        "Astana uses UTC+05:00 for these study dates. Peak is 17:30 to 19:30; off-peak is 10:00 to 12:00 "
        "or 22:00 to 24:00. Each interval includes its start and excludes its end. "
        "The processed table checks request success, positive finite duration and distance, speed between 2 and 200 km/h, "
        "timestamps and duplicate identities. It retains excluded rows with reasons. "
        "Google follow-up pairs required successful responses within their planned window and request starts no more than 60 seconds apart.",
        "",
        "Google individual durations, distances and geometry are absent because the study retained only aggregate statistics and request status. "
        "The Google aggregates therefore cannot be recalculated from individual archived responses. "
        "Signed differences are Google minus ORS. Repeated routes and batches share locations; request counts are not independent sample counts. "
        "Provider differences describe disagreement and do not establish accuracy or isolate the effect of traffic.",
        "",
        "Route coordinates and endpoint links identify existing OpenStreetMap road nodes near public landmarks. "
        "They do not establish building entrances. `expected_length_m` is the spherical straight-line lower bound, "
        "while `distance_m` is the route length returned by ORS. The pilot GeoJSON preserves the original ORS coordinates; "
        "it does not represent Google routes or prove that two providers chose the same path.",
        "",
        "ORS attribution: © openrouteservice by HeiGIT | Data from OpenStreetMap. "
        "See [openrouteservice](https://openrouteservice.org/), its [terms](https://account.heigit.org/info/tos), "
        "and [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). "
        "OpenStreetMap attribution: © OpenStreetMap contributors; see the "
        "[copyright page](https://www.openstreetmap.org/copyright) and [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/). "
        "Individual source URLs appear in the route and endpoint tables. "
        "Google source: [Routes API](https://developers.google.com/maps/documentation/routes/overview) and "
        "[traffic preferences](https://developers.google.com/maps/documentation/routes/config_trade_offs). "
        "Provider terms apply to the respective material; this repository does not assign a single license to all sources.",
        "",
    ]
    return "\n".join(rows).encode("utf-8")


def ensure_no_secrets(root, payloads):
    secrets = set()
    envpath = root / ".env"
    lines = envpath.read_text(encoding="utf-8-sig").splitlines() if envpath.exists() else []
    candidates = list(os.environ.items())
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            candidates.append((name.strip(), value.strip().strip("\"'")))
    for name, value in candidates:
        if re.search(r"(?:KEY|TOKEN|SECRET|PASSWORD)$", name, re.I) and len(value) >= 8:
            secrets.add(value.encode("utf-8"))
    credential_patterns = [rb"AIza[0-9A-Za-z_-]{30,}", rb"gh[pousr]_[0-9A-Za-z]{20,}", rb"github_pat_[0-9A-Za-z_]{20,}", rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"]
    for filename, payload in payloads.items():
        if any(secret in payload for secret in secrets) or any(re.search(pattern, payload) for pattern in credential_patterns):
            raise ValueError(f"Credential check failed for {filename}; no files were published")


def export(root, output):
    root, output = root.resolve(), output.resolve()
    if root == output or root in output.parents:
        raise ValueError("Output must be outside the operational source directory")
    if output.exists() and any(path.name not in OUTPUT_NAMES | {".git"} for path in output.iterdir()):
        raise ValueError("Output contains files outside the public dataset allowlist")
    source = Source(root)
    route_fields, routes = source.table("data/routes.csv", ["route_id"])
    endpoint_fields, endpoints = source.table("data/sources/selected_endpoints.csv", ["endpoint_id"])
    if len({row["route_id"] for row in routes}) != len(routes):
        raise ValueError("Duplicate route identifier")
    raw = []
    raw_paths = sorted((root / "data/raw").glob("*.csv"))
    for path in raw_paths + [root / "data/exploratory/ors/observations.csv", root / "data/followup/ors/observations.csv"]:
        _, records = source.table(path.relative_to(root), RAW_FIELDS)
        if any(row["provider"] != "ors" for row in records):
            raise ValueError("Unexpected non-ORS raw provider; review the public export schema")
        raw.extend(records)
    if not raw:
        raise ValueError("No ORS observations to export")
    raw.sort(key=lambda row: (row["timestamp_utc"], row["route_id"], row["request_id"]))
    route_ids = {row["route_id"] for row in routes}
    if any(row["route_id"] not in route_ids for row in raw):
        raise ValueError("Observation refers to an unknown route")
    processed = process_ors(raw)
    protocol = json.loads(source.read("docs/google_followup_protocol.json"))
    statuses, summaries = google_data(source, protocol, raw)
    if any(row["route_id"] not in route_ids for row in statuses):
        raise ValueError("Google status refers to an unknown route")
    as_of = max([stamp(row["timestamp_utc"]) for row in raw + statuses] + [stamp(row["end_utc"]) for row in summaries])
    windows = collection_windows(protocol, summaries, statuses, raw, as_of)
    features = []
    pilot = sorted((row for row in raw if row["cycle_id"] == "pilot-1"), key=lambda row: row["route_id"])
    if {row["route_id"] for row in pilot} != route_ids or len(pilot) != len(routes):
        raise ValueError("First pilot cycle must contain exactly one geometry for each route")
    for row in pilot:
        path = root / row["geometry_file"]
        if not path.resolve().is_relative_to(root):
            raise ValueError("Geometry path leaves source directory")
        geometry = json.loads(source.read(path.relative_to(root)))
        if geometry.get("type") != "LineString" or not geometry.get("coordinates"):
            raise ValueError("Pilot geometry is not a nonempty LineString")
        features.append(dict(type="Feature", properties={field: row[field] for field in ("route_id", "cycle_id", "request_id", "timestamp_utc")}, geometry=geometry))
    tables = {
        "routes.csv": (route_fields, routes),
        "ors_observations.csv": (OBS_FIELDS, raw),
        "ors_processed.csv": (OBS_FIELDS + DERIVED_FIELDS, processed),
        "google_request_status.csv": (GOOGLE_FIELDS, statuses),
        "google_ors_summary.csv": (SUMMARY_FIELDS, summaries),
        "collection_windows.csv": (WINDOW_FIELDS, windows),
        "selected_endpoints.csv": (endpoint_fields, endpoints),
    }
    schemas = {name: fields for name, (fields, _) in tables.items()}
    schemas["data_dictionary.csv"] = DICTIONARY_FIELDS
    tables["data_dictionary.csv"] = (DICTIONARY_FIELDS, make_dictionary(schemas))
    payloads = {name: csv_bytes(rows, fields) for name, (fields, rows) in tables.items()}
    payloads["ors_pilot_routes.geojson"] = (json.dumps(dict(type="FeatureCollection", features=features), ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    payloads["README.md"] = make_readme(raw, processed, statuses, summaries, windows, as_of, len(routes), len(endpoints))
    if set(payloads) != OUTPUT_NAMES:
        raise ValueError("Public export allowlist mismatch")
    # Round-trip assertions ensure projection did not change a raw value or count.
    for name, fields, records in (("ors_observations.csv", OBS_FIELDS, raw), ("routes.csv", route_fields, routes), ("selected_endpoints.csv", endpoint_fields, endpoints)):
        actual = list(csv.DictReader(io.StringIO(payloads[name].decode("utf-8"))))
        if actual != [{field: row[field] for field in fields} for row in records]:
            raise ValueError(f"Value fidelity check failed for {name}")
    summary_roundtrip = list(csv.DictReader(io.StringIO(payloads["google_ors_summary.csv"].decode("utf-8"))))
    if any(any(item[field] != original[field] for field in METRIC_FIELDS) for item, original in zip(summary_roundtrip, summaries)):
        raise ValueError("Aggregate numeric strings changed during export")
    ensure_no_secrets(root, payloads)
    source.verify_unchanged()
    output.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        target = output / name
        if target.exists() and target.read_bytes() == payload:
            continue
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=output, prefix=".export-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    return dict(files=sorted(payloads), routes=len(routes), ors_observations=len(raw),
                ors_usable=sum(row["usable"] == "true" for row in processed),
                ors_duplicates=sum(bool(row["duplicate_kind"]) for row in processed),
                google_status=len(statuses), google_batches=len(summaries),
                windows=Counter(row["status"] for row in windows),
                snapshot_utc=as_of.isoformat(), secrets_detected=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="Operational study source directory")
    parser.add_argument("--output", required=True, type=Path, help="Data-only destination or existing checkout")
    args = parser.parse_args()
    try:
        result = export(args.root, args.output)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Export stopped: {error}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
