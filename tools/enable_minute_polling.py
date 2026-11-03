"""Extend the existing five-minute Cron launcher with bounded minute polling."""
import ast
import ftplib
import hashlib
import io
import json
import re
import ssl
from pathlib import Path
import certifi
from deploy_notifications import read

root = Path(__file__).resolve().parent.parent
config = json.loads((root / '.vscode/sftp.json').read_text())
ftp = ftplib.FTP_TLS(context=ssl.create_default_context(cafile=certifi.where()), timeout=30)
ftp.connect(config['host'], config.get('port', 21))
ftp.login(config['username'], config['password']); ftp.prot_p(); ftp.cwd(config['remotePath'])
try:
    original = read(ftp, 'main.py')
    text = original.decode()
    match = re.search(r"release = os.path.join\(root, '([.a-zA-Z0-9_/-]+)'\)", text)
    if not match or '/20261101-notifications-' not in match[1]:
        raise RuntimeError('Unexpected current release')
    release = match[1]
    previous = "runpy.run_path(os.path.join(release, 'main.py'), run_name='__main__')"
    updated = text.replace(previous, "os.environ['SCOREBOARD_POLL_LOCK'] = os.path.join(root, '.scoreboard-releases/.minute-poll.lock')\nrunpy.run_path(os.path.join(release, 'notification_runner.py'), run_name='__main__')")
    if text.count(previous) != 1:
        raise RuntimeError('Unexpected entrypoint')
    runner = (root / 'notification_runner.py').read_bytes()
    for source in (runner.decode(), updated): ast.parse(source, feature_version=(3, 7))
    ftp.storbinary('STOR ' + release + '/notification_runner.py', io.BytesIO(runner))
    if read(ftp, release + '/notification_runner.py') != runner:
        raise RuntimeError('Runner verification failed')
    backup = release + '/launcher-before-minute-' + hashlib.sha256(original).hexdigest()[:16] + '.py'
    ftp.storbinary('STOR ' + backup, io.BytesIO(original))
    if read(ftp, 'main.py') != original: raise RuntimeError('Concurrent launcher change')
    ftp.storbinary('STOR main.minute-next.py', io.BytesIO(updated.encode()))
    ftp.sendcmd('SITE CHMOD 744 main.minute-next.py'); ftp.rename('main.minute-next.py', 'main.py')
    if read(ftp, 'main.py').decode() != updated: raise RuntimeError('Launcher verification failed')
    print('Enabled minute polling:', release)
    print('Rollback:', backup)
finally:
    ftp.quit()
