#!/usr/local/bin/python3.7

from NBAGame import NBAGame
from NPBGame import NPBGame

#NBAの情報取得
nba = NBAGame()
nba.getLiveInfo()
nba.saveLiveInfo()

#NPBの情報取得
# npb = NPBGame()
# npb.getLiveInfo()
# npb.saveLiveInfo()


