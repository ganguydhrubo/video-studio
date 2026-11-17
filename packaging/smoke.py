"""Check a frozen app without downloads, AI accounts or a browser."""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

def main():
    executable = str(Path(sys.argv[1]).resolve())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix='vs-package-check-') as temporary:
        with (Path(temporary) / 'server.log').open('w') as log:
            proc = subprocess.Popen([executable, 'studio', '--port', '7894', '--no-browser', '--workspace', str(Path(temporary)/'projects')],
                env={**os.environ, 'VIDEO_STUDIO_HOME':temporary}, stdout=log, stderr=subprocess.STDOUT)
        try:
            for _ in range(100):
                try:
                    with opener.open('http://127.0.0.1:7894/api/health',timeout=.5) as response:
                        health=json.load(response)
                    break
                except OSError:
                    if proc.poll() is not None:
                        raise RuntimeError((Path(temporary)/'server.log').read_text())
                    time.sleep(.1)
            else:
                raise RuntimeError('Packaged server did not start')
            assert health['app']=='VideoStudio'
            with opener.open('http://127.0.0.1:7894/') as response:
                html=response.read().decode()
            assert 'AI_ICONS=' in html and 'id="quest"' in html and '/brief-assistant.js' in html and '__TOKEN__' not in html
            with opener.open('http://127.0.0.1:7894/brief-assistant.js') as response:
                assert b'/api/brief/interpret' in response.read()
            assert 'id="mPhotos"' in html and 'id="machineCard"' in html and 'id="updateCard"' in html
            assert '/autopilot.js' in html and '/understand.js' in html and 'id="mGenerate"' not in html
    assert 'function wireShare' in html and 'id="tDownload"' in html and 'function fixPreview' in html
            with opener.open('http://127.0.0.1:7894/api/library') as response:
                lib=json.load(response)
            assert lib['counts']['templates']>=20 and lib['counts']['looks']>=15 and lib['counts']['transitions']>=50
            with opener.open('http://127.0.0.1:7894/api/projects') as response:
                assert json.load(response)['projects']==[]
            try:
                opener.open(urllib.request.Request('http://127.0.0.1:7894/api/pick-files',data=b'{}'))
                raise AssertionError('Write without token accepted')
            except urllib.error.HTTPError as error:
                assert error.code==403
            print('Packaged app passed:',health['version'])
        finally:
            proc.terminate()
            proc.wait(timeout=15)

if __name__=='__main__':
    main()
