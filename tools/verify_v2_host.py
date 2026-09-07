"""Launch/stop the explicit Linux host with disposable, inactive credentials."""

import http.client
import json
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

from twitchbot.application.workers import ProcessLease


def run():
    with TemporaryDirectory(prefix='host-qa-') as directory:
        root = Path(directory)
        (root/'stage').mkdir()
        (root/'credentials.json').write_text(json.dumps(dict(client_id='fixture', user_id='123', access_token='synthetic-token')))
        (root/'runtime.json').write_text(json.dumps(dict(database_path=str(root/'core.sqlite3'), staging_root=str(root/'stage'), channel_id='123', credentials_file=str(root/'credentials.json'))))
        sock = root/'server.sock'
        process = subprocess.Popen([sys.executable, '-m', 'twitchbot.serve', '--configuration', str(root/'runtime.json'), '--bind', 'unix:'+str(sock)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(60):
                if process.poll() is not None: raise RuntimeError('fixture host exited')
                try:
                    connection = http.client.HTTPConnection('localhost', timeout=2)
                    connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    connection.sock.settimeout(2); connection.sock.connect(str(sock))
                    connection.request('GET', '/api/v2/operations')
                    response = connection.getresponse(); data = json.loads(response.read()); connection.close()
                    if response.status == 200 and data['runtime']['ready']:
                        assert not data['enabled'] and data['connection']['state'] == 'not_validated'
                        break
                except OSError:
                    pass
                time.sleep(.1)
            else: raise RuntimeError('fixture host did not become ready')
        finally:
            process.terminate()
            try: process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
                raise RuntimeError('fixture shutdown timed out') from None
        lease = ProcessLease(root/'.v2-runtime.lock'); lease.acquire(); lease.release()
        return {'host': 'started_inactive', 'shutdown': 'lease_released', 'twitch': 'not_validated'}


if __name__ == '__main__':
    print(json.dumps(run()))
