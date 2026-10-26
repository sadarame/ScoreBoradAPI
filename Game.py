"""Validated score collectors, compatible with deployed Python 3.7."""
import datetime
import logging
import time
import requests
import pytz
from bs4 import BeautifulSoup

NBA_URL = 'https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json'
ESPN_URL = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard'
NPB_URL = 'https://baseball.yahoo.co.jp/npb/schedule/'
LOG = logging.getLogger(__name__)
# Match existing app searchNM values, including Charlotte's CHO.
ALIASES = {'NY': 'NYK', 'GS': 'GSW', 'SA': 'SAS', 'NO': 'NOP',
           'UTAH': 'UTA', 'WSH': 'WAS', 'PHO': 'PHX', 'CHA': 'CHO'}


def request(url, as_json=True):
    for attempt in range(3):
        response = None
        try:
            response = requests.get(url, timeout=(5, 15))
            response.raise_for_status()
            return response.json() if as_json else response.text
        except (requests.RequestException, ValueError):
            if response is not None and response.status_code in (401, 403, 404):
                raise
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def team_code(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Missing team code')
    return ALIASES.get(value, value)


def score(value):
    if value is None or value == '':
        return ''
    text = str(value)
    if not text.isdigit():
        raise ValueError('Invalid score')
    return text


def row(home, away, hs, aws, status, league, match_id, source, now):
    if not home or not away or not isinstance(status, str) or not status:
        raise ValueError('Incomplete match')
    return dict(homeTeamName=home, awayTeamName=away, homeTeamScore=score(hs),
                awayTeamScore=score(aws), status=status, league=league,
                matchId=str(match_id), source=source, update_date=now)


class Game:
    def __init__(self, fetch=request, now=None):
        self.fetch = fetch
        self.now = now or datetime.datetime.now(datetime.timezone.utc)

    def getNBALiveInfo(self):
        try:
            rows = self.parse_nba(self.fetch(NBA_URL))
        except Exception as exc:
            LOG.warning('NBA primary failed (%s); trying ESPN', type(exc).__name__)
            date = self.game_day().strftime('%Y%m%d')
            rows = self.parse_espn(self.fetch(ESPN_URL + '?dates=' + date))
        legacy = []
        for r in rows:
            legacy.append(dict(home_team=r['homeTeamName'], away_team=r['awayTeamName'],
                               home_team_abbreviation=r['homeTeamName'],
                               away_team_abbreviation=r['awayTeamName'],
                               home_score=int(r['homeTeamScore']) if r['homeTeamScore'] else 0,
                               away_score=int(r['awayTeamScore']) if r['awayTeamScore'] else 0,
                               game_status=r['status'], update_date=self.now))
        return legacy, rows

    def game_day(self):
        # Keep the previous evening's games until 06:00 Eastern, including late finals.
        eastern = self.now.astimezone(pytz.timezone('America/New_York'))
        return (eastern - datetime.timedelta(hours=6)).date()

    def validate_date(self, value):
        expected = self.game_day()
        actual = datetime.datetime.strptime(value[:10], '%Y-%m-%d').date()
        if actual != expected:
            raise ValueError('Stale NBA scoreboard date: ' + str(actual))

    def parse_nba(self, data):
        board = data['scoreboard']
        self.validate_date(board['gameDate'])
        if not isinstance(board['games'], list):
            raise ValueError('Missing NBA games array')
        rows = []
        for g in board['games']:
            h, a = g['homeTeam'], g['awayTeam']
            rows.append(row(team_code(h['teamTricode']), team_code(a['teamTricode']),
                            h['score'], a['score'], g['gameStatusText'], 'NBA',
                            g['gameId'], 'nba', self.now))
        return self.deduplicate(rows)

    def parse_espn(self, data):
        if not any(l.get('slug') == 'nba' for l in data['leagues']):
            raise ValueError('Unexpected ESPN league')
        if 'day' in data:
            self.validate_date(data['day']['date'])
        if not isinstance(data['events'], list):
            raise ValueError('Missing ESPN events array')
        rows = []
        for event in data['events']:
            start = datetime.datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
            self.validate_date(start.astimezone(pytz.timezone('America/New_York')).date().isoformat())
            competition = event['competitions'][0]
            teams = {t['homeAway']: t for t in competition['competitors']}
            h, a = teams['home'], teams['away']
            state = event['status']['type']
            status = state['shortDetail']
            if state['state'] == 'pre':
                dt = datetime.datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
                status = dt.astimezone(pytz.timezone('Asia/Tokyo')).strftime('%m/%d %H:%M 試合前')
            rows.append(row(team_code(h['team']['abbreviation']), team_code(a['team']['abbreviation']),
                            h.get('score', ''), a.get('score', ''), status, 'NBA',
                            event['id'], 'espn', self.now))
        return self.deduplicate(rows)

    @staticmethod
    def deduplicate(rows):
        unique = {}
        for r in rows:
            if r['matchId'] in unique and unique[r['matchId']] != r:
                raise ValueError('Conflicting duplicate match')
            unique[r['matchId']] = r
        return list(unique.values())

    def getNPBLiveInfo(self):
        return self.parse_npb(self.fetch(NPB_URL, as_json=False))

    def parse_npb(self, html):
        soup = BeautifulSoup(html, 'html.parser')
        nav = soup.select_one('#day_nav')
        section = nav.find_parent('section') if nav else None
        if section is None:
            raise ValueError('NPB daily section missing')
        today = self.now.astimezone(pytz.timezone('Asia/Tokyo')).date()
        previous = section.select_one('.bb-head01__naviItem--previous')
        expected = (today - datetime.timedelta(days=1)).isoformat()
        if previous is None or ('date=' + expected) not in previous.get('href', ''):
            raise ValueError('NPB daily page date mismatch')
        games = section.select('li.bb-score__item')
        if not games:
            empty = section.select_one('.bb-noData')
            if empty is None or empty.get_text(strip=True) != '試合はありません':
                raise ValueError('NPB markup changed; refusing to publish empty scores')
            return [], []
        legacy, rows = [], []
        seen = set()
        for g in games:
            def text(selector, required=True):
                el = g.select_one(selector)
                if el is None and required:
                    raise ValueError('NPB required element missing: ' + selector)
                return el.get_text(strip=True) if el else ''
            link = g.select_one('.bb-score__content')['href']
            if link in seen:
                continue
            seen.add(link)
            home, away = text('.bb-score__homeLogo'), text('.bb-score__awayLogo')
            status = text('.bb-score__link')
            hs, aws = text('.bb-score__score--left', False), text('.bb-score__score--right', False)
            rows.append(row(home, away, hs, aws, status, 'NPB', link, 'yahoo', self.now))
            legacy.append(dict(venue=text('.bb-score__venue'), home_team=home, away_team=away,
                               status=status, link=link, score=dict(left=hs, center=text('.bb-score__score--center', False), right=aws),
                               update_date=self.now))
        return legacy, rows
