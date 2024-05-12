#!/usr/local/bin/python3.7

from Game1 import Game1 

# #NBAの情報取得
# nba = NBAGame()
# nba.getLiveInfo()
# nba.saveLiveInfo()

# #NPBの情報取得
# npb = NPBGame()
# npb.getLiveInfo()
# npb.saveLiveInfo()


game = Game1()
game.getNBALiveInfo()
game.getNPBLiveInfo()

