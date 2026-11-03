"""NBA official injury reports -> versioned, read-only JSON. No Firestore writes.

Run independently of the score/notification worker. Python 3.7 compatible.
Reject unrecognized layouts rather than publish incorrect player/reason pairs.
"""
import argparse
import fcntl
import io
import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pdfplumber
import pytz
import requests

ET = pytz.timezone('America/New_York')
UTC = pytz.UTC
ROOT = 'https://ak-static.cms.nba.com/referee/injury/'
STATUSES = {'Out', 'Doubtful', 'Questionable', 'Probable', 'Available'}
TEAM_NAMES = dict(zip(
    'ATL BOS BKN CHA CHI CLE DAL DEN DET GSW HOU IND LAC LAL MEM MIA MIL MIN NOP NYK OKC ORL PHI PHX POR SAC SAS TOR UTA WAS'.split(),
    ['Atlanta Hawks', 'Boston Celtics', 'Brooklyn Nets', 'Charlotte Hornets',
     'Chicago Bulls', 'Cleveland Cavaliers', 'Dallas Mavericks', 'Denver Nuggets',
     'Detroit Pistons', 'Golden State Warriors', 'Houston Rockets', 'Indiana Pacers',
     'LA Clippers', 'Los Angeles Lakers', 'Memphis Grizzlies', 'Miami Heat',
     'Milwaukee Bucks', 'Minnesota Timberwolves', 'New Orleans Pelicans',
     'New York Knicks', 'Oklahoma City Thunder', 'Orlando Magic', 'Philadelphia 76ers',
     'Phoenix Suns', 'Portland Trail Blazers', 'Sacramento Kings', 'San Antonio Spurs',
     'Toronto Raptors', 'Utah Jazz', 'Washington Wizards']))
NAMES = {re.sub(r'\s+', '', v).lower(): k for k, v in TEAM_NAMES.items()}
NAMES['losangelesclippers'] = 'LAC'


def iso(value):
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def source_time(url):
    if not url.startswith(ROOT):
        raise ValueError('Not an NBA official report URL')
    m = re.fullmatch(r'Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2}_\d{2}[AP]M)\.pdf', url[len(ROOT):])
    if not m:
        raise ValueError('Invalid report URL')
    return ET.localize(datetime.strptime(' '.join(m.groups()), '%Y-%m-%d %I_%M%p'), is_dst=None)


def parse_rows(rows, report_at, url, fetched_at):
    """Rows are exact PDF cells. Context carries across page breaks only."""
    games, game, team, date = {}, None, None, None
    for cells in rows:
        day, clock, matchup, team_name, player, status, reason = cells
        if day:
            date = datetime.strptime(day, '%m/%d/%Y').strftime('%Y-%m-%d')
        if matchup:
            if not date or not re.fullmatch(r'[A-Z]{3}@[A-Z]{3}', matchup):
                raise ValueError('Missing game identity')
            away, home = matchup.split('@')
            if away not in TEAM_NAMES or home not in TEAM_NAMES or away == home:
                raise ValueError('Unknown matchup')
            key = date + '_' + matchup
            if key in games:
                raise ValueError('Duplicate matchup')
            game = dict(gameDateEt=date, away=away, home=home, teams={})
            games[key] = game
            team = None
        if team_name:
            code = NAMES.get(re.sub(r'\s+', '', team_name).lower())
            if game is None or code not in (game['home'], game['away']):
                raise ValueError('Team outside matchup')
            if code in game['teams']:
                raise ValueError('Duplicate team')
            team = dict(team=code, submission='submitted', players=[])
            game['teams'][code] = team
        if team is None:
            raise ValueError('Player without team')
        if re.sub(r'\s+', '', reason).upper() == 'NOTYETSUBMITTED':
            if player or status or team['players']:
                raise ValueError('Conflicting submission state')
            team['submission'] = 'pending'
        elif player:
            if status not in STATUSES or not reason or team['submission'] != 'submitted':
                raise ValueError('Invalid player status or reason')
            if any(p['name'] == player for p in team['players']):
                raise ValueError('Duplicate player')
            team['players'].append(dict(name=player, status=status, reason=reason))
        else:
            # Do not interpret a lost player cell as "no injuries".
            raise ValueError('Unrecognized report row')
    if not games or any(len(g['teams']) != 2 for g in games.values()):
        raise ValueError('Incomplete matchups')
    return dict(schemaVersion=1, league='NBA', reportAt=iso(report_at),
                fetchedAt=iso(fetched_at), sourceUrl=url, games=list(games.values()))


