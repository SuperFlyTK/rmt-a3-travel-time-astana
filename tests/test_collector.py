"""Offline contract, quota, and failure tests; no real credentials or requests."""

import copy
import io
import json
from http.client import IncompleteRead
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from collector import collect


ROUTE = {'route_id':'TEST01','origin_lat':'51.1','origin_lon':'71.2','destination_lat':'51.2','destination_lon':'71.3'}
GEOJSON = {'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'LineString','coordinates':[[71.2,51.1],[71.3,51.2]]},
             'properties':{'summary':{'duration':600.5,'distance':5000.25}}}]}


def config():
    return {'active_providers':['ors'],'routes_per_scheduled_run':1,
            'providers':{'ors':{'collection_approved':True,'study_request_budget':3,'minimum_interval_s':0}}}


def state():
    return {'next_route':0,'counts':{},'completed_cycles':[]}


class CollectorTests(unittest.TestCase):
    def test_ors_geojson_contract_coordinates_and_exact_measurements(self):
        request=collect.build_request('ors',ROUTE,'unit-test-secret')
        self.assertEqual(request.method,'POST')
        self.assertEqual(request.full_url,'https://api.heigit.org/openrouteservice/v2/directions/driving-car/geojson')
        self.assertNotIn('unit-test-secret',request.full_url)
        self.assertEqual(request.get_header('Authorization'),'unit-test-secret')
        self.assertEqual(json.loads(request.data)['coordinates'],[[71.2,51.1],[71.3,51.2]])
        duration,baseline,distance,geometry=collect.extract('ors',GEOJSON)
        self.assertEqual((duration,baseline,distance),(600.5,'',5000.25))
        self.assertEqual(geometry,GEOJSON['features'][0]['geometry'])

    def test_scrubbing_nested_keys_values_and_urlencoded_secrets(self):
        secret='FAKE/SECRET+='
        value={'authorization':secret,'metadata':{'url':'?key=FAKE%2FSECRET%2B%3D','text':f'echo {secret}'},secret:secret,'x-goog-api-key':'unused'}
        serialized=json.dumps(collect.scrub(value,[secret]))
        self.assertNotIn(secret,serialized)
        self.assertNotIn('FAKE%2FSECRET%2B%3D',serialized)
        self.assertNotIn('authorization',serialized)
        self.assertNotIn('x-goog-api-key',serialized)

    def test_redirects_do_not_reissue_secret_headers(self):
        self.assertIsNone(collect.NoRedirect().redirect_request(None,None,302,'move',{},'https://example.invalid'))

    def test_fetch_saves_sanitized_payload_and_expected_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            response=MagicMock()
            response.status=200
            payload=copy.deepcopy(GEOJSON)
            payload['metadata']={'key':'unit-test-secret','echo':'unit-test-secret'}
            response.read.return_value=json.dumps(payload).encode()
            opener=MagicMock()
            opener.open.return_value.__enter__.return_value=response
            with patch.object(collect,'ROOT',root),patch.object(collect,'build_opener',return_value=opener):
                row=collect.fetch('ors',ROUTE,'unit-test-secret','test-cycle','pilot',root/'data/raw',['unit-test-secret'])
            self.assertEqual(row['status'],'ok')
            self.assertEqual(row['duration_s'],600.5)
            self.assertEqual(opener.open.call_count,1)
            self.assertNotIn('unit-test-secret',(root/row['raw_file']).read_text())
            self.assertEqual(json.loads((root/row['geometry_file']).read_text())['type'],'LineString')

    def test_geojson_content_negotiation_fixture_and_406_audit(self):
        """A server fixture serves GeoJSON only for an acceptable media range.

        It reproduces the observed 406 failure without reaching an API or
        pretending the diagnostic response is part of the research dataset.
        """
        class GeoJsonServer:
            def __init__(self):self.calls=0
            def open(self,request,timeout):
                self.calls+=1
                media_ranges={item.split(';',1)[0].strip() for item in request.get_header('Accept','').split(',')}
                if not media_ranges.intersection({'application/geo+json','application/*','*/*'}):
                    raise HTTPError(request.full_url,406,'Not Acceptable',{},io.BytesIO(b'unsupported response type'))
                response=MagicMock()
                response.status=200
                response.read.return_value=json.dumps(GEOJSON).encode()
                response.__enter__.return_value=response
                return response
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            server=GeoJsonServer()
            with patch.object(collect,'ROOT',root),patch.object(collect,'build_opener',return_value=server):
                good=collect.fetch('ors',ROUTE,'fake','accept-good','preflight',root/'data/preflight',['fake'])
            self.assertEqual(good['status'],'ok')
            self.assertEqual(good['http_code'],200)
            self.assertEqual(server.calls,1)
            wrong_request=collect.build_request('ors',ROUTE,'fake')
            wrong_request.add_header('Accept','application/json')
            with patch.object(collect,'ROOT',root),patch.object(collect,'build_opener',return_value=server),patch.object(collect,'build_request',return_value=wrong_request):
                failed=collect.fetch('ors',ROUTE,'fake','accept-bad','preflight',root/'data/preflight',['fake'])
            self.assertEqual(failed['status'],'http_error')
            self.assertEqual(failed['http_code'],406)
            self.assertEqual(failed['duration_s'],'')
            self.assertEqual(failed['raw_file'],'')
            self.assertEqual(server.calls,2)

    def test_incomplete_http_read_is_sanitized_transport_failure(self):
        opener=MagicMock()
        opener.open.side_effect=IncompleteRead(b'unit-test-secret',999)
        with patch.object(collect,'build_opener',return_value=opener):
            row=collect.fetch('ors',ROUTE,'unit-test-secret','test-cycle','pilot',Path('unused'),['unit-test-secret'])
        self.assertEqual(row['status'],'transport_error')
        self.assertEqual(row['error'],'TransportError')
        self.assertNotIn('unit-test-secret',json.dumps(row))
        self.assertEqual(opener.open.call_count,1)

    def test_reservation_consumes_budget_before_execution_and_rejects_replay(self):
        current=state()
        plan=collect.reserve_plan(current,config(),[ROUTE],'scheduled','cycle','hash')
        self.assertEqual(current['counts']['ors'],1)
        self.assertEqual(plan['status'],'reserved')
        with self.assertRaises(SystemExit):collect.reserve_plan(current,config(),[ROUTE],'scheduled','cycle','hash')
        current['counts']['ors']=3
        with self.assertRaises(SystemExit):collect.reserve_plan(current,config(),[ROUTE],'scheduled','new-cycle','hash')
        self.assertNotIn('new-cycle',current['reservations'])

    def test_unapproved_provider_never_reserved(self):
        settings=config()
        settings['providers']['ors']['collection_approved']=False
        current=state()
        with self.assertRaises(SystemExit):collect.reserve_plan(current,settings,[ROUTE],'scheduled','cycle','hash')
        self.assertEqual(current['counts'],{})

    def test_crash_after_reservation_keeps_budget_and_prevents_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            current=state()
            plan=collect.reserve_plan(current,config(),[ROUTE],'scheduled','cycle','hash')
            path=root/'data/collection_state.json'
            collect.atomic_state(path,current)
            with patch.object(collect,'ROOT',root),patch.object(collect,'load_keys',return_value={'ors':'fake'}),patch.object(collect,'fetch',side_effect=RuntimeError('simulated crash')):
                with self.assertRaises(RuntimeError):collect.execute_plan(current,plan,config(),path,'hash')
            stored=json.loads(path.read_text())
            self.assertEqual(stored['counts']['ors'],1)
            self.assertEqual(stored['reservations']['cycle']['status'],'started')
            with self.assertRaises(SystemExit):collect.execute_plan(stored,stored['reservations']['cycle'],config(),path,'hash')

    def test_config_change_stops_reserved_execution(self):
        current=state()
        plan=collect.reserve_plan(current,config(),[ROUTE],'scheduled','cycle','oldhash')
        with self.assertRaises(SystemExit):collect.execute_plan(current,plan,config(),Path('unused'),'newhash')

    def test_rate_limit_halts_following_routes_and_future_cycles(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            settings=config()
            current=state()
            plan=collect.reserve_plan(current,settings,[ROUTE,dict(ROUTE,route_id='TEST02')],'pilot','cycle','hash')
            path=root/'data/collection_state.json'
            failed=dict.fromkeys(collect.FIELDS,'')
            failed.update(provider='ors',http_code=429,status='http_error',route_id='TEST01')
            with patch.object(collect,'ROOT',root),patch.object(collect,'load_keys',return_value={'ors':'fake'}),patch.object(collect,'fetch',return_value=failed) as fetch,patch.object(collect,'output_flag'):
                collect.execute_plan(current,plan,settings,path,'hash')
            self.assertEqual(fetch.call_count,1)
            self.assertEqual(current['counts']['ors'],2)
            self.assertTrue(current['halted_reason'])
            with self.assertRaises(SystemExit):collect.reserve_plan(current,settings,[ROUTE],'scheduled','cycle2','hash')

    def test_atomic_state_and_local_concurrency_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(collect,'ROOT',root):
                with collect.collection_lock():
                    with self.assertRaises(SystemExit):
                        with collect.collection_lock():pass
                self.assertFalse((root/'data/.collector.lock').exists())
                path=root/'data/collection_state.json'
                collect.atomic_state(path,{'counts':{'ors':2}})
                self.assertEqual(json.loads(path.read_text())['counts']['ors'],2)
                self.assertFalse(list(path.parent.glob('*.tmp')))

    def test_workflow_persists_budget_before_http_and_uses_concurrency(self):
        workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/collect.yml').read_text()
        self.assertLess(workflow.index('Persist budget before network requests'),workflow.index('Collect raw measurements'))
        self.assertIn('git push origin HEAD:main',workflow[workflow.index('Persist budget before network requests'):workflow.index('Collect raw measurements')])
        self.assertIn('cancel-in-progress: false',workflow)
        self.assertIn('timeout-minutes: 5',workflow)
        self.assertNotIn('secrets.TOMTOM_KEY',workflow)
        self.assertNotIn('secrets.DGIS_KEY',workflow)


if __name__=='__main__':unittest.main()
