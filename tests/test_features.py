import copy
import datetime
import unittest
from unittest.mock import patch
from Soccer import Soccer
from SportsData import JST, instant, stats, nba_metadata, espn_metadata
from Notifications import milestone, game_key, topic, message_payload, NotificationDispatcher

NOW = datetime.datetime(2026, 10, 26, 0, 0, tzinfo=datetime.timezone.utc)


def event(date='2026-10-25T15:00Z'):
    return dict(id='1', date=date, status=dict(period=1, displayClock="23'", type=dict(state='in', name='STATUS_IN_PROGRESS', completed=False)),
                competitions=[dict(competitors=[dict(homeAway=side, score='1', team=dict(id=id, abbreviation='ABC', displayName='Club '+id), statistics=[dict(name='possessionPct', displayValue='54.5')], records=[dict(type='total', summary='3-2-1')]) for side,id in [('home','10'),('away','20')]])])


def nba(phase='scheduled'):
    return dict(league='NBA', matchId='nba1', homeTeamName='LAL', awayTeamName='GSW', homeTeamScore='50', awayTeamScore='48', phase=phase, period=1, startsAt=NOW)


class SportsTests(unittest.TestCase):
    def test_jst_midnight_and_explicit_offset(self):
        self.assertEqual(instant('2026-10-25T15:00Z').astimezone(JST).day, 26)
        self.assertEqual(instant('2026-10-26T00:00+09:00'), instant('2026-10-25T15:00Z'))
        with self.assertRaises(ValueError): instant('2026-10-25T15:00')

    def test_soccer_filters_jst_date_and_uses_league_scoped_ids(self):
        data = dict(leagues=[dict(slug='jpn.1')], events=[event('2026-10-25T14:59Z'),event()])
        _,rows = Soccer(now=NOW).parse(data, 'J1', NOW.astimezone(JST).date())
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['homeTeamName'],'J1:10')
        self.assertEqual(rows[0]['homeStats'], {'ボール支配率':'54.5%'})
        self.assertEqual(rows[0]['homeRecord'], '3-2-1')

    def test_soccer_empty_vs_invalid_and_scheduled_stats(self):
        data = dict(leagues=[dict(slug='jpn.1')], events=[])
        self.assertEqual(Soccer().parse(data,'J1',NOW.date()),([],[]))
        with self.assertRaises(ValueError): Soccer().parse(data,'EPL',NOW.date())
        e=event();e['status']['type']['state']='pre'
        e['status']['type']['name']='STATUS_SCHEDULED'
        data['events']=[e]
        r=Soccer().parse(data,'J1',NOW.date())[1][0]
        self.assertEqual(r['homeTeamScore'],'')
        self.assertEqual(r['homeStats'],{})

    def test_collector_uses_individual_dates_not_rejected_date_ranges(self):
        urls=[]
        def fetch(url):
            urls.append(url)
            return dict(leagues=[dict(slug='jpn.1')],events=[event()])
        rows=Soccer(fetch=fetch,now=NOW).collect('J1')[1]
        self.assertEqual(len(rows),1)
        self.assertEqual(len(urls),2)
        self.assertIn('dates=20261025&', urls[0])
        self.assertIn('dates=20261026&', urls[1])

    def test_stats_missing_not_zero_and_percentage_units(self):
        self.assertEqual(stats({'fieldGoalsPercentage': .5, 'assists':0},nba=True), {'FG成功率':'50.0%','アシスト':'0'})
        self.assertEqual(stats([dict(name='assists',displayValue=None)]),{})
        self.assertEqual(stats([dict(name='fieldGoalPct',displayValue='50')]),{'FG成功率':'50%'})

    def test_nba_halftime_and_clock(self):
        g=dict(gameStatus=2,gameStatusText='Halftime',gameTimeUTC='2026-10-26T00:00:00Z',period=2,gameClock='PT00M00.00S',homeTeam=dict(teamTricode='LAL'),awayTeam=dict(teamTricode='GSW'))
        self.assertEqual(nba_metadata(g)['phase'],'halftime')
        g.update(gameStatusText='Q1',period=1,gameClock='PT09M32.00S')
        self.assertEqual(nba_metadata(g)['status'],'第1Q 09:32')

    def test_milestone_transitions_and_no_historical_replay(self):
        self.assertIsNone(milestone(None,nba('final'),NOW))
        previous=dict(phase='scheduled',observedAt=NOW)
        self.assertEqual(milestone(previous,nba('live'),NOW),'start')
        self.assertIsNone(milestone(previous,dict(nba('live'),period=3),NOW))
        self.assertEqual(milestone(dict(phase='live',observedAt=NOW),nba('halftime'),NOW),'halftime')
        self.assertEqual(milestone(dict(phase='live',observedAt=NOW),nba('final'),NOW),'final')
        self.assertIsNone(milestone(dict(phase='live',observedAt=NOW),nba('final'),NOW+datetime.timedelta(minutes=11)))
        self.assertIsNone(milestone(dict(phase='halftime',observedAt=NOW),nba('halftime'),NOW))

    def test_fallback_identity_and_both_team_subscribers(self):
        self.assertEqual(game_key(nba()),game_key(dict(nba(),matchId='espn2')))
        payload=message_payload(nba('final'),'final')
        self.assertIn("'score_NBA_LAL_final' in topics || 'score_NBA_GSW_final' in topics",payload['condition'])
        self.assertEqual(payload['body'], 'レイカーズ 50 − 48 ウォリアーズ')
        self.assertEqual(topic('LAL','start'),'score_NBA_LAL_start')


