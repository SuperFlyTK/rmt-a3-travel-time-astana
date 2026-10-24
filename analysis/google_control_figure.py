"""Plot only the saved aggregate exploratory comparison, never Google responses."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    root = Path(__file__).resolve().parents[1]
    source = root / 'results/google_control_benchmark.csv'
    with source.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        raise SystemExit('Expected exactly one aggregate benchmark row.')
    row = rows[0]
    n = int(row['n_valid_pairs'])
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11})
    fig, ax = plt.subplots(figsize=(7.8, 4.2), constrained_layout=True)
    if n >= 5:
        labels = ['Median signed difference', 'Median absolute difference', 'MAE (disagreement)', 'RMSE (disagreement)']
        keys = ['median_signed_difference_s', 'median_absolute_difference_s', 'mae_s', 'rmse_s']
        values = [float(row[k]) for k in keys]
        bars = ax.barh(labels, values, color=['#365F8A', '#288C83', '#288C83', '#288C83'], height=.6)
        ax.invert_yaxis()
        ax.axvline(0, color='#555555', linewidth=.8)
        ax.bar_label(bars, labels=[f'{v:,.1f}' for v in values], padding=5, fontsize=11)
        low, high = min(0, min(values)), max(0, max(values))
        span = max(high - low, 1)
        ax.set_xlim(low - span * .23, high + span * .23)
        ax.set_xlabel('Seconds; signed difference = Google minus ORS')
    else:
        ax.text(.5, .5, f'{n} valid pairs: fewer than the 5 required for numeric summaries',
                ha='center', va='center', transform=ax.transAxes, wrap=True)
        ax.set_xticks([]); ax.set_yticks([])
    ax.set_title(f'Exploratory Google–ORS comparison | {n} valid pairs', fontsize=13, pad=15)
    ax.spines[['top', 'right']].set_visible(False)
    fig.text(.02, -.02, 'Traffic-aware request versus control without live traffic; neither provider is ground truth.', fontsize=9)
    output = root / 'figures/google_control_benchmark.png'
    output.parent.mkdir(exist_ok=True)
    fig.savefig(output, dpi=200, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    manifest = {'figures': [{
        'status': 'created', 'file': 'figures/google_control_benchmark.png',
        'caption': 'Original aggregate disagreement metrics for one separate exploratory cycle. Google used TRAFFIC_AWARE_OPTIMAL; ORS has no live traffic. These are not accuracy measures or the primary peak-period comparison.',
        'context': 'summarizes the separate Google–ORS instrument comparison using aggregate metrics only',
        'sources': ['results/google_control_benchmark.csv'],
        'source_manifest': 'figures/google_control_manifest.json',
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'category': 'exploratory',
        'credit': 'Team-computed comparative-test results; ORS control: © openrouteservice by HeiGIT | Data from OpenStreetMap',
        'n_valid_pairs': n, 'google_raw_retained': False
    }]}
    (root / 'figures/google_control_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'figure': str(output), 'n_valid_pairs': n}))


if __name__ == '__main__':
    main()
