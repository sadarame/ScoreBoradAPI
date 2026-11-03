"""J1 + five European leagues. ESPN's public endpoint is not a guaranteed API."""
import datetime
from Game import request, row
from SportsData import SOCCER_LEAGUES, JST, instant, espn_metadata


class Soccer:
    def __init__(self, fetch=request, now=None):
        self.fetch = fetch
        self.now = now or datetime.datetime.now(datetime.timezone.utc)

    def collect(self, league):
        slug = SOCCER_LEAGUES[league][0]
        today = self.now.astimezone(JST).date()
        # ESPN date boundaries are not JST. Fetch both dates, then filter by JST.
        rows = {}
        for day in (today - datetime.timedelta(days=1), today):
            date = day.strftime('%Y%m%d')
            data = self.fetch('https://site.api.espn.com/apis/site/v2/sports/soccer/{}/scoreboard?dates={}&limit=100'.format(slug, date))
            response_day = data.get('day', {}).get('date')
            if response_day and response_day[:10] != day.isoformat():
                raise ValueError('Soccer response day mismatch')
            _, found = self.parse(data, league, today)
            for item in found:
                rows[item['matchId']] = item
        return [], list(rows.values())

    def parse(self, data, league, today):
        slug = SOCCER_LEAGUES[league][0]
        if not any(l.get('slug') == slug for l in data['leagues']) or not isinstance(data['events'], list):
            raise ValueError('Unexpected soccer response')
        rows = []
        for event in data['events']:
            start = instant(event['date'])
            if start.astimezone(JST).date() != today:
                continue
            competitors = {c['homeAway']: c for c in event['competitions'][0]['competitors']}
            h, a = competitors['home'], competitors['away']
            meta = espn_metadata(event, h, a, basketball=False)
            r = row('{}:{}'.format(league, h['team']['id']), '{}:{}'.format(league, a['team']['id']),
                    h.get('score', ''), a.get('score', ''), meta['status'], league,
                    event['id'], 'espn', self.now)
            r.update(meta)
            if meta['phase'] == 'scheduled':
                r['homeTeamScore'] = r['awayTeamScore'] = ''
            rows.append(r)
        return [], rows

    def teams(self, league):
        slug = SOCCER_LEAGUES[league][0]
        data = self.fetch('https://site.api.espn.com/apis/site/v2/sports/soccer/{}/teams?limit=100'.format(slug))
        leagues = data['sports'][0]['leagues']
        target = next(l for l in leagues if l['slug'] == slug)
        teams = []
        for entry in target['teams']:
            t = entry['team']
            teams.append(dict(id='espn_{}_{}'.format(league, t['id']), league=league,
                              leagueName=SOCCER_LEAGUES[league][1], category='サッカー', sport='soccer',
                              searchNM='{}:{}'.format(league, t['id']), teamLongNM=t['displayName'],
                              teamShortNM=t.get('shortDisplayName', t['displayName']), provider='espn'))
        if not teams:
            raise ValueError('Empty team catalog')
        return teams
