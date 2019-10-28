import logging

import pandas as pd
from inflection import underscore

from src.booking_service import BookingService
from src.producers import p, topic_prefix
from src.spreadsheet import DriveClient
from src.utils import date_of_next_day

DAYS_OF_WEEK = dict(zip(['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'], range(7)))
DAYS_FRENCH_TO_ENGLISH = {
    'lundi': 'mon',
    'mardi': 'tue',
    'mercredi': 'wed',
    'jeudi': 'thu',
    'vendredi': 'fri',
    'samedi': 'sat',
    'dimanche': 'sun',
}
logger = logging.getLogger(__name__)
booking_service = BookingService()
drive_client = DriveClient()


def booking_job():
    booking_references = (
        drive_client.get_sheet_as_dataframe(0)
        .rename(columns=underscore)
        .replace({'in_out': {'Couvert': 'V', 'Découvert': 'F', '': 'V,F'}})
        .assign(
            password=lambda df: df[['username', 'password']].groupby('username').transform('max'),
            places=lambda df: df.filter(regex=r'court_\d').agg(lambda row: row[row != ''].to_list(), axis=1),
            in_out=lambda df: df.in_out.str.split(','),
        )
        .replace({'': pd.np.NaN})
        .dropna(subset=['match_day', 'places'])
        .loc[lambda df: df.active == 'TRUE']
        .filter(regex=r'^(?!(court_\d|active)$)')
        .assign(
            match_day=lambda df: (
                df.match_day.str.lower().str.strip()
                .replace(DAYS_FRENCH_TO_ENGLISH)
                .replace(DAYS_OF_WEEK)
                .map(date_of_next_day)
            ),
        )
    )
    for _, row in booking_references.iterrows():
        response = booking_service.find_courts(**row.drop(['username', 'password']))
        courts = booking_service.parse_courts(response)
        if not courts:
            message = f'No court available for {row.username} playing on {row.match_day}'
            p.produce(f'{topic_prefix}default', message)
            logger.log(logging.WARNING, message)
        else:
            booking_service.login(row.username, row.password)
            booking_service.book_court(**row.drop(['username', 'password']))
            if booking_service._is_booking:
                drive_client.append_series_to_sheet(
                    sheet_index=2,
                    data=row.append(pd.Series(booking_service.reservation)).rename(underscore),
                )
            booking_service.post_player()
            booking_service.pay()
            booking_service.logout()
