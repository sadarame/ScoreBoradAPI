"""Atomically deploy the tested score/notification release via existing FTPS."""
import ast
import ftplib
import hashlib
import io
import json
import ssl
from pathlib import Path
import certifi

FILES = ['main.py', 'Game.py', 'SportsData.py', 'Soccer.py', 'Notifications.py', 'FirestoreHandler.py']
ROOT = Path(__file__).resolve().parent.parent

def read(ftp, path):
    out = io.BytesIO()
    ftp.retrbinary('RETR ' + path, out.write)
    return out.getvalue()

def deploy():
    payload = {name: (ROOT / name).read_bytes() for name in FILES}
    for name, data in payload.items():
        ast.parse(data.decode(), filename=name, feature_version=(3, 7))
    digest = hashlib.sha256(b''.join(payload[name] for name in FILES)).hexdigest()[:16]
    release = '.scoreboard-releases/20261101-notifications-' + digest
    config = json.loads((ROOT / '.vscode/sftp.json').read_text())
    ftp = ftplib.FTP_TLS(context=ssl.create_default_context(cafile=certifi.where()), timeout=30)
    ftp.connect(config['host'], config.get('port', 21))
    ftp.login(config['username'], config['password']); ftp.prot_p(); ftp.cwd(config['remotePath'])
    try:
        original = read(ftp, 'main.py')
        expected = Path('/private/tmp/scoreboard-notification-check/remote-main.py').read_bytes()
        if original != expected:
            raise RuntimeError('Launcher changed since inspection; inspect before deploying')
        old_tail = "release = os.path.join(root, '.scoreboard-releases/20261026-score-recovery')\nsys.path.insert(0, release)\nrunpy.run_path(os.path.join(release, 'main.py'), run_name='__main__')\n"
        if original.decode().count(old_tail) != 1:
            raise RuntimeError('Unexpected launcher')
        tail = '''release = os.path.join(root, RELEASE_PATH)
sys.path.insert(0, release)
import logging
log_path = os.path.join(release, 'collector.log')
if os.path.exists(log_path) and os.path.getsize(log_path) > 2000000:
    os.replace(log_path, log_path + '.previous')
logging.basicConfig(filename=log_path, level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')
os.environ['SCOREBOARD_HEALTH_PATH'] = os.path.join(release, 'health.json')
# Validate server FCM credentials without delivering a notification.
try:
    marker = os.path.join(release, '.fcm-validated')
    if not os.path.exists(marker):
        from FirestoreHandler import FirestoreHandler
        from firebase_admin import messaging
        FirestoreHandler()
        messaging.send(messaging.Message(topic='scoreboard_delivery_validation',
                       notification=messaging.Notification(title='validation')), dry_run=True)
        with open(marker, 'w') as validated:
            validated.write('FCM dry-run accepted\\n')
        logging.info('FCM dry-run accepted; enabling automatic notifications')
    os.environ['ENABLE_PUSH'] = '1'
except Exception:
    os.environ['ENABLE_PUSH'] = '0'
    logging.exception('FCM validation failed; score collection will continue')
runpy.run_path(os.path.join(release, 'main.py'), run_name='__main__')
'''.replace('RELEASE_PATH', repr(release))
        updated = original.decode().replace(old_tail, tail).encode()
        ast.parse(updated.decode(), feature_version=(3, 7))
        ftp.mkd(release)
        ftp.storbinary('STOR ' + release + '/.htaccess', io.BytesIO(b'Require all denied\n'))
        for name, data in payload.items():
            ftp.storbinary('STOR ' + release + '/' + name, io.BytesIO(data))
            if read(ftp, release + '/' + name) != data:
                raise RuntimeError('Upload verification failed: ' + name)
        ftp.storbinary('STOR ' + release + '/launcher-before.py', io.BytesIO(original))
        if read(ftp, 'main.py') != original:
            raise RuntimeError('Concurrent launcher change; refusing switch')
        ftp.storbinary('STOR main.notifications-next.py', io.BytesIO(updated))
        ftp.sendcmd('SITE CHMOD 744 main.notifications-next.py')
        ftp.rename('main.notifications-next.py', 'main.py')
        if read(ftp, 'main.py') != updated:
            raise RuntimeError('Launcher verification failed')
        Path('/private/tmp/scoreboard-notification-check/release.txt').write_text(release)
        print('Deployed:', release)
        print('Rollback launcher:', release + '/launcher-before.py')
    finally:
        ftp.quit()

if __name__ == '__main__':
    deploy()
