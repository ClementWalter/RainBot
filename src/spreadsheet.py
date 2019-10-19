import json
import os

import gspread
import pandas as pd
from inflection import underscore
from oauth2client.service_account import ServiceAccountCredentials


class DriveClient:

    def __init__(self, client_secret='client_secret.json'):
        json_secret = json.loads(os.getenv('CLIENT_SECRET', client_secret))
        scope = ['https://www.googleapis.com/auth/drive']
        credentials = ServiceAccountCredentials.from_json_keyfile_dict(json_secret, scope)
        self.client = gspread.authorize(credentials)
        self.spreadsheet = self.client.open('RainBot')
        self.worksheets = self.spreadsheet.worksheets()
        self.headers = [
            list(map(underscore, sheet.get_all_values()[0])) for sheet in self.worksheets
        ]

    def get_sheet_as_dataframe(self, sheet_index):
        return pd.DataFrame(self.worksheets[sheet_index].get_all_records())

    def append_series_to_sheet(self, sheet_index, data):
        self.worksheets[sheet_index].append_row(
            data.reindex(self.headers[sheet_index]).fillna('').to_list()
        )