class Snapshot:
    def __init__(self,ref): self.reference,self.id,self.exists=ref,ref.id,ref.id in ref.db.docs
    def to_dict(self): return copy.deepcopy(self.reference.db.docs.get(self.id))
class Ref:
    def __init__(self,db,id): self.db,self.id=db,id
    def get(self,transaction=None): return Snapshot(self)
    def update(self,data): self.db.docs[self.id].update(data)
class Collection:
    def __init__(self,db,name): self.db,self.name=db,name
    def document(self,key): return Ref(self.db,self.name+'/'+key)
    def where(self,*args): return self
    def limit(self,n): return self
    def stream(self): return [Snapshot(Ref(self.db,k)) for k,v in list(self.db.docs.items()) if k.startswith(self.name+'/') and v.get('status')=='pending']
class DB:
    def __init__(self): self.docs={}
    def collection(self,name): return Collection(self,name)
    def transaction(self): return self
    def set(self,ref,data): self.docs[ref.id]=copy.deepcopy(data)
    def update(self,ref,data): ref.update(data)

class DeliveryTests(unittest.TestCase):
    @patch('Notifications.firestore.transactional',lambda fn:fn)
    def test_event_claim_is_persistent_and_no_duplicate_on_next_poll(self):
        db=DB();sent=[]
        n=NotificationDispatcher(db,send=lambda m:sent.append(m) or 'message1')
        n.observe([nba()],NOW)
        n.observe([nba('live')],NOW)
        n.observe([nba('live')],NOW)
        n.dispatch(NOW);n.dispatch(NOW)
        self.assertEqual(len(sent),1)
        self.assertTrue(any(v.get('status')=='sent' for v in db.docs.values()))

    @patch('Notifications.firestore.transactional',lambda fn:fn)
    def test_ambiguous_send_failure_is_not_retried_and_old_pending_expires(self):
        db=DB();calls=[]
        def fail(m): calls.append(m);raise TimeoutError()
        n=NotificationDispatcher(db,send=fail)
        n.observe([nba()],NOW);n.observe([nba('live')],NOW)
        n.dispatch(NOW);n.dispatch(NOW)
        self.assertEqual(len(calls),1)
        self.assertTrue(any(v.get('status')=='failed' for v in db.docs.values()))
        n.observe([nba('halftime')],NOW)
        n.dispatch(NOW+datetime.timedelta(minutes=6))
        self.assertEqual(len(calls),1)
        self.assertTrue(any(v.get('status')=='expired' for v in db.docs.values()))
