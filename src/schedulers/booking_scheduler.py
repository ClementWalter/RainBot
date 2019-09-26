import logging
import os
import time
from inflection import underscore

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from bs4 import BeautifulSoup

from src.spreadsheet import DriveClient
from src.utils import date_of_next_day

logging.basicConfig(level=logging.DEBUG)
logging.getLogger('apscheduler').setLevel(logging.DEBUG)

# Site info
LOGIN_URL = os.getenv('LOGIN_URL')
BOOKING_URL = os.getenv('BOOKING_URL')
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

# Cron info
HOUR = int(os.getenv('HOUR', 0))
MINUTE = int(os.getenv('MINUTE', 0))
SECOND = int(os.getenv('SECOND', 0))
JITTER = int(os.getenv('JITTER', 0))


def create_booking_job(username, password, places, match_day, hour_from, hour_to, in_out, **_):
    def book_court():
        # Login request
        session = requests.session()
        response = session.get(LOGIN_URL)
        soup = BeautifulSoup(response.text, features='html5lib')
        token_input = soup.find(id='form-login')
        route = token_input.attrs['action']
        login_data = {
            'username': username,
            'password': password,
            'Submit': '',
        }
        session.post(route, login_data)

        # Find time spot
        session.get(BOOKING_URL, params={'page': 'recherche', 'action': 'rechercher_creneau'})
        search_data = {
            'where': places,
            'selWhereTennisName': places,
            'when': match_day,
            'selCoating': ['96', '2095', '94', '1324', '2016', '92'],
            'selInOut': in_out,
            'hourRange': f'{hour_from}-{hour_to}',
        }
        response = session.post(
            BOOKING_URL,
            search_data,
            params={'page': 'recherche', 'action': 'rechercher_creneau'}
        )
        soup = BeautifulSoup(response.text, features='html5lib')

        user_not_logged_in = soup.find('a', {'onclick': 'displayCreateAccountPage();'})
        if user_not_logged_in:
            return logging.log(logging.ERROR, f'{username} failed to log in')

        player_has_reservation = soup.find('button', {'class': 'buttonHasReservation'})
        if player_has_reservation:
            return logging.log(logging.WARNING, f'User {username} has already an active reservation')

        courts = soup.findAll('button', {'class': 'buttonAllOk'})
        if not courts:
            return logging.log(logging.WARNING, f'No court available on {match_day} for {username}')

        courts.sort(key=lambda court: court.attrs['datedeb'])
        reservation_data = {
            'equipmentId': courts[0].attrs['equipmentid'],
            'courtId': courts[0].attrs['courtid'],
            'dateDeb': courts[0].attrs['datedeb'],
            'dateFin': courts[0].attrs['datefin'],
            'annulation': False
        }
        response = session.post(
            BOOKING_URL,
            reservation_data,
            params={'page': 'reservation', 'view': 'reservation_creneau'}
        )
        if response.status_code >= 400:
            return logging.log(logging.ERROR, f'Error received from server')
        player_data = {
            'player1': ['Roger', 'Federer', ''],
            'counter': '',
            'submitControle': 'submit'
        }
        response = session.post(
            BOOKING_URL,
            player_data,
            params={'page': 'reservation', 'action': 'validation_court'}
        )
        attempts = 1
        while response.status_code >= 400 and attempts < 5:
            attempts += 1
            time.sleep(5)
            response = session.post(
                BOOKING_URL,
                player_data,
                params={'page': 'reservation', 'action': 'validation_court'}
            )
        if response.status_code >= 400:
            return logging.log(logging.ERROR, f'Cannot validate reservation after {attempts} trials')

        # Payment page
        response = session.get(
            BOOKING_URL,
            params={'page': 'reservation', 'view': 'methode_paiement'}
        )
        if response.status_code >= 400:
            return logging.log(logging.ERROR, f'Error received from server')

        soup = BeautifulSoup(response.text, features='html5lib')
        if soup.find('table', {'nbtickets': 10}):
            return logging.log(
                logging.WARNING,
                f'Insufficient credit to proceed with payment for {username}. Reservation on hold for 15 minutes.'
            )

        payment_data = {
            'page': 'reservation',
            'action': 'selection_methode_paiement',
            'paymentMode': 'existingTicket',
            'nbTickets': '1',
        }
        response = session.post(BOOKING_URL, payment_data)
        if response.status_code == 200:
            return logging.log(logging.INFO, f'Court successfully booked for {username}')
        return logging.log(logging.ERROR, f'Cannot book court for {username}')

    return book_court


def create_scheduler():
    scheduler = BlockingScheduler()

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
        logging.log(logging.INFO, f'Creating booking job for {row.username} playing on {row.match_day}')
        scheduler.add_job(
            create_booking_job(**row), 'interval', hours=HOUR, minutes=MINUTE, seconds=SECOND, jitter=JITTER
        )
    return scheduler
