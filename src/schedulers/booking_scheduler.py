import datetime
import logging
import os
import time

import pandas as pd
import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from bs4 import BeautifulSoup

from src.spreadsheet import DriveClient

logging.basicConfig(level=logging.DEBUG)
logging.getLogger('apscheduler').setLevel(logging.DEBUG)

# Site info
LOGIN_URL = os.getenv('LOGIN_URL')
BOOKING_URL = os.getenv('BOOKING_URL')
DAYS_OF_WEEK = dict(zip(range(7), ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']))
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
HOUR = int(os.getenv('HOUR', 7))
MINUTE = int(os.getenv('MINUTE', 0))
SECOND = int(os.getenv('SECOND', 2))
JITTER = int(os.getenv('JITTER', 0))


def create_booking_job(username, password, places, hour_from, hour_to, in_out):
    def book_court():
        # Login request
        session = requests.session()
        response = session.get('%sPortal.jsp' % LOGIN_URL)
        soup = BeautifulSoup(response.text, features='html5lib')
        token_input = soup.find('input', {'name': 'token'})
        token = token_input.attrs['value']
        login_data = {
            'page': 'mylutece',
            'action': 'dologin',
            'token': token,
            'auth_provider': 'mylutece-openam',
            'username': username,
            'password': password,
            'Submit': '',
        }
        session.post('%splugins/mylutece/DoMyLuteceLogin.jsp' % LOGIN_URL, login_data)

        # Find time spot
        booking_date = (datetime.datetime.now() + datetime.timedelta(days=6)).strftime('%d/%m/%Y')
        search_data = {
            'hourRange': f'{hour_from}-{hour_to}',
            'when': booking_date,
            'selWhereTennisName': places,
            'selCoating': ['96', '2095', '94', '1324', '2016', '92'],
            'selInOut': in_out,
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
            return logging.log(logging.WARNING, f'No court available on {booking_date} for {username}')

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
        session.post(BOOKING_URL, payment_data)
        return logging.log(logging.INFO, f'Court successfully booked for {username}')

    return book_court


def create_scheduler():
    scheduler = BlockingScheduler()
    booking_references = (
        DriveClient().get_sheet_as_dataframe('RainBot')
        .assign(
            match_day=lambda df: df.MatchDay.str.lower().replace(DAYS_FRENCH_TO_ENGLISH),
            in_out=lambda df: df.InOut.replace({'Couvert': 'V', 'Découvert': 'F', '': 'V,F'}).str.split(','),
            places=lambda df: df.Courts.str.split(',')
        )
        .assign(
            match_day=lambda df: pd.Categorical(df.match_day, categories=DAYS_OF_WEEK.values(), ordered=True),
            day_of_booking=lambda df: (df.match_day.cat.codes + 1 % 7).map(lambda x: DAYS_OF_WEEK[x])
        )
        [['Username', 'Password', 'places', 'HourFrom', 'HourTo', 'in_out', 'day_of_booking']]
    )
    for row in booking_references.iterrows():
        logging.log(logging.INFO, f'Creating booking job for {row.Username} playing on {row.match_day}')
        scheduler.add_job(
            create_booking_job(row.Username, row.Password, row.places, row.HourFrom, row.HourTo, row.in_out),
            'cron', day_of_week=row.day_of_booking, hour=HOUR, minute=MINUTE, second=SECOND, jitter=JITTER,
        )
    return scheduler
