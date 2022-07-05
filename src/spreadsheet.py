import json
import os

import gspread
import pandas as pd
from inflection import underscore
from oauth2client.service_account import ServiceAccountCredentials


class DriveClient:
    def __init__(self, client_secret="client_secret.json"):
        json_secret = json.loads(os.getenv("CLIENT_SECRET", client_secret))
        scope = ["https://www.googleapis.com/auth/drive"]
        self.credentials = ServiceAccountCredentials.from_json_keyfile_dict(json_secret, scope)
        self._client = None
        self._worksheets = []
        self._headers = {}
        self.login()

    def login(self):
        self._client = gspread.authorize(self.credentials)
        self._worksheets = self._client.open("RainBot").worksheets()
        self._headers = {
            worksheet.title: list(map(underscore, worksheet.get_all_values()[0]))
            for worksheet in self._worksheets
        }

    @property
    def worksheets(self):
        if self._client.auth.expired:
            self.login()
        return {worksheet.title: worksheet for worksheet in self._worksheets}

    @property
    def headers(self):
        if self._client.auth.expired:
            self.login()
        return self._headers

    def get_sheet_as_dataframe(self, sheet_title: str) -> pd.DataFrame:
        return pd.DataFrame(self.worksheets[sheet_title].get_all_records())

    def append_series_to_sheet(self, sheet_title, data):
        self.worksheets[sheet_title].append_row(
            data.reindex(self.headers[sheet_title]).fillna("").to_list(),
            insert_data_option="INSERT_ROWS",
            table_range="A1",
        )

    def clear_sheet(self, sheet_title):
        self.worksheets[sheet_title].clear()
        self.worksheets[sheet_title].append_row(self.headers[sheet_title])
