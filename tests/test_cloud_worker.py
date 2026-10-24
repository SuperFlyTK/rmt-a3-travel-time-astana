"""No HTTP or git operations: fake clock and backends exercise worker guarantees."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from collector.cloud_worker import (HARD_END, RepositoryBackend, WorkerError,
                                    handoff, inspect_protocol, run_worker)


class FakeClock:
    def __init__(self, now=None):
        self.instant=now or datetime(2026,10,24,11,20,tzinfo=timezone.utc)
        self.elapsed=0
    def now(self):return self.instant
    def monotonic(self):return self.elapsed
    def sleep(self,seconds):
        self.instant+=timedelta(seconds=seconds)
        self.elapsed+=seconds


class FakeBackend:
    def __init__(self,clock,**changes):
        self.clock=clock
        self.snapshot=dict(end=HARD_END,reserved=70,budget=900,halted=False,last_batch=None)
        self.snapshot.update(changes)
        self.calls=[]
    def inspect(self):return dict(self.snapshot)
    def process_followup(self,cutoff):return False
    def collect_cycle(self,cycle,cutoff):
        self.calls.append(self.clock.now())
        self.snapshot['last_batch']=self.clock.now()
        self.snapshot['reserved']+=4
        self.clock.sleep(2)
        return 'completed'


class WorkerTests(unittest.TestCase):
    def test_two_cycles_are_fifteen_minutes_apart_then_bounded_handoff(self):
        clock=FakeClock()
        backend=FakeBackend(clock)
        outcome=run_worker(backend,clock,'123',max_runtime_seconds=1800)
        self.assertEqual(len(backend.calls),2)
        self.assertEqual((backend.calls[1]-backend.calls[0]).total_seconds(),900)
        self.assertTrue(outcome['handoff'])
        self.assertEqual(backend.snapshot['reserved'],78)

    def test_a_long_missing_interval_is_not_backfilled(self):
        clock=FakeClock()
        backend=FakeBackend(clock,last_batch=clock.now()-timedelta(hours=8))
        run_worker(backend,clock,'123',max_runtime_seconds=1800)
        self.assertEqual(len(backend.calls),2)
        self.assertEqual(backend.calls[0],datetime(2026,10,24,11,20,tzinfo=timezone.utc))

    def test_successor_respects_recent_reservation_minimum_interval(self):
        clock=FakeClock()
        first=clock.now()
        backend=FakeBackend(clock,last_batch=first-timedelta(seconds=100))
        run_worker(backend,clock,'123',max_runtime_seconds=1800)
        self.assertEqual((backend.calls[0]-first).total_seconds(),800)
        self.assertEqual((backend.calls[1]-backend.calls[0]).total_seconds(),900)

    def test_expiry_halt_and_budget_never_start_or_handoff(self):
        for changes in [{'end':datetime(2026,10,24,11,tzinfo=timezone.utc)}, {'halted':True}, {'reserved':898}]:
            clock=FakeClock()
            backend=FakeBackend(clock,**changes)
            outcome=run_worker(backend,clock,'123',1800)
            self.assertEqual(backend.calls,[])
            self.assertFalse(outcome['handoff'])

    def test_late_window_does_not_start_a_partial_new_batch(self):
        clock=FakeClock(HARD_END-timedelta(seconds=59))
        backend=FakeBackend(clock)
        outcome=run_worker(backend,clock,'123',1800)
        self.assertEqual(backend.calls,[])
        self.assertFalse(outcome['handoff'])

    def test_original_fixed_end_and_budget_are_hard_caps(self):
        config={'active_providers':['ors'],'routes_per_scheduled_run':4,'scheduled_interval_minutes':15,
                'collection_end_utc':'2027-01-01T00:00:00+00:00',
                'providers':{'ors':{'collection_approved':True,'study_request_budget':99999}}}
        snapshot=inspect_protocol(config,{'counts':{'ors':70},'reservations':{}})
        self.assertEqual(snapshot['end'],HARD_END)
        self.assertEqual(snapshot['budget'],900)
        config['active_providers']=['tomtom','ors']
        with self.assertRaises(WorkerError):inspect_protocol(config,{})

    def test_remote_reservation_failure_prevents_every_http_attempt(self):
        events=[]
        class Backend(RepositoryBackend):
            def reserve(self,cycle):events.append('reserve')
            def publish_reservation(self):
                events.append('publish-reservation')
                raise WorkerError('simulated_push_failure')
            def execute(self,cycle,cutoff):events.append('HTTP')
            def publish_observations(self):events.append('publish-observations')
        backend=Backend(Path('unused'),FakeClock(),'1')
        with self.assertRaises(WorkerError):backend.collect_cycle('unit-fixture',HARD_END)
        self.assertEqual(events,['reserve','publish-reservation'])

    def test_submission_deadline_can_shorten_but_never_extend_original_end(self):
        config={'active_providers':['ors'],'routes_per_scheduled_run':4,'scheduled_interval_minutes':15,
                'collection_end_utc':HARD_END.isoformat(),
                'providers':{'ors':{'collection_approved':True,'study_request_budget':900}}}
        cutoff=datetime(2026,10,25,12,40,tzinfo=timezone.utc)
        protocol={'collection_end_utc':cutoff.isoformat(),'windows':[]}
        self.assertEqual(inspect_protocol(config,{},protocol)['end'],cutoff)
        protocol['collection_end_utc']='2027-01-01T00:00:00+00:00'
        self.assertEqual(inspect_protocol(config,{},protocol)['end'],HARD_END)

    def test_short_final_peak_window_wakes_before_ordinary_ors_batch(self):
        clock=FakeClock(datetime(2026,10,25,12,29,tzinfo=timezone.utc))
        peak_start=clock.now()+timedelta(minutes=1)
        calls=[]
        class Backend(FakeBackend):
            def process_followup(self,cutoff):
                if self.snapshot['followup_starts'] and self.clock.now()>=peak_start:
                    calls.append(self.clock.now())
                    self.snapshot['followup_starts']=[]
                    self.clock.sleep(45)
                    return True
                return False
        backend=Backend(clock,end=peak_start+timedelta(minutes=10),
                        last_batch=clock.now()-timedelta(minutes=2),followup_starts=[peak_start])
        outcome=run_worker(backend,clock,'test',1800)
        self.assertEqual(calls,[peak_start])
        self.assertEqual(backend.calls,[])
        self.assertFalse(outcome['handoff'])

    def test_partial_attempt_is_published_even_after_collector_failure(self):
        events=[]
        class Backend(RepositoryBackend):
            def reserve(self,cycle):events.append('reserve')
            def publish_reservation(self):events.append('publish-reservation')
            def execute(self,cycle,cutoff):
                events.append('HTTP')
                raise WorkerError('simulated_collector_failure')
            def publish_observations(self):events.append('publish-observations')
        backend=Backend(Path('unused'),FakeClock(),'1')
        with self.assertRaises(WorkerError):backend.collect_cycle('unit-fixture',HARD_END)
        self.assertEqual(events,['reserve','publish-reservation','HTTP','publish-observations'])

    def test_child_process_deadline_is_less_than_remaining_collection_window(self):
        clock=FakeClock(HARD_END-timedelta(seconds=20))
        backend=RepositoryBackend(Path('unused'),clock,'1')
        captured=[]
        def timeout_command(arguments,stage,timeout=120,**kwargs):
            captured.append(timeout)
            raise WorkerError('execute_reserved_cycle_timeout')
        backend.command=timeout_command
        self.assertEqual(backend.execute('unit-fixture',HARD_END),'deadline_reached')
        self.assertEqual(captured,[19])
        clock.sleep(20)
        self.assertEqual(backend.execute('unit-fixture',HARD_END),'deadline_reached')
        self.assertEqual(captured,[19])

    def test_subprocess_timeout_is_a_nonsecret_diagnostic(self):
        backend=RepositoryBackend(Path('unused'),FakeClock(),'1')
        with patch('collector.cloud_worker.subprocess.run',side_effect=subprocess.TimeoutExpired(['fake'],1,output='FAKE_SECRET')):
            with self.assertRaisesRegex(WorkerError,'execute_timeout') as caught:
                backend.command(['fake'],'execute',timeout=1)
        self.assertNotIn('FAKE_SECRET',str(caught.exception))

    def test_handoff_recognizes_ambiguous_success_without_duplicate_dispatch(self):
        class Backend:
            calls=0
            def queued_successor_exists(self):return self.calls>0
            def dispatch_successor(self):
                self.calls+=1
                raise WorkerError('ambiguous_network_timeout')
        backend=Backend()
        self.assertTrue(handoff(backend,FakeClock(),HARD_END))
        self.assertEqual(backend.calls,1)

    def test_handoff_retries_only_three_times_and_surfaces_failure(self):
        class Backend:
            calls=0
            def queued_successor_exists(self):return False
            def dispatch_successor(self):
                self.calls+=1
                raise WorkerError('network_failure')
        backend=Backend()
        with self.assertRaises(WorkerError):handoff(backend,FakeClock(),HARD_END)
        self.assertEqual(backend.calls,3)

    def test_no_successor_after_window_end(self):
        backend=object()
        self.assertFalse(handoff(backend,FakeClock(HARD_END),HARD_END))

    def test_pending_concurrency_run_counts_as_existing_successor(self):
        backend=RepositoryBackend(Path('unused'),FakeClock(),'1')
        result=subprocess.CompletedProcess([],0,stdout=json.dumps({'workflow_runs':[{'id':2,'status':'pending'}]}))
        with patch.dict('os.environ',{'GITHUB_REPOSITORY':'example/repo'}),patch.object(backend,'command',return_value=result):
            self.assertTrue(backend.queued_successor_exists())


if __name__=='__main__':unittest.main()
