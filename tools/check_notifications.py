"""Read the active notification release health and recent logs via FTPS."""
import ftplib
import json
import re
import ssl
from pathlib import Path
import certifi
from deploy_notifications import read


def main():
    root = Path(__file__).resolve().parent.parent
    config = json.loads((root / '.vscode/sftp.json').read_text())
    ftp = ftplib.FTP_TLS(context=ssl.create_default_context(cafile=certifi.where()), timeout=30)
    ftp.connect(config['host'], config.get('port', 21))
    ftp.login(config['username'], config['password']); ftp.prot_p(); ftp.cwd(config['remotePath'])
    try:
        launcher = read(ftp, 'main.py').decode()
        match = re.search(r"release = os.path.join\(root, '([.a-zA-Z0-9_/-]+)'\)", launcher)
        if not match or not match[1].startswith('.scoreboard-releases/'):
            raise RuntimeError('Unknown active release')
        release = match[1]
        print('Active release:', release)
        print('Minute polling:', 'notification_runner.py' in launcher)
        for name in ['health.json', '.fcm-validated', 'collector.log']:
            try:
                value = read(ftp, release + '/' + name).decode()
                print(name + ':\n' + '\n'.join(value.splitlines()[-25:]))
            except ftplib.error_perm:
                print(name + ': not present')
    finally:
        ftp.quit()


if __name__ == '__main__':
    main()
