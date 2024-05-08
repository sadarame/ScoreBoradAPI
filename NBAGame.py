from Game import Game
from nba_api.live.nba.endpoints import scoreboard

class NBAGame(Game):

    collection_name = "TB_NBA_LIVE_SCORE"

    def __init__(self):
        super().__init__("NBA",self.collection_name)
        self.getLiveInfo()

    ##ライブ情報を返却
    # def getLiveInfo(self):
    #     # NBAの試合情報を取得
    #     games = scoreboard.ScoreBoard()
    #     print(games.get_dict())
    #     Game.scoreDict =  games.get_dict()
        
    #     for game in Game.scoreDict['games']:
    #         home_team = game['homeTeam']['teamCity']
    #         away_team = game['awayTeam']['teamCity']
    #         home_score = game['homeTeam']['score']
    #         away_score = game['awayTeam']['score']
    #         game_status = game['gameStatusText']
    #         print(f"{home_team} vs {away_team}: {home_score} - {away_score} ({game_status})")
        
    def getLiveInfo(self):
        # NBAの試合情報を取得
        games = scoreboard.ScoreBoard()
        Game.scoreDict = games.get_dict()

        # 各試合の情報を抜き出す
        for game in Game.scoreDict['scoreboard']['games']:
            home_team = game['homeTeam']['teamCity']
            home_team_abbreviation = game['homeTeam']['teamTricode']
            away_team = game['awayTeam']['teamCity']
            away_team_abbreviation = game['awayTeam']['teamTricode']
            home_score = game['homeTeam']['score']
            away_score = game['awayTeam']['score']
            game_status = game['gameStatusText']

            # 試合情報を辞書に格納し、リストに追加
            game_info = {
                'home_team': home_team,
                'home_team_abbreviation': home_team_abbreviation,
                'away_team': away_team,
                'away_team_abbreviation': away_team_abbreviation,
                'home_score': home_score,
                'away_score': away_score,
                'game_status': game_status
            }
            Game.game_list.append(game_info)
