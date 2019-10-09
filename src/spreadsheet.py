import os
import json

import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials


class DriveClient:

    def __init__(self, client_secret='client_secret.json'):
        json_secret = json.loads(os.getenv('CLIENT_SECRET', client_secret))
        scope = ['https://www.googleapis.com/auth/drive']
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(json_secret, scope)
        self.client = gspread.authorize(credentials)

    def get_sheet_as_dataframe(self, spreadsheet_name):
        sheet = self.client.open(spreadsheet_name).sheet1
        return pd.DataFrame(sheet.get_all_records())
