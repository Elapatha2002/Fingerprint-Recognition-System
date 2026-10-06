import http.client
import json
import threading
import unittest
from unittest.mock import patch

from tools.mantra_bridge import bridge
from tools.mantra_bridge.protocol import MAX_REQUEST_BYTES
from test_sdk_matcher import template


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.server=bridge.BridgeServer(('127.0.0.1',0),bridge.Handler,token='test-pairing',
                                       origins={'https://example.test'},timeout=5)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.sdk=patch.object(bridge,'run_sdk',return_value=(200,{'ok':True,'scores':[]}))
        self.mock=self.sdk.start()

    def tearDown(self):
        self.sdk.stop();self.server.shutdown();self.server.server_close();self.thread.join()

    def request(self,path='/match',payload=None,headers=None):
        conn=http.client.HTTPConnection(*self.server.server_address,timeout=3)
        heads={'Origin':'https://example.test','X-FRS-Bridge-Token':'test-pairing','Content-Type':'application/json'}
        if headers:heads.update(headers)
        data=json.dumps(payload if payload is not None else {})
        conn.request('POST',path,data,headers=heads)
        response=conn.getresponse();result=(response.status,json.loads(response.read()))
        conn.close();return result

    def valid(self):
        return {'request_id':'a'*64,'probe':template(),'candidates':[{'user_id':'USR-001','template':template()}]}

    def test_authorised_iso_matching(self):
        self.assertEqual(self.request(payload=self.valid())[0],200)
        self.mock.assert_called_once_with('match',60,self.valid())

    def test_bad_origin_or_token_never_reaches_sdk(self):
        self.assertEqual(self.request(payload=self.valid(),headers={'Origin':'https://evil.test'})[0],403)
        self.assertEqual(self.request(payload=self.valid(),headers={'X-FRS-Bridge-Token':'wrong'})[0],401)
        self.mock.assert_not_called()

    def test_malformed_or_large_body_rejected(self):
        self.assertEqual(self.request(payload={'probe':'invalid'})[0],400)
        self.assertEqual(self.request(headers={'Content-Length':str(MAX_REQUEST_BYTES+1)})[0],413)
        self.assertEqual(self.request(headers={'Content-Length':'-1'})[0],413)
        self.mock.assert_not_called()

    def test_capture_retains_existing_route(self):
        self.assertEqual(self.request('/capture')[0],200)
        self.mock.assert_called_once_with('capture',5)

    def test_busy_matching_rejected_without_parallel_sdk(self):
        bridge.CAPTURE_LOCK.acquire()
        try:self.assertEqual(self.request(payload=self.valid())[0],409)
        finally:bridge.CAPTURE_LOCK.release()
        self.mock.assert_not_called()


if __name__=='__main__':unittest.main()
