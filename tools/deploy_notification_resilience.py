"""Deploy quota-resilient polling, preserving active collectors and news.
Dry-run by default; --apply atomically switches the launcher after verification.
"""
import ast
import ftplib
import hashlib
import io
import json
import re
import ssl
import sys
from pathlib import Path
import certifi
from deploy_notifications import read

ROOT = Path(__file__).resolve().parent.parent
CHANGED = ['main.py', 'Notifications.py', 'LocalState.py', 'DurableNotifications.py', 'EfficientFirestore.py']
PRESERVED = ['Game.py', 'SportsData.py', 'Soccer.py', 'FirestoreHandler.py', 'notification_runner.py']


def main():
    config = json.loads((ROOT / '.vscode/sftp.json').read_text())
    ftp = ftplib.FTP_TLS(context=ssl.create_default_context(cafile=certifi.where()), timeout=30)
    ftp.connect(config['host'], config.get('port', 21))
    ftp.login(config['username'], config['password'])
    ftp.prot_p()
    ftp.cwd(config['remotePath'])
    try:
        before = read(ftp, 'main.py')
        match = re.search(r"release = os.path.join\(root, '([.a-zA-Z0-9_/-]+)'\)", before.decode())
        if not match or match[1] not in ('.scoreboard-releases/20261101-notifications-9f0d44f8f4c790cf', '.scoreboard-releases/20261103-push-resilience-78d5c90eea7f2b86', '.scoreboard-releases/20261103-push-resilience-b4d3a0ac3fa011cb'):
            raise RuntimeError('Active release changed; inspect before deployment')
        old = match[1]
        if '20261101-notifications' in old:
            for name in ['main.py', 'Notifications.py']:
                if read(ftp, old + '/' + name) != (Path('/private/tmp/scoreboard-push-20261103') / name).read_bytes():
                    raise RuntimeError('Remote source differs from inspected baseline: ' + name)
        else:
            manifest = json.loads(read(ftp, old + '/manifest.json'))
            for name in CHANGED + PRESERVED:
                if hashlib.sha256(read(ftp, old + '/' + name)).hexdigest() != manifest[name]:
                    raise RuntimeError('Deployed source changed: ' + name)
        files = {name: read(ftp, old + '/' + name) for name in PRESERVED}
        files.update({name: (ROOT / name).read_bytes() for name in CHANGED})
        for name, data in files.items():
            ast.parse(data.decode(), filename=name, feature_version=(3, 7))
        digest = hashlib.sha256(b''.join(files[name] for name in sorted(files))).hexdigest()[:16]
        release = '.scoreboard-releases/20261103-push-resilience-' + digest
        launcher = before.decode().replace(repr(old), repr(release))
        anchor = 'sys.path.insert(0, release)'
        if launcher.count(anchor) != 1:
            raise RuntimeError('Unexpected launcher')
        if "os.environ['SCOREBOARD_STATE_PATH']" not in launcher:
            launcher = launcher.replace(anchor,
                "os.umask(0o077)\nos.environ['SCOREBOARD_STATE_PATH'] = os.path.join(root, '.scoreboard-state', 'collector.sqlite3')\n" + anchor)
        ast.parse(launcher, feature_version=(3, 7))
        print('Proposed release:', release, flush=True)
        print('Preserved active collectors and existing news launcher.', flush=True)
        if '--apply' not in sys.argv:
            return
        try:
            ftp.mkd('.scoreboard-state')
        except ftplib.error_perm as exc:
            if not str(exc).startswith('550'):
                raise
        ftp.storbinary('STOR .scoreboard-state/.htaccess', io.BytesIO(b'Require all denied\n'))
        ftp.sendcmd('SITE CHMOD 700 .scoreboard-state')
        ftp.mkd(release)
        files['.htaccess'] = b'Require all denied\n'
        files['launcher-before.py'] = before
        files['manifest.json'] = json.dumps({name: hashlib.sha256(data).hexdigest() for name, data in files.items()}, indent=2).encode()
        for name, data in files.items():
            ftp.storbinary('STOR ' + release + '/' + name, io.BytesIO(data))
            if read(ftp, release + '/' + name) != data:
                raise RuntimeError('Verification failed: ' + name)
        if read(ftp, 'main.py') != before:
            raise RuntimeError('Concurrent launcher change; refusing switch')
        ftp.storbinary('STOR main.resilience-next.py', io.BytesIO(launcher.encode()))
        ftp.sendcmd('SITE CHMOD 744 main.resilience-next.py')
        ftp.rename('main.resilience-next.py', 'main.py')
        if read(ftp, 'main.py') != launcher.encode():
            raise RuntimeError('Launcher verification failed')
        print('Deployed:', release)
        print('Rollback launcher:', release + '/launcher-before.py')
        print('Keep .scoreboard-state/collector.sqlite3 across future releases.')
    finally:
        ftp.quit()


if __name__ == '__main__':
    main()
