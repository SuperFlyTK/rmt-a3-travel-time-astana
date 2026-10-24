"""Prospective follow-up safety checks with in-memory responses; no HTTP/git."""
import copy
import csv
from datetime import datetime, timedelta, timezone
import hashlib
from http.client import IncompleteRead
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from collector import google_followup as followup
from collector.cloud_worker import RepositoryBackend, WorkerError, run_worker
from tests.test_cloud_worker import FakeClock, FakeBackend


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root/"config").mkdir()
        (self.root/"data").mkdir()
        (self.root/"docs").mkdir()
        self.now = datetime(2026,10,24,12,30,tzinfo=timezone.utc)
        self.clock = FakeClock(self.now)
        self.routes = [{"route_id":f"R{i:02}","origin_lat":"51.1","origin_lon":"71.4", "destination_lat":"51.2","destination_lon":"71.5"} for i in range(1,21)]
        route_bytes = b"fixed-route-fixture"
        (self.root/"data/routes.csv").write_bytes(route_bytes)
        self.config = {"active_providers":["ors"],"routes_per_scheduled_run":4,
                       "collection_end_utc":followup.HARD_END.isoformat(),
                       "providers":{"ors":{"collection_approved":True,"study_request_budget":900,"minimum_interval_s":2.1}}}
        config_bytes = json.dumps(self.config).encode()
        (self.root/"config/study.json").write_bytes(config_bytes)
        self.config_hash = hashlib.sha256(config_bytes).hexdigest()
        starts = [self.now, self.now+timedelta(hours=4.5), self.now+timedelta(hours=16.5), self.now+timedelta(days=1), self.now+timedelta(hours=28.5)]
        self.protocol = {"approved_at_utc":(self.now-timedelta(hours=1)).isoformat(),"routes_per_window":20,
                         "windows":[{"id":f"window-{i}","start_utc":v.isoformat(),"end_utc":(v+timedelta(hours=2)).isoformat(),"period":"peak" if i in (0,3) else "offpeak"} for i,v in enumerate(starts)],
                         "collection_end_utc":followup.HARD_END.isoformat(),"publish_google_raw":False,
                         "google_secret_env":"GOOGLE_MAPS_DEMO_KEY","google_routing_preference":"TRAFFIC_AWARE_OPTIMAL",
                         "minimum_pairs_for_numeric_aggregates":5,"max_pair_start_gap_seconds":60,
                         "minimum_pair_interval_seconds":2.1,"google_total_request_budget":121,"google_already_reserved":21,
                         "future_google_request_budget":100,"future_ors_request_budget":100,
                         "routes_sha256":hashlib.sha256(route_bytes).hexdigest()}
        (self.root/followup.PROTOCOL).write_text(json.dumps(self.protocol),encoding="utf-8")
        self.protocol_hash = followup.load_protocol(self.root)[1]
        self.state = {"counts":{"ors":94,"google_demo":21},"next_route":12,"reservations":{},"completed_cycles":[]}

    def prepare(self, now=None, deadline=None):
        return followup.prepare_window(self.state,self.config,self.config_hash,self.protocol,self.protocol_hash,self.routes,
                                       now or self.clock.now(),deadline or followup.HARD_END)

    def run_window(self, google_code=200, ors_code=200, extra_google_fields=False, ors_start_shift=0):
        if "window-0" not in self.state["reservations"]:
            self.prepare()
        def google(route,key):
            metadata={"timestamp_utc":self.clock.now().isoformat(),"route_id":route["route_id"],"provider":"google_demo",
                      "http_code":google_code,"status":"ok" if google_code==200 else "http_error"}
            if extra_google_fields:metadata.update(duration_s=601.23456, payload="GOOGLE_PAYLOAD_NEVER_STORE", key=key)
            return metadata,(601.23456,6001.23456) if google_code==200 else None
        def ors(provider,route,key,cycle,phase,directory,secrets):
            row={field:"" for field in followup.collect.FIELDS}
            row.update(timestamp_utc=(self.clock.now()+timedelta(seconds=ors_start_shift)).isoformat(),route_id=route["route_id"],
                       provider="ors",cycle_id=cycle,phase=phase,http_code=ors_code,status="ok" if ors_code==200 else "http_error",
                       duration_s=600,distance_m=6000)
            return row
        return followup.execute_window(self.root,self.state,self.config,self.protocol,self.protocol_hash,"window-0",followup.HARD_END,
                                       "FAKE_GOOGLE_SECRET","FAKE_ORS_SECRET",now=self.clock.now,sleep=self.clock.sleep,
                                       google_fetch=google,ors_fetch=ors)

    def test_future_window_makes_no_reservation_and_zero_budget_change(self):
        before=copy.deepcopy(self.state)
        action=self.prepare(now=self.now-timedelta(seconds=1))
        self.assertFalse(action["changed"])
        self.assertIsNone(action["cycle_id"])
        self.assertEqual(self.state["counts"],before["counts"])
        self.assertEqual(self.state["reservations"],{})

    def test_due_window_reserves_twenty_both_without_changing_rotation(self):
        action=self.prepare()
        self.assertEqual(action["cycle_id"],"window-0")
        self.assertEqual(self.state["counts"],{"ors":114,"google_demo":41})
        self.assertEqual(self.state["next_route"],12)
        self.assertEqual(self.state["reservations"]["window-0"]["mode"],"exploratory")
        self.assertEqual(self.state["reservations"]["window-0"]["protocol_sha256"],self.protocol_hash)

    def test_interrupted_reservation_is_consumed_without_replay_or_refund(self):
        self.prepare()
        action=self.prepare()
        self.assertIsNone(action["cycle_id"])
        self.assertEqual(self.state["counts"]["google_demo"],41)
        self.assertEqual(self.state["google_followup"]["windows"]["window-0"]["status"],"interrupted_no_replay")

    def test_half_open_expired_window_marked_missed_not_backfilled(self):
        action=self.prepare(now=self.now+timedelta(hours=2))
        self.assertIsNone(action["cycle_id"])
        self.assertEqual(self.state["google_followup"]["windows"]["window-0"]["status"],"missed")
        self.assertEqual(self.state["counts"]["google_demo"],21)

    def test_five_windows_never_exceed_google_121_future_100(self):
        for window in self.protocol["windows"]:
            action=self.prepare(now=followup.stamp(window["start_utc"]))
            self.assertEqual(action["cycle_id"],window["id"])
            self.state["google_followup"]["windows"][window["id"]]["status"]="completed"
        self.assertEqual(self.state["counts"]["google_demo"],121)
        self.assertEqual(self.state["counts"]["ors"],194)
        self.assertEqual(sum(p["google_followup_reserved_requests"] for p in self.state["reservations"].values()),100)

    def test_google_total_future_and_shared_ors_caps_fail_closed(self):
        original=copy.deepcopy(self.state)
        for changes in ({"google_demo":102},{"ors":881}):
            self.state=copy.deepcopy(original)
            self.state["counts"].update(changes)
            self.assertIsNone(self.prepare()["cycle_id"])
            self.assertEqual(self.state["reservations"],{})
        self.state=copy.deepcopy(original)
        self.state["reservations"]["prior"]={"google_followup_reserved_requests":100}
        self.assertIsNone(self.prepare()["cycle_id"])

    def test_google_halt_does_not_set_ors_global_halt(self):
        result=self.run_window(google_code=403)
        self.assertEqual(result["n_attempted_pairs"],1)
        self.assertIn("halted_reason",self.state["google_followup"])
        self.assertNotIn("halted_reason",self.state)
        self.assertEqual(result["completion_reason"],"google_followup_halt")

    def test_ors_rate_limit_sets_global_halt(self):
        result=self.run_window(ors_code=429)
        self.assertEqual(result["n_attempted_pairs"],1)
        self.assertIn("halted_reason",self.state)

    def test_aggregate_only_persistence_strips_google_payload_and_individual_values(self):
        result=self.run_window(extra_google_fields=True)
        self.assertEqual(result["n_valid_pairs"],20)
        status_path=self.root/"data/followup/google_request_status.csv"
        with status_path.open(encoding="utf-8") as handle:
            rows=list(csv.DictReader(handle))
        self.assertEqual(len(rows),20)
        self.assertEqual(set(rows[0]),set(followup.STATUS_FIELDS))
        text="\n".join(p.read_text(encoding="utf-8") for p in self.root.rglob("*") if p.is_file())
        for forbidden in ("GOOGLE_PAYLOAD_NEVER_STORE","FAKE_GOOGLE_SECRET","601.23456","6001.23456"):
            self.assertNotIn(forbidden,text)
        with (self.root/"results/google_followup_benchmark.csv").open(encoding="utf-8") as handle:
            self.assertEqual(len(list(csv.DictReader(handle))),1)

    def test_both_request_start_times_must_be_inside_window(self):
        result=self.run_window(ors_start_shift=7200)
        self.assertEqual(result["n_attempted_pairs"],20)
        self.assertEqual(result["n_valid_pairs"],0)
        self.assertEqual(result["mae_s"],"")

    def test_malformed_and_implausible_ors_measurements_excluded(self):
        meta={"status":"ok","timestamp_utc":self.now.isoformat()}
        for duration in ("bad",None,float("nan"),1):
            ors={**meta,"duration_s":duration,"distance_m":6000}
            self.assertIsNone(followup.valid_pair(meta,(600,6000),ors,self.now,self.now+timedelta(hours=2),60))

    def test_window_end_stops_new_route_starts_and_keeps_inflight_metadata(self):
        self.clock=FakeClock(self.now+timedelta(hours=2,seconds=-91))
        self.prepare()
        self.clock=FakeClock(self.now+timedelta(hours=2,seconds=-1))
        result=self.run_window()
        self.assertEqual(result["n_attempted_pairs"],1)
        self.assertEqual(result["n_valid_pairs"],1)
        self.assertEqual(result["mae_s"],"")

    def test_route_hash_change_prevents_execution(self):
        self.prepare()
        (self.root/"data/routes.csv").write_text("changed",encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"route_register_changed"):
            followup.execute_window(self.root,self.state,self.config,self.protocol,self.protocol_hash,"window-0",followup.HARD_END,"fake","fake",now=self.clock.now)

    def test_route_hash_is_invariant_to_windows_linux_line_endings(self):
        self.assertEqual(followup.route_hash(b"route_id,lat\r\nR01,51.1\r\n"),
                         followup.route_hash(b"route_id,lat\nR01,51.1\n"))
        self.assertNotEqual(followup.route_hash(b"route_id,lat\nR01,51.1\n"),
                            followup.route_hash(b"route_id,lat\nR01,51.2\n"))

    def test_transport_exception_never_exposes_google_body(self):
        class Opener:
            def open(self,*args,**kwargs):raise IncompleteRead(b"FAKE_GOOGLE_SECRET")
        with patch.object(followup,"build_opener",return_value=Opener()):
            metadata,values=followup.google_request(self.routes[0],"FAKE_GOOGLE_SECRET")
        self.assertEqual(metadata["status"],"transport_error")
        self.assertIsNone(values)
        self.assertNotIn("FAKE_GOOGLE_SECRET",json.dumps(metadata))

    def test_remote_reservation_push_failure_prevents_followup_http(self):
        events=[]
        class Backend(RepositoryBackend):
            def command(self,arguments,stage,**kwargs):
                events.append(stage)
                return subprocess.CompletedProcess(arguments,0,stdout=json.dumps({"changed":True,"cycle_id":"window-0","deadline_utc":followup.HARD_END.isoformat()}))
            def commit_and_push(self,paths,message):
                events.append("remote-publish")
                raise WorkerError("simulated_push_failure")
        backend=Backend(self.root,self.clock,"1")
        with self.assertRaises(WorkerError):backend.process_followup(followup.HARD_END)
        self.assertEqual(events,["prepare_google_followup","remote-publish"])

    def test_no_due_worker_hook_does_not_publish_or_execute(self):
        events=[]
        class Backend(RepositoryBackend):
            def command(self,arguments,stage,**kwargs):
                events.append(stage)
                return subprocess.CompletedProcess(arguments,0,stdout=json.dumps({"changed":False,"cycle_id":None}))
            def commit_and_push(self,paths,message):events.append("unexpected-publish")
        self.assertFalse(Backend(self.root,self.clock,"1").process_followup(followup.HARD_END))
        self.assertEqual(events,["prepare_google_followup"])

    def test_worker_reads_followup_ors_halt_before_ordinary_request(self):
        class Backend(FakeBackend):
            def process_followup(self,cutoff):
                self.snapshot["halted"]=True
                return True
        backend=Backend(self.clock)
        result=run_worker(backend,self.clock,"fixture",1800)
        self.assertEqual(backend.calls,[])
        self.assertFalse(result["handoff"])

    def test_google_only_halt_does_not_stop_ordinary_worker(self):
        class Backend(FakeBackend):
            did_followup=False
            def process_followup(self,cutoff):
                if self.did_followup:return False
                self.did_followup=True
                self.snapshot["reserved"]+=20
                # Google-specific halt never changes the shared ORS halt flag.
                return True
        backend=Backend(self.clock)
        result=run_worker(backend,self.clock,"fixture",1800)
        self.assertEqual(len(backend.calls),2)
        self.assertEqual(backend.snapshot["reserved"],98)
        self.assertTrue(result["handoff"])


if __name__=="__main__":unittest.main()
