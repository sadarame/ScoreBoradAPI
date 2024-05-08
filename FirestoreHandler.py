import os
from firebase_admin import credentials, firestore, initialize_app
from datetime import datetime
import pytz 

class FirestoreHandler:
    _app_initialized = False
    
    def __init__(self,collectionName):
        self.collectionName = collectionName
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.json_path = os.path.join(script_dir, 'scoreboard-651cf-firebase-adminsdk-obk22-368b90d493.json')

        if not FirestoreHandler._app_initialized:
            cred = credentials.Certificate(self.json_path)
            initialize_app(cred)
            FirestoreHandler._app_initialized = True
    
        self.db = firestore.client()

        # メッセージ登録
    def setLiveScoer(self, score_list):
        # 追加するデータ
        # データをFirestoreに追加
        # self.db.collection(self.collectionName).document().set(score)
        for score in score_list:
            self.db.collection(self.collectionName).add(score)
       