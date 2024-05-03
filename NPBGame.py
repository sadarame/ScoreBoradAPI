from Game import Game
import requests
from bs4 import BeautifulSoup

class NPBGame(Game):

    collection_name = "TB_NPB_LIVE_SCORE"

    def __init__(self):
        super().__init__("NPB",self.collection_name)
        self.getLiveInfo()

    ##ライブ情報を返却
    def getLiveInfo(self):
        # NBAの試合情報を取得
        #ターゲットのURL
        url = "https://baseball.yahoo.co.jp/npb/schedule/"
        # ページの取得
        # ページの取得
        response = requests.get(url)

        # ページの解析
        soup = BeautifulSoup(response.text, 'html.parser')

        # 試合情報を格納するリスト
        game_list = []

        # 試合情報の取得
        games = soup.find_all('li', class_='bb-score__item')

        for game in games:
            venue = game.select_one('.bb-score__venue').text.strip()
            home_team = game.select_one('.bb-score__homeLogo').text.strip()
            away_team = game.select_one('.bb-score__awayLogo').text.strip()
            status = game.select_one('.bb-score__link').text.strip()
            link = game.select_one('.bb-score__content')['href']

            # スコアの取得
            score_left = game.select_one('.bb-score__score--left').text.strip()
            score_center = game.select_one('.bb-score__score--center').text.strip()
            score_right = game.select_one('.bb-score__score--right').text.strip()

            # すでに同じ試合情報がリストに存在するかどうかを確認
            existing_game_info = next((game_info for game_info in game_list if game_info['venue'] == venue and
                                                                game_info['home_team'] == home_team and
                                                                game_info['away_team'] == away_team), None)

            # 試合情報を辞書にまとめてリストに追加
            if existing_game_info is None:
                game_info = {
                    'venue': venue,
                    'home_team': home_team,
                    'away_team': away_team,
                    'status': status,
                    'link': link,
                    'score': {
                        'left': score_left,
                        'center': score_center,
                        'right': score_right
                    }
                }

            game_list.append(game_info)

        # 取得した試合情報の表示
        for game_info in game_list:
            print(game_info)

        Game.scoreDict =  game_info