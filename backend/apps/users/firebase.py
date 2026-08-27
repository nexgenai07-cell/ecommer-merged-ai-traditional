import json
import os
import firebase_admin
from firebase_admin import credentials

if not firebase_admin._apps:
    firebase_credentials = json.loads(
        os.environ["FIREBASE_CREDENTIALS_JSON"]
    )

    cred = credentials.Certificate(firebase_credentials)
    firebase_admin.initialize_app(cred)