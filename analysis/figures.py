"""Render genuine recorded observations from results/*.csv; no fabricated rows.

Run after process.py: python analysis/figures.py
Empty inputs are logged as skipped; no illustrative data replace missing evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

COLORS = {"tomtom": "#167D9A", "dgis": "#E58B26", "ors": "#667380"}


def read_csv(path):
    if not path.exists():
        raise FileNotFoundError(f"Run analysis/process.py first: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def truth(value):
    return str(value).lower() == "true"


def render(root):
    results, output = root / "results", root / "figures"
    output.mkdir(parents=True, exist_ok=True)
    quality = read_csv(results / "request_quality.csv")
    observations = read_csv(results / "observations.csv")
    pairs = read_csv(results / "pairs.csv")
    primary = [p for p in pairs if p["comparison_role"] == "primary"]
    usable = [r for r in observations if truth(r["usable"])]
    manifest = []
    phases = sorted({r["phase"] for r in observations})
    phase_label = "Pilot" if phases == ["pilot"] else "Recorded observations"
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.titlesize": 14,
                         "axes.labelsize": 10, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.titleweight": "bold", "figure.facecolor": "white", "savefig.facecolor": "white"})

    def save(fig, filename, caption, sources, n):
        fig.tight_layout()
        fig.savefig(output / filename, dpi=170, bbox_inches="tight")
        plt.close(fig)
        manifest.append({"file": f"figures/{filename}", "caption": caption, "sources": sources, "n_records": n, "status": "created"})

    def skip(filename, reason):
        manifest.append({"file": f"figures/{filename}", "status": "skipped", "reason": reason})

    if quality:
        fig, ax = plt.subplots(figsize=(8.2, 4.4))
        labels = [f"{r['provider']}\n{r['phase']}" for r in quality]
        ok = [int(r["n_http_ok"]) for r in quality]
        failed = [int(r["n_failed"]) for r in quality]
        ax.bar(labels, ok, label="HTTP 200 + status ok", color="#167D9A")
        ax.bar(labels, failed, bottom=ok, label="Failed request", color="#C85745")
        for i, (a, b) in enumerate(zip(ok, failed)):
            ax.text(i, a+b, f"n={a+b}", ha="center", va="bottom", fontsize=9)
        ax.set(ylabel="Recorded unique requests", title=f"{phase_label}: request outcomes")
        ax.set_ylim(0, max(a+b for a, b in zip(ok, failed))*1.16 or 1)
        ax.legend(frameon=False)
        save(fig, "analysis_request_quality.png", "Recorded request outcomes by provider and collection phase. Exact duplicate rows are counted once. HTTP success does not itself imply a usable route.", ["results/request_quality.csv"], sum(a+b for a, b in zip(ok, failed)))
    else:
        skip("analysis_request_quality.png", "No recorded requests")

    if usable:
        fig, ax = plt.subplots(figsize=(8.2, 4.4))
        period_order = ["peak", "offpeak", "other"]
        providers = sorted({r["provider"] for r in usable})
        counts = Counter((r["provider"], r["period"]) for r in usable)
        width = .72/len(providers)
        for i, provider in enumerate(providers):
            x = [j + (i-(len(providers)-1)/2)*width for j in range(3)]
            values = [counts[(provider, p)] for p in period_order]
            ax.bar(x, values, width, color=COLORS.get(provider), label=provider)
            for xx, value in zip(x, values):
                ax.text(xx, value, str(value), ha="center", va="bottom", fontsize=9)
        ax.set_xticks(range(3), ["Peak\n17:30–19:30", "Off-peak\n10–12 / 22–24", "Other"])
        ax.set(ylabel="Usable request observations", title=f"{phase_label}: observed time coverage")
        ax.set_ylim(0, max(counts.values())*1.16 or 1)
        ax.legend(frameon=False, title="Provider")
        save(fig, "analysis_period_coverage.png", "Usable observations classified by Asia/Almaty request time. Intervals include their start and exclude their end. A zero bar is observed absence, not a missing plot.", ["results/observations.csv"], len(usable))
    else:
        skip("analysis_period_coverage.png", "No usable observations")

    if usable:
        # These instrument figures remain valid for an ORS-only control pilot.
        # They do not stand in for the blocked traffic-aware comparison.
        grouped = defaultdict(list)
        for observation in usable:
            grouped[(observation["provider"], observation["route_id"])].append(float(observation["duration_s"])/60)
        routes = sorted({r["route_id"] for r in usable})
        providers = sorted({r["provider"] for r in usable})
        fig, ax = plt.subplots(figsize=(9.2, 4.8))
        for provider in providers:
            x = [i for i, route in enumerate(routes) if (provider, route) in grouped]
            y = [statistics.median(grouped[(provider, routes[i])]) for i in x]
            ax.plot(x, y, marker="o", ms=4, lw=1, color=COLORS.get(provider), label=provider)
        ax.set_xticks(range(len(routes)), routes, rotation=60)
        ax.set(xlabel="Fixed origin–destination route", ylabel="Median estimated travel time (minutes)", title=f"{phase_label}: recorded route estimates")
        ax.legend(frameon=False)
        save(fig, "analysis_provider_durations.png", "Median recorded duration per provider and fixed origin–destination route. ORS is a control without live traffic. This instrument summary cannot establish peak traffic disagreement or travel-time accuracy.", ["results/observations.csv"], len(usable))

        fig, ax = plt.subplots(figsize=(8.2, 4.6))
        for provider in providers:
            selected = [r for r in usable if r["provider"] == provider]
            ax.scatter([float(r["distance_m"])/1000 for r in selected], [float(r["duration_s"])/60 for r in selected],
                       color=COLORS.get(provider), s=30, alpha=.7, label=provider)
        ax.set(xlabel="Returned route distance (km)", ylabel="Estimated duration (minutes)", title=f"{phase_label}: distance and estimated duration")
        ax.legend(frameon=False)
        save(fig, "analysis_distance_duration.png", "Recorded estimated durations plotted against returned route distances. Values are API outputs, not measured journeys. Repeated values may overlap; no jitter or artificial observations are added.", ["results/observations.csv"], len(usable))

        latency_rows = [r for r in observations if r["latency_s"] and r["duplicate_kind"] != "exact"]
        if latency_rows:
            fig, ax = plt.subplots(figsize=(8.2, 4.6))
            plotted = 0
            for provider in providers:
                selected = [r for r in latency_rows if r["provider"] == provider]
                xs, ys = [], []
                for r in selected:
                    try:
                        instant = datetime.fromisoformat(r["timestamp_astana"])
                        latency = float(r["latency_s"])
                        if not math.isfinite(latency) or latency < 0:
                            continue
                    except (ValueError, TypeError):
                        continue
                    xs.append(instant)
                    ys.append(latency)
                if xs:
                    ax.scatter(xs, ys, color=COLORS.get(provider), s=24, alpha=.7, label=provider)
                    plotted += len(xs)
            if plotted:
                tz = datetime.fromisoformat(usable[0]["timestamp_astana"]).tzinfo
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%H:%M", tz=tz))
                ax.set(xlabel="Request start, Astana local time", ylabel="Recorded request latency (seconds)", title=f"{phase_label}: request timing")
                ax.legend(frameon=False)
                save(fig, "analysis_request_latency.png", "Recorded API request latencies by request start time. Latency is instrument performance and is distinct from estimated journey duration. Failed requests remain eligible if their timing was recorded.", ["results/observations.csv"], plotted)
            else:
                plt.close(fig)
                skip("analysis_request_latency.png", "No parseable latency records")
        else:
            skip("analysis_request_latency.png", "No latency records")

        sample = observations[:10]
        columns = ["cycle_id", "route_id", "provider", "duration_s", "distance_m", "status", "phase"]
        fig, ax = plt.subplots(figsize=(11.5, max(3.6, .34*len(sample)+1.4)))
        ax.axis("off")
        cells = [[str(r.get(column, "")) for column in columns] for r in sample]
        table = ax.table(cellText=cells, colLabels=columns, cellLoc="left", colLoc="left", loc="center")
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table.scale(1, 1.6)
        table.auto_set_column_width(range(len(columns)))
        for (i, _), cell in table.get_celld().items():
            if i == 0:
                cell.set_facecolor("#183C50")
                cell.set_text_props(color="white", weight="bold")
            else:
                cell.set_facecolor("#EDF3F5" if i % 2 else "white")
        ax.set_title(f"{phase_label}: first {len(sample)} recorded rows", pad=15)
        save(fig, "analysis_recorded_extract.png", "Verbatim selected raw fields from the first recorded CSV rows, retained in observations.csv with source filename and line number. The rendered table is an evidence view, not a screenshot of a separate application.", ["results/observations.csv"], len(sample))
    else:
        for name in ["provider_durations", "distance_duration", "request_latency", "recorded_extract"]:
            skip(f"analysis_{name}.png", "No usable observations")

    if primary:
        fig, ax = plt.subplots(figsize=(6.4, 5.2))
        for flag, color, label in [(True, "#167D9A", "Length difference ≤5%"), (False, "#E58B26", "Length difference >5%")]:
            selected = [p for p in primary if truth(p["length_comparable"]) == flag]
            if selected:
                ax.scatter([float(p["duration_b_s"])/60 for p in selected], [float(p["duration_a_s"])/60 for p in selected], c=color, s=28, alpha=.7, label=label)
        highest = max(float(p[field])/60 for p in primary for field in ["duration_a_s", "duration_b_s"])*1.05
        ax.plot([0, highest], [0, highest], "--", color="#6B7280", lw=1, label="Equal estimates")
        ax.set(xlim=(0, highest), ylim=(0, highest), xlabel="2GIS estimate (minutes)", ylabel="TomTom estimate (minutes)", title=f"{phase_label}: paired travel-time estimates")
        ax.legend(frameon=False, fontsize=8)
        save(fig, "analysis_paired_estimates.png", "Synchronized TomTom and 2GIS estimates for identical origin–destination pairs and collection cycles, with request starts at most 60 seconds apart. Equality indicates agreement only; no observed travel-time ground truth exists.", ["results/pairs.csv"], len(primary))

        fig, ax = plt.subplots(figsize=(9.2, 4.8))
        by_route = defaultdict(list)
        for pair in primary:
            by_route[pair["route_id"]].append(float(pair["abs_diff_s"]))
        routes = sorted(by_route)
        ax.boxplot([by_route[r] for r in routes], tick_labels=routes, showfliers=True, widths=.5,
                   medianprops={"color": "#C85745", "linewidth": 1.7})
        ax.set(xlabel="Fixed origin–destination route", ylabel="Absolute disagreement (seconds)", title=f"{phase_label}: disagreement across routes")
        ax.tick_params(axis="x", labelrotation=60)
        save(fig, "analysis_route_disagreement.png", "Distributions of absolute TomTom−2GIS time differences by route. Each point entering a box is one paired cycle; small pilot boxes summarize few repeated observations and are not confidence intervals.", ["results/pairs.csv"], len(primary))

        fig, ax = plt.subplots(figsize=(8.2, 4.6))
        ax.scatter([float(p["length_diff_pct"]) for p in primary], [float(p["abs_diff_s"]) for p in primary],
                   color="#167D9A", alpha=.72, s=30)
        ax.axvline(5, color="#C85745", linestyle="--", lw=1.4, label="Pre-specified 5% threshold")
        ax.set(xlabel="Absolute length difference / mean length (%)", ylabel="Absolute time disagreement (seconds)", title=f"{phase_label}: returned route lengths and time estimates")
        ax.legend(frameon=False, fontsize=8)
        save(fig, "analysis_length_comparability.png", "Time disagreement against returned route-length disagreement, using the pair mean as denominator. Similar lengths are a limited comparability proxy and do not establish identical street geometry.", ["results/pairs.csv"], len(primary))

        fig, ax = plt.subplots(figsize=(8.2, 4.6))
        ordered = sorted(primary, key=lambda p: p["timestamp_utc"])
        times = [datetime.fromisoformat(p["timestamp_astana"]) for p in ordered]
        ax.scatter(times, [float(p["time_gap_s"]) for p in ordered], color="#167D9A", s=25, alpha=.75)
        ax.axhline(60, color="#C85745", linestyle="--", lw=1.3, label="Pairing limit: 60 seconds")
        ax.set(ylabel="Request-start separation (seconds)", xlabel="Request midpoint, Astana local time", title=f"{phase_label}: synchronization evidence", ylim=(-1, 66))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%H:%M", tz=times[0].tzinfo))
        ax.legend(frameon=False, fontsize=8)
        save(fig, "analysis_synchronization.png", "Observed start-time separations for accepted primary pairs. Pair exclusions are separately retained in results/pair_exclusions.csv; the plot does not imply every raw request was paired.", ["results/pairs.csv", "results/pair_exclusions.csv"], len(primary))

        fig, ax = plt.subplots(figsize=(8.2, 4.6))
        for route in sorted(by_route):
            selected = sorted([p for p in primary if p["route_id"] == route], key=lambda p: p["timestamp_utc"])
            route_times = [datetime.fromisoformat(p["timestamp_astana"]) for p in selected]
            ax.plot(route_times, [float(p["signed_diff_s"]) for p in selected], marker="o", ms=3, lw=.9, alpha=.65)
        ax.axhline(0, color="#6B7280", lw=1)
        ax.set(xlabel="Request midpoint, Astana local time", ylabel="TomTom − 2GIS (seconds)", title=f"{phase_label}: signed disagreement over recorded cycles")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%H:%M", tz=times[0].tzinfo))
        save(fig, "analysis_signed_timeline.png", "Signed TomTom−2GIS time differences over recorded cycles. Each line connects observations of one fixed origin–destination route; positive values indicate a longer TomTom estimate. No interpolation is used in calculations.", ["results/pairs.csv"], len(primary))
    else:
        for name in ["paired_estimates", "route_disagreement", "length_comparability", "synchronization", "signed_timeline"]:
            skip(f"analysis_{name}.png", "No synchronized valid TomTom–2GIS pairs")

    payload = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "python_version": platform.python_version(),
               "matplotlib_version": matplotlib.__version__, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "input_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [results / "request_quality.csv", results / "observations.csv", results / "pairs.csv", results / "pair_exclusions.csv"]},
               "figures": manifest}
    (output / "analysis_manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"created": sum(f["status"] == "created" for f in manifest), "skipped": sum(f["status"] == "skipped" for f in manifest)}))
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    render(parser.parse_args().root)
