"""Synthetic unit fixtures live only in temporary directories, never research data."""

import csv
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analysis.process import (RAW_FIELDS, basic_metrics, build_pairs, classify_period,
                              clustered_ci, exact_signed_rank, inference_table,
                              parse_time, run, validate_rows)


def row(provider="tomtom", instant="2026-10-24T12:40:00+00:00", cycle="c1", route="R01", duration=600, distance=5000, phase="pilot"):
    stamp = parse_time(instant)
    values = {field: "" for field in RAW_FIELDS}
    values.update(timestamp_utc=stamp.isoformat(), timestamp_astana=stamp.astimezone(timezone(timedelta(hours=5))).isoformat(),
                  cycle_id=cycle, route_id=route, provider=provider, duration_s=str(duration), distance_m=str(distance),
                  status="ok", http_code="200", request_id=f"{phase}-{cycle}-{route}-{provider}", latency_s="0.5", phase=phase,
                  source_file="unit-fixture.csv", source_line=2)
    return values


class AnalysisTests(unittest.TestCase):
    def test_half_open_local_period_boundaries(self):
        expected = {"17:29:59": "other", "17:30:00": "peak", "19:29:59": "peak", "19:30:00": "other",
                    "09:59:59": "other", "10:00:00": "offpeak", "11:59:59": "offpeak", "12:00:00": "other",
                    "21:59:59": "other", "22:00:00": "offpeak", "23:59:59": "offpeak", "00:00:00": "other"}
        for time, period in expected.items():
            with self.subTest(time=time):
                self.assertEqual(classify_period(parse_time(f"2026-10-24T{time}+05:00")), period)

    def test_naive_and_mismatched_timestamps_rejected(self):
        bad = row()
        bad["timestamp_utc"] = "2026-10-24T12:40:00"
        rows, _ = validate_rows([bad])
        self.assertFalse(rows[0]["usable"])
        self.assertIn("invalid_timestamp", rows[0]["exclusion_reason"])
        bad = row()
        bad["timestamp_astana"] = "2026-10-24T12:40:00+05:00"
        self.assertFalse(validate_rows([bad])[0][0]["usable"])

    def test_exact_duplicate_once_but_conflicting_identity_all_excluded(self):
        a = row()
        rows, _ = validate_rows([a, dict(a)])
        self.assertEqual(sum(r["usable"] for r in rows), 1)
        self.assertEqual(rows[1]["duplicate_kind"], "exact")
        changed = dict(a, duration_s="601")
        rows, _ = validate_rows([a, changed])
        self.assertFalse(any(r["usable"] for r in rows))
        self.assertTrue(all(r["duplicate_kind"] == "conflicting_identity" for r in rows))

    def test_speed_limits_and_nonfinite_values(self):
        for speed, accepted in [(1.99, False), (2, True), (200, True), (200.1, False)]:
            observation = row(duration=3600, distance=speed*1000)
            self.assertEqual(validate_rows([observation])[0][0]["usable"], accepted)
        for invalid in ["NaN", "inf", "0", "-1", ""]:
            self.assertFalse(validate_rows([row(duration=invalid)])[0][0]["usable"])

    def test_failed_requests_kept_but_not_paired(self):
        failed = row(provider="dgis")
        failed.update(status="http_error", http_code="429", duration_s="", distance_m="")
        rows, _ = validate_rows([row(), failed])
        self.assertEqual(len(rows), 2)
        self.assertEqual(build_pairs(rows)[0], [])
        self.assertIn("request_failed", rows[1]["exclusion_reason"])

    def test_pairing_time_cycle_boundary_and_provider_order(self):
        values = [row(), row("dgis", instant="2026-10-24T12:41:00+00:00")]
        pairs, exclusions = build_pairs(validate_rows(values)[0])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["time_gap_s"], 60)
        self.assertEqual((pairs[0]["provider_a"], pairs[0]["provider_b"]), ("tomtom", "dgis"))
        values[1] = row("dgis", instant="2026-10-24T12:41:01+00:00")
        pairs, exclusions = build_pairs(validate_rows(values)[0])
        self.assertFalse(pairs)
        self.assertEqual(exclusions[0]["reason"], "time_gap_over_60s")
        values[1] = row("dgis", cycle="different")
        self.assertFalse(build_pairs(validate_rows(values)[0])[0])
        boundary = [row(instant="2026-10-24T14:29:40+00:00"), row("dgis", instant="2026-10-24T14:30:10+00:00")]
        pairs, exclusions = build_pairs(validate_rows(boundary)[0])
        self.assertFalse(pairs)
        self.assertEqual(exclusions[0]["reason"], "period_or_date_boundary")

    def test_pair_mean_denominators_sign_and_metrics(self):
        pairs, _ = build_pairs(validate_rows([row(duration=600, distance=5125), row("dgis", duration=400, distance=4875)])[0])
        pair = pairs[0]
        self.assertEqual(pair["signed_diff_s"], 200)
        self.assertEqual(pair["smape_pct"], 40)
        self.assertEqual(pair["relative_diff_pct"], 40)
        self.assertEqual(pair["length_diff_pct"], 5)
        self.assertTrue(pair["length_comparable"])
        metric = basic_metrics(pairs)
        self.assertEqual(metric["mae_s"], 200)
        self.assertEqual(metric["rmse_s"], 200)
        self.assertEqual(metric["iqr_abs_diff_s"], 0)

    def test_ors_is_control_and_never_primary(self):
        pairs, _ = build_pairs(validate_rows([row(), row("dgis"), row("ors")])[0])
        self.assertEqual(len(pairs), 3)
        self.assertEqual(sum(p["comparison_role"] == "primary" for p in pairs), 1)

    def test_no_pilot_inference_or_misleading_tiny_cluster_ci(self):
        pairs, _ = build_pairs(validate_rows([row(), row("dgis", duration=500)])[0])
        self.assertEqual(clustered_ci(pairs)["ci_status"], "not_estimated_pilot")
        self.assertTrue(all(r["status"] == "not_run_pilot_only" for r in inference_table(pairs)[1]))
        for pair in pairs:
            pair["phase"] = "scheduled"
        self.assertEqual(clustered_ci(pairs)["ci_status"], "not_estimated_need_8_routes_and_2_dates")

    def test_route_cluster_bootstrap_reproducible_and_whole_route(self):
        pairs = [{"route_id": f"R{i}", "date_astana": day, "phase": "scheduled", "abs_diff_s": float(i)}
                 for i in range(8) for day in ["2026-10-24", "2026-10-25"]]
        first = clustered_ci(pairs, seed=42, replicates=100)
        self.assertEqual(first, clustered_ci(pairs, seed=42, replicates=100))
        self.assertEqual(first["bootstrap_replicates"], 100)
        self.assertLessEqual(first["mae_ci_low_s"], 3.5)
        self.assertGreaterEqual(first["mae_ci_high_s"], 3.5)

    def test_exact_signed_rank_handles_ties_zeros_and_directions(self):
        self.assertEqual(exact_signed_rank([1, 2, 3])["p_value"], .25)
        self.assertEqual(exact_signed_rank([1, 1, 1])["p_value"], .25)
        self.assertEqual(exact_signed_rank([0, 0])["p_value"], 1)
        self.assertEqual(exact_signed_rank([-1, -2])["rank_biserial"], -1)
        self.assertEqual(exact_signed_rank([1, -1])["rank_biserial"], 0)

    def test_future_inference_matches_route_days_and_excludes_pilot(self):
        pairs = []
        for route in range(8):
            for day in ["2026-10-24", "2026-10-25"]:
                for period, value in [("peak", 100+route), ("offpeak", 50)]:
                    pairs.append(dict(comparison_role="primary", phase="scheduled", period=period,
                                      route_id=f"R{route}", date_astana=day, abs_diff_s=value, length_comparable=True))
                    pairs.append(dict(comparison_role="primary", phase="pilot", period=period,
                                      route_id=f"R{route}", date_astana=day, abs_diff_s=9999, length_comparable=True))
        contrasts, inference = inference_table(pairs)
        result = next(item for item in inference if item["length_group"] == "all")
        self.assertEqual(result["n_route_days"], 16)
        self.assertEqual(result["n_routes"], 8)
        self.assertEqual(result["median_peak_minus_offpeak_s"], 53.5)
        self.assertEqual(result["p_value"], 2/256)
        self.assertEqual(result["rank_biserial"], 1)

    def test_end_to_end_counts_missingness_and_raw_immutability(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "data" / "raw"
            raw.mkdir(parents=True)
            source = raw / "fixture.csv"
            values = [row(), row("dgis", duration=500), row("ors", duration=450)]
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=RAW_FIELDS, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(values)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            summary = run(root, replicates=100)
            self.assertEqual(summary["n_raw_rows"], 3)
            self.assertEqual(summary["n_pairs"], 3)
            self.assertEqual(summary["n_primary_pairs"], 1)
            self.assertEqual(before, hashlib.sha256(source.read_bytes()).hexdigest())
            with (root/"results"/"data_dictionary.csv").open(newline="", encoding="utf-8") as handle:
                dictionary = list(csv.DictReader(handle))
            optional = next(r for r in dictionary if r["dataset"] == "raw" and r["column"] == "duration_no_traffic_s")
            self.assertEqual(optional["missing_count"], "3")
            self.assertEqual(float(optional["missing_pct"]), 100)
            saved = json.loads((root/"results"/"summary.json").read_text())
            self.assertFalse(saved["rules"]["accuracy_estimable"])

    def test_empty_input_outputs_no_synthetic_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = run(Path(directory), replicates=100)
            self.assertEqual(summary["n_raw_rows"], 0)
            self.assertEqual(summary["n_pairs"], 0)
            self.assertIsNone(summary["first_request_utc"])
            self.assertTrue(all(item["status"] == "not_run_no_primary_comparison_pairs" for item in summary["inference"]))

    def test_ors_only_pilot_is_valid_instrument_evidence_without_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "data" / "raw"
            raw.mkdir(parents=True)
            with (raw / "fixture.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=RAW_FIELDS, extrasaction="ignore")
                writer.writeheader()
                writer.writerows([row("ors", cycle=f"c{i}") for i in range(3)])
            summary = run(root, replicates=100)
            self.assertEqual(summary["n_usable_observations"], 3)
            self.assertEqual(summary["n_primary_pairs"], 0)
            self.assertIsNone(summary["primary_length_comparable_pct"])
            self.assertEqual(summary["primary_metrics"], [])
            self.assertTrue(all(item["p_value"] is None for item in summary["inference"]))

    def test_frozen_raw_snapshot_excludes_later_live_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live = root / "data/raw"
            frozen = root / "data/report_snapshot/raw"
            live.mkdir(parents=True)
            frozen.mkdir(parents=True)
            source = live / "fixture.csv"
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=RAW_FIELDS, extrasaction="ignore")
                writer.writeheader()
                writer.writerow(row())
            snapshot = frozen / source.name
            snapshot.write_bytes(source.read_bytes())
            snapshot_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            with source.open("a", newline="", encoding="utf-8") as handle:
                csv.DictWriter(handle, fieldnames=RAW_FIELDS, extrasaction="ignore").writerow(row("dgis"))
            frozen_summary = run(root, replicates=100, raw_dir=Path("data/report_snapshot/raw"))
            self.assertEqual(frozen_summary["n_raw_rows"], 1)
            self.assertEqual(frozen_summary["n_primary_pairs"], 0)
            self.assertEqual(frozen_summary["input_files"][0]["file"], "data/report_snapshot/raw/fixture.csv")
            self.assertEqual(frozen_summary["input_files"][0]["sha256"], snapshot_hash)
            with (root / "results/observations.csv").open(newline="", encoding="utf-8") as handle:
                self.assertEqual(next(csv.DictReader(handle))["source_file"], "data/report_snapshot/raw/fixture.csv")
            live_summary = run(root, replicates=100)
            self.assertEqual(live_summary["n_raw_rows"], 2)
            self.assertEqual(live_summary["n_primary_pairs"], 1)
            self.assertEqual(hashlib.sha256(snapshot.read_bytes()).hexdigest(), snapshot_hash)


if __name__ == "__main__":
    unittest.main()
