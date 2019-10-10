import logging
from inflection import underscore

from src.booking_service import BookingService
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


def booking_job():
    booking_service = BookingService()
    booking_references = (
        DriveClient().get_sheet_as_dataframe('RainBot')
        .rename(columns=underscore)
        .rename(columns={'courts': 'places'})
        .assign(
            match_day=lambda df: (
                df.match_day.str.lower()
                .replace(DAYS_FRENCH_TO_ENGLISH)
                .replace(DAYS_OF_WEEK)
                .map(date_of_next_day)
            ),
            in_out=lambda df: df.in_out.replace({'Couvert': 'V', 'Découvert': 'F', '': 'V,F'}).str.split(','),
            places=lambda df: df.places.str.split(','),
        )
    )
    for _, row in booking_references.iterrows():
        response = booking_service.find_courts(**row.drop(['username', 'password']))
        courts = booking_service.parse_courts(response)
        if not courts:
            logger.log(logging.WARNING, f'No court available for {row.username} playing on {row.match_day}')
        else:
            booking_service.login(row.username, row.password)
            booking_service.book_court(**row.drop(['username', 'password']))
            booking_service.post_player()
            booking_service.pay()
            booking_service.logout()