def parse_pdf(raw, url, fetched_at):
    expected = source_time(url)
    rows = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        if not 1 <= len(pdf.pages) <= 40:
            raise ValueError('Unexpected page count')
        for page in pdf.pages:
            if abs(page.width - 841.95) > 2 or abs(page.height - 595.35) > 2:
                raise ValueError('Report layout changed')
            text = page.extract_text(x_tolerance=1) or ''
            header = re.search(r'Injury Report:\s*(\d{2}/\d{2}/\d{2}\s+\d{2}:\d{2}\s+[AP]M)', text)
            if not header or datetime.strptime(header[1], '%m/%d/%y %I:%M %p') != expected.replace(tzinfo=None):
                raise ValueError('Report timestamp mismatch')
            if page.page_number == 1 and 'Game Date Game Time Matchup Team Player Name Current Status Reason' not in text:
                raise ValueError('Report columns changed')
            # Every player row ends in a horizontal line across the Reason column.
            edges = sorted(set(round(line['top'], 2) for line in page.lines
                if line['x0'] <= 670 and line['x1'] >= 820 and
                abs(line['top'] - line['bottom']) < .1 and 78 < line['top'] < 525))
            if not edges:
                raise ValueError('Missing row boundaries')
            top = 123 if page.page_number == 1 else 78
            xs = [19, 116, 196, 260, 421, 582, 662, 824]
            for bottom in edges:
                if bottom <= top:
                    continue
                cells = [' '.join((page.crop((xs[i], top, xs[i+1], bottom)).extract_text(
                    x_tolerance=1) or '').split()) for i in range(7)]
                if any(cells):
                    if not any(cells[:6]) and rows and rows[-1][4]:
                        rows[-1][6] += ' ' + cells[6]
                    else:
                        rows.append(cells)
                top = bottom
            # A new row below the last detected border must not be silently lost.
            if (page.crop((19, top + .1, 824, 525)).extract_text() or '').strip():
                raise ValueError('Unterminated report row')
    return parse_rows(rows, expected, url, fetched_at)


def download(url):
    source_time(url)  # fixed HTTPS host/path; redirects not accepted
    with requests.get(url, timeout=(5, 15), stream=True, allow_redirects=False) as r:
        if r.status_code == 404:
            return None
        r.raise_for_status()
        if r.status_code != 200:
            raise ValueError('Unexpected response')
        chunks, size = [], 0
        for part in r.iter_content(65536):
            size += len(part)
            if size > 3000000:
                raise ValueError('PDF too large')
            chunks.append(part)
        raw = b''.join(chunks)
        if not raw.startswith(b'%PDF-'):
            raise ValueError('Not PDF')
        return raw


def collect(now=None, fetch=download):
    now = now or datetime.now(UTC)
    # The official index may deny automated requests. Use the official 15-minute
    # publication naming convention, bounded to 2 hours. Never bypass a 403.
    current = now.astimezone(ET).replace(second=0, microsecond=0)
    current -= timedelta(minutes=current.minute % 15)
    for i in range(9):
        stamp = ET.normalize(current - timedelta(minutes=15*i))
        url = ROOT + stamp.strftime('Injury-Report_%Y-%m-%d_%I_%M%p.pdf')
        raw = fetch(url)
        if raw is not None:
            return parse_pdf(raw, url, now)
    raise RuntimeError('No recent official report available; previous snapshot retained')


def save(snapshot, output):
    output = Path(output)
    if output.exists():
        previous = json.loads(output.read_text(encoding='utf-8'))
        if previous['reportAt'] > snapshot['reportAt']:
            raise ValueError('Refusing older snapshot')
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(output.parent), prefix='.availability-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, ensure_ascii=False, separators=(',', ':'))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, str(output))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, help='Local JSON destination; no remote deployment')
    parser.add_argument('--pdf', help='Local PDF for offline validation')
    parser.add_argument('--source-url', help='Official URL of the local PDF')
    args = parser.parse_args()
    if bool(args.pdf) != bool(args.source_url):
        parser.error('--pdf and --source-url must be provided together')
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with (output.parent / ('.' + output.name + '.lock')).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Availability collector already running')
            return
        snapshot = parse_pdf(Path(args.pdf).read_bytes(), args.source_url, datetime.now(UTC)) if args.pdf else collect()
        save(snapshot, output)
        print('Saved {} matchups, report {}'.format(len(snapshot['games']), snapshot['reportAt']))


if __name__ == '__main__':
    main()
