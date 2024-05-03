from Game import Game
from nba_api.live.nba.endpoints import scoreboard

class NBAGame(Game):

    collection_name = "TB_NBA_LIVE_SCORE"

    def __init__(self):
        super().__init__("NBA",self.collection_name)
        self.getLiveInfo()

    ##ライブ情報を返却
    def getLiveInfo(self):
        # NBAの試合情報を取得
        games = scoreboard.ScoreBoard()
        print(games.get_dict())
        Game.scoreDict =  games.get_dict()
