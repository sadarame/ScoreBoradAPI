#!/usr/local/bin/python3.7
from Game import Game
import datetime
import pytz
import datetime
import pytz

# #NBAの情報取得
# nba = NBAGame()
# nba.getLiveInfo()
# nba.saveLiveInfo()

# #NPBの情報取得
# npb = NPBGame()
# npb.getLiveInfo()
# npb.saveLiveInfo()

game = Game()
game.getNBALiveInfo()
game.getNPBLiveInfo()

