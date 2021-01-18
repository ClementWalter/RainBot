import logging

import pandas as pd
from inflection import underscore

from src.booking_service import BookingService
from src.producers import p, topic_prefix
from src.spreadsheet import DriveClient
from src.utils import date_of_next_day
from src.emails import EmailService

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
email_service = EmailService()
drive_client = DriveClient()


def booking_job():
    booking_references = (
        drive_client.get_sheet_as_dataframe('Requests')
        .rename(columns=underscore)
        .replace({'in_out': {'Couvert': 'V', 'Découvert': 'F', '': 'V,F'}})
        .assign(
            password=lambda df: df[['username', 'password']].groupby('username').transform('max'),
            places=lambda df: df.filter(regex=r'court_\d').agg(lambda r: r[r != ''].to_list(), axis=1),
            in_out=lambda df: df.in_out.str.split(','),
        )
        .replace({'': pd.np.NaN})
        .dropna(subset=['match_day', 'places'])
        .filter(regex=r'^(?!(court_\d)$)')
        .assign(
            match_day=lambda df: (
                df.match_day.str.lower().str.strip()
                .replace(DAYS_FRENCH_TO_ENGLISH)
                .replace(DAYS_OF_WEEK)
                .map(date_of_next_day)
            ),
        )
    )
    for _, row in booking_references.loc[lambda df: df.active == 'TRUE'].drop('active', axis=1).iterrows():
        response = booking_service.find_courts(**row.drop(['username', 'password']))
        courts = booking_service.parse_courts(response)
        if not courts:
            message = f'No court available for {row.username} playing on {row.match_day}'
            p.produce(f'{topic_prefix}default', message)
            logger.log(logging.INFO, message)
        else:
            booking_service.login(row.username, row.password)
            booking_service.book_court(**row.drop(['username', 'password']))
            booking_service.post_player()
            response = booking_service.pay()
            if response is not None:
                drive_client.append_series_to_sheet(
                    sheet_title='Historique',
                    data=row.append(pd.Series(booking_service.reservation)).rename(underscore),
                )
                email_service.send_mail({
                    "email": row.username,
                    "subject": "Nouvelle réservation Rainbot !",
                    "message": response.text
                })
                update_job()
            booking_service.logout()


def update_job():
    """
    A job for updating the Current tab
    """
    update_requests = (
        drive_client.get_sheet_as_dataframe('Update')
        .loc[lambda df: df.request_update == 'TRUE']
    )
    if update_requests.empty:
        return
    users = (
        drive_client.get_sheet_as_dataframe('Requests')
        .rename(columns=underscore)
        .assign(password=lambda df: df[['username', 'password']].groupby('username').transform('max'))
        .drop_duplicates(['username'])
        [['username', 'password']]
    )
    reservations = booking_service.get_reservations(users)
    drive_client.clear_sheet(sheet_title='Current')
    for _, reservation in reservations.iterrows():
        drive_client.append_series_to_sheet(
            sheet_title='Current',
            data=reservation,
        )
    drive_client.clear_sheet(sheet_title='Update')
    for _, update_request in update_requests.assign(request_update='FALSE').iterrows():
        drive_client.append_series_to_sheet(sheet_title='Update', data=update_request)
    logger.log(logging.INFO, 'Current tab updated')
