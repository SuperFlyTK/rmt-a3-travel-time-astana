"""Plotting regression tests use temporary synthetic fixtures, never research data."""

import csv
from datetime import datetime,timedelta,timezone
import tempfile
import unittest
from pathlib import Path

from analysis.figures import render,time_formatter
from analysis.process import RAW_FIELDS, run


class FigureTests(unittest.TestCase):
    def test_time_tick_precision_resolves_short_spans(self):
        start=datetime(2026,10,24,7,37,0,tzinfo=timezone(timedelta(hours=5)))
        self.assertEqual(time_formatter([start,start+timedelta(seconds=119)]).fmt,"%d %b\n%H:%M:%S")
        self.assertEqual(time_formatter([start,start+timedelta(seconds=120)]).fmt,"%d %b\n%H:%M")
    def test_ors_only_instrument_figures_without_synthetic_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            raw=root/'data/raw'
            raw.mkdir(parents=True)
            with (raw/'unit-fixtures.csv').open('w',encoding='utf-8',newline='') as handle:
                writer=csv.DictWriter(handle,fieldnames=RAW_FIELDS)
                writer.writeheader()
                for route in range(3):
                    item=dict.fromkeys(RAW_FIELDS,'')
                    item.update(timestamp_utc=f'2026-10-24T06:0{route}:00+00:00',timestamp_astana=f'2026-10-24T11:0{route}:00+05:00',
                                cycle_id='unit-fixture',route_id=f'TEST{route}',provider='ors',duration_s=str(300+route*60),
                                distance_m=str(3000+route*500),status='ok',http_code='200',request_id=f'fixture-{route}',latency_s='0.5',phase='pilot')
                    writer.writerow(item)
            summary=run(root,replicates=100)
            self.assertEqual(summary['n_primary_pairs'],0)
            manifest=render(root)
            created=[item for item in manifest['figures'] if item['status']=='created']
            self.assertEqual(len(created),6)
            self.assertTrue(all((root/item['file']).stat().st_size>5000 for item in created))
            comparison=next(item for item in manifest['figures'] if item['file'].endswith('analysis_paired_estimates.png'))
            self.assertEqual(comparison['status'],'skipped')

    def test_empty_dataset_skips_every_plot(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            run(root,replicates=100)
            manifest=render(root)
            self.assertTrue(all(item['status']=='skipped' for item in manifest['figures']))
            self.assertFalse(list((root/'figures').glob('*.png')))


if __name__=='__main__':unittest.main()
