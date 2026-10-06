"""Browser regression: mocked loopback SDK, actual Streamlit component roundtrip.

No real biometric data, hosted service, database or connected sensor is used.
Requires playwright + installed Edge; launches its own loopback test server.
"""
import base64
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['DATABASE_URL']=''
from PIL import Image
from playwright.sync_api import sync_playwright, expect
from test_sdk_matcher import template


def main():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    env=dict(os.environ,DATABASE_URL='',MANTRA_SENSOR_TRANSPORT='bridge')
    with tempfile.TemporaryDirectory(prefix='frs-sdk-check-') as directory:
        log_path=Path(directory)/'server.log'
        with log_path.open('w',encoding='utf-8') as log:
            server=subprocess.Popen([sys.executable,'-m','streamlit','run','tests/frs_ui_fixture.py',
                '--server.address','127.0.0.1','--server.port',str(port),
                '--server.headless','true','--browser.gatherUsageStats','false'],
                cwd=ROOT,env=env,stdout=log,stderr=log,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                for _ in range(100):
                    try:
                        urllib.request.urlopen(f'http://127.0.0.1:{port}/_stcore/health',timeout=.5);break
                    except Exception:time.sleep(.1)
                image=io.BytesIO();Image.new('L',(100,120),200).save(image,format='PNG')
                encoded=base64.b64encode(image.getvalue()).decode()
                count=0;fail=False
                with sync_playwright() as pw:
                    browser=pw.chromium.launch(channel='msedge',headless=True)
                    page=browser.new_page(viewport={'width':1280,'height':900})
                    def mock(route):
                        nonlocal count
                        request=route.request
                        headers={'Access-Control-Allow-Origin':request.headers.get('origin','*'),
                                 'Access-Control-Allow-Headers':'Content-Type, X-FRS-Bridge-Token',
                                 'Access-Control-Allow-Methods':'GET, POST, OPTIONS',
                                 'Access-Control-Allow-Private-Network':'true'}
                        if request.method=='OPTIONS':route.fulfill(status=204,headers=headers);return
                        if request.url.endswith('/status'):
                            payload={'ok':True,'protocol_version':2,'matcher':'Mantra MFS100 MatchISO','model':'Test scanner'}
                        elif request.url.endswith('/capture'):
                            count+=1
                            payload={'ok':True,'protocol_version':2,'iso_template':template(),
                                'image_base64':encoded,'width':100,'height':120,'quality':70,'capture_id':f'test-{count}'}
                        else:
                            body=json.loads(request.post_data)
                            assert body['probe']==template()
                            assert body['candidates'][0]['user_id']=='USR-001'
                            payload={'ok':not fail,'engine':'Mantra MFS100 MatchISO','request_id':body['request_id'],
                                     'scores':[{'user_id':'USR-001','score':1700}], 'error':'Test SDK failure'}
                        route.fulfill(status=200,headers=headers,content_type='application/json',body=json.dumps(payload))
                    page.route('http://127.0.0.1:8766/**',mock)
                    page.goto(f'http://127.0.0.1:{port}')
                    button=page.get_by_role('button',name='Sign out',exact=True)
                    button.wait_for(timeout=30000)
                    assert page.get_by_text('Supabase',exact=True).count()==0
                    assert page.locator('[data-testid="stException"]').count()==0
                    bounds=button.bounding_box()
                    header=page.locator('[data-testid="stHeader"]').bounding_box()
                    assert bounds['y'] >= header['y']+header['height'],(bounds,header)
                    point=page.evaluate('''() => {const b=document.querySelector('.st-key-frs_sign_out button');
                      const r=b.getBoundingClientRect();return b.contains(document.elementFromPoint(r.x+r.width/2,r.y+2));}''')
                    assert point,'Sign-out top is obscured'
                    artifacts=ROOT/'tests/_artifacts';artifacts.mkdir(exist_ok=True)
                    page.screenshot(path=str(artifacts/'header-desktop.png'))
                    page.get_by_role('tab',name='Identify',exact=True).click()
                    frame=page.get_by_role('tabpanel',name='Identify').locator('iframe').content_frame
                    frame.get_by_label('Bridge pairing code').fill('test-pairing')
                    frame.get_by_role('button',name='Connect',exact=True).click()
                    frame.get_by_role('button',name='Capture fingerprint').wait_for()
                    frame.get_by_role('button',name='Capture fingerprint').click()
                    page.locator('.frs-result-name').filter(has_text='Synthetic Evaluator').wait_for(timeout=15000)
                    assert 'SDK score = 1700' in page.locator('.frs-result-meta').inner_text()
                    fail=True
                    frame.get_by_role('button',name='Capture fingerprint').click()
                    frame.get_by_text('Test SDK failure',exact=True).wait_for(timeout=10000)
                    expect(page.locator('.frs-result-name')).to_have_count(0,timeout=15000)
                    page.set_viewport_size({'width':390,'height':844})
                    page.wait_for_timeout(400)
                    assert button.bounding_box()['y']>=50
                    assert page.locator('[data-testid="stException"]').count()==0
                    page.screenshot(path=str(artifacts/'header-mobile.png'))
                    browser.close()
                print('PASS: header unclipped, badge removed, hosted capture→MatchISO roundtrip, failure clears old identity, narrow viewport')
            finally:
                server.terminate()
                try:server.wait(timeout=10)
                except subprocess.TimeoutExpired:server.kill();server.wait()
        if server.returncode not in (0,1,-15):
            print('Test server stopped.')


if __name__=='__main__':main()
