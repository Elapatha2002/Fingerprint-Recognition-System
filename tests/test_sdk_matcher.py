"""Synthetic protocol fixtures only: no personal biometric samples or live DB."""
import base64
import io
import os
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from demo_matcher import matcher, sdk_matcher as sdk, sensor
from tools.mantra_bridge.protocol import iso_template, validate_match_request


def template():
    # Structural test fixture, NOT a fingerprint and never passed to native SDK.
    raw = b'FMR\x00 20\x00' + (32).to_bytes(4, 'big') + b'\0' * 20
    return base64.b64encode(raw).decode()


class SDKMatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patchers = [patch.object(matcher, 'DATABASE_URL', ''),
                         patch.object(matcher, 'ENROL_DIR', root),
                         patch.object(matcher, 'INDEX_PATH', root/'index.json'),
                         patch.dict(os.environ, {'FRS_MANTRA_MATCH_THRESHOLD': '1400'})]
        for p in self.patchers:p.start()

    def tearDown(self):
        for p in reversed(self.patchers):p.stop()
        self.temp.cleanup()

    def enrolled(self, count=2):
        for i in range(count):sdk.enrol(f'USR-{i+1:03}', f'Person {i+1}', template(), 'Right index')
        return sdk.gallery()

    def response(self, values):
        return {'ok':True, 'request_id':'a'*64, 'engine':sdk.ENGINE,
                'scores':[{'user_id':f'USR-{i+1:03}', 'score':s} for i,s in enumerate(values)]}

    def test_enrol_roundtrip_fits_existing_schema(self):
        packed=sdk.pack_template(template())
        self.assertGreaterEqual(len(packed),1024)
        self.assertEqual(sdk.unpack_template(packed),template())
        current=self.enrolled(1)
        self.assertEqual(current.candidates[0]['template'],template())
        self.assertEqual(len(current.legacy),0)

    def test_only_one_passing_identity(self):
        result=sdk.decide(self.response([1700,50]),self.enrolled(),'a'*64)
        self.assertTrue(result.matched)
        self.assertEqual(result.user_id,'USR-001')
        self.assertEqual(result.score,1700)

    def test_native_threshold_boundary(self):
        current=self.enrolled(1)
        self.assertFalse(sdk.decide(self.response([1399]),current,'a'*64).matched)
        self.assertTrue(sdk.decide(self.response([1400]),current,'a'*64).matched)

    def test_multiple_passers_and_ties_never_assign_identity(self):
        current=self.enrolled()
        for scores in ([2000,1500],[1800,1800]):
            result=sdk.decide(self.response(scores),current,'a'*64)
            self.assertFalse(result.matched)
            self.assertEqual(result.reason,'ambiguous')
            self.assertIsNone(result.user_id)

    def test_below_threshold_is_not_spoof_verdict(self):
        result=sdk.decide(self.response([20,40]),self.enrolled(),'a'*64)
        self.assertEqual(result.reason,'below_threshold')
        self.assertIsNone(result.user_id)

    def test_incomplete_duplicate_negative_or_noninteger_scores_rejected(self):
        current=self.enrolled()
        bad=[self.response([100]),self.response([100,-1]),self.response([100,True]),
             self.response([100,0.9])]
        duplicate=self.response([100,100]);duplicate['scores'][1]['user_id']='USR-001';bad.append(duplicate)
        for payload in bad:
            with self.assertRaises(ValueError):sdk.decide(payload,current,'a'*64)

    def test_stale_or_wrong_engine_rejected(self):
        current=self.enrolled()
        with self.assertRaises(ValueError):sdk.decide(self.response([200,10]),current,'b'*64)
        payload=self.response([200,10]);payload['engine']='ORB'
        with self.assertRaises(ValueError):sdk.decide(payload,current,'a'*64)

    def test_legacy_preserved_and_recaptured_with_same_id(self):
        matcher.ENROL_DIR.mkdir(exist_ok=True)
        path=matcher.ENROL_DIR/'USR-001.npz';path.write_bytes(b'PK\x03\x04legacy')
        entry=matcher.Enrolment('USR-001','Existing','USR-001.npz','2026-10-01','Right index')
        matcher._save_local_index({'USR-001':entry})
        old=sdk.gallery()
        self.assertEqual(len(old.legacy),1)
        self.assertEqual(old.candidates,[])
        sdk.enrol('USR-001','Existing',template(),'Right index',replace=True)
        self.assertEqual(len(sdk.gallery().candidates),1)
        self.assertTrue(path.exists())
        self.assertEqual(matcher.count_enrolled(),1)

    def test_existing_id_cannot_be_overwritten_without_confirmation(self):
        self.enrolled(1)
        with self.assertRaises(ValueError):sdk.enrol('USR-001','Other',template(),'Right index')
        self.assertEqual(matcher.list_enrolments()[0].display_name,'Person 1')

    def test_bad_templates_and_path_ids_rejected(self):
        for value in ('bad',base64.b64encode(b'not iso').decode()):
            with self.assertRaises(ValueError):iso_template(value)
        with self.assertRaises(ValueError):sdk.enrol('../elsewhere','One',template(),'Right index')

    def test_request_validation(self):
        request={'request_id':'a'*64,'probe':template(),'candidates':[{'user_id':'USR-001','template':template()}]}
        self.assertEqual(validate_match_request(request),request)
        request['candidates']*=2
        with self.assertRaises(ValueError):validate_match_request(request)

    def test_old_orb_threshold_is_not_reused(self):
        with patch.dict(os.environ,{'MATCH_THRESHOLD':'0.1'}):self.assertEqual(sdk.threshold(),1400)
        with patch.dict(os.environ,{'FRS_MANTRA_MATCH_THRESHOLD':'0.55'}):
            with self.assertRaises(ValueError):sdk.threshold()

    def test_capture_requires_iso_and_new_protocol(self):
        with self.assertRaises(sensor.SensorError):sensor.from_bridge_payload({'ok':True})
        stream=io.BytesIO();Image.new('L',(100,100)).save(stream,format='PNG')
        payload={'protocol_version':2,'iso_template':template(),
                 'image_base64':base64.b64encode(stream.getvalue()).decode(),'width':100,'height':100}
        self.assertEqual(sensor.from_bridge_payload(payload).iso_template,template())

    def test_direct_matching_passes_templates_via_helper(self):
        with patch.object(sensor,'capture_transport',return_value='direct'), patch.object(sensor,'_run_helper',return_value={'ok':True}) as run:
            sensor.match_templates(template(),self.enrolled(1).candidates,'a'*64)
            self.assertEqual(run.call_args.args[0],'match')
            self.assertEqual(run.call_args.args[2]['probe'],template())

    def test_hosted_server_does_not_attempt_windows_matching(self):
        with patch.object(sensor,'capture_transport',return_value='bridge'):
            with self.assertRaises(sensor.SensorError):sensor.match_templates(template(),[],'a'*64)

    def test_private_database_iso_insert_and_read_without_schema_change(self):
        from unittest.mock import MagicMock
        connection=MagicMock()
        @contextmanager
        def connected():yield connection
        with patch.object(matcher,'DATABASE_URL','postgresql://unused'), patch.object(matcher,'_database_connection',connected):
            sdk.enrol('USR-001','One',template(),'Right index')
            query,params=connection.execute.call_args.args
            self.assertIn('INSERT INTO fingerprint_demo.enrolments',query)
            self.assertEqual(sdk.unpack_template(params[3]),template())
            connection.execute.return_value.fetchall.return_value=[{
                'user_id':'USR-001','display_name':'One','finger_label':'Right index',
                'enrolled_at':datetime.now(timezone.utc),'template':params[3]}]
            self.assertEqual(sdk.gallery().candidates[0]['template'],template())

    def test_missing_database_recapture_target_fails_without_insert(self):
        from unittest.mock import MagicMock
        connection=MagicMock();connection.execute.return_value.fetchone.return_value=None
        @contextmanager
        def connected():yield connection
        with patch.object(matcher,'DATABASE_URL','postgresql://unused'), patch.object(matcher,'_database_connection',connected):
            with self.assertRaisesRegex(ValueError,'no longer exists'):
                sdk.enrol('USR-001','One',template(),'Right index',replace=True)
            self.assertIn('UPDATE fingerprint_demo.enrolments',connection.execute.call_args.args[0])

    def test_gallery_revision_changes_after_name_update(self):
        before=self.enrolled(1).revision
        sdk.enrol('USR-001','Changed',template(),'Right index',replace=True)
        self.assertNotEqual(before,sdk.gallery().revision)


if __name__=='__main__':unittest.main()
