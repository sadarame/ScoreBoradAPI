import os
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from FirestoreHandler import FirestoreHandler

class Game:

    scoreDict = None

    def __init__(self, league,tableName):
        
        self.league = league
        #firestore用のインスタンス
        self.firestoreHandler = FirestoreHandler(tableName)

    def getLiveInfo(self):
        raise NotImplementedError("Subclasses must implement getLiveInfo method")

    def saveLiveInfo(self):
        self.firestoreHandler.setLiveScoer(self.scoreDict)