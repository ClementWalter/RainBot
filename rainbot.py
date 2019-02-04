# coding=UTF-8
import datetime
import logging
import os
import time

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.DEBUG)
logging.getLogger('apscheduler').setLevel(logging.DEBUG)

# Site info
LOGIN_URL = os.getenv('LOGIN_URL')
BOOKING_URL = os.getenv('BOOKING_URL')
# Cron info
DAYS_OF_BOOKING = os.getenv('DAYS_OF_BOOKING').split(',')
HOUR = int(os.getenv('HOUR'))
MINUTE = int(os.getenv('MINUTE'))
SECOND = int(os.getenv('SECOND'))
JITTER = int(os.getenv('JITTER'))
# Users info
USERNAMES = os.getenv('USERNAMES', '').split(',')
PASSWORDS = os.getenv('PASSWORDS', '').split(',')
HOURS_FROM = os.getenv('HOURS_FROM').split(',')
HOURS_TO = os.getenv('HOURS_TO').split(',')
PLACES_LIST = [places.split(',') for places in os.getenv('PLACES_LIST').split(';')]
IN_OUT = os.getenv('IN_OUT', 'V').split(',')  # V = couvert, F = non couvert


def create_booking_job(username, password, places, hour_from, hour_to):
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
            'selInOut': IN_OUT,
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
    for username, password, day_of_booking, places, hour_from, hour_to in zip(USERNAMES, PASSWORDS, DAYS_OF_BOOKING, PLACES_LIST, HOURS_FROM, HOURS_TO):
        logging.log(logging.INFO, f'Creating booking job for {username} on {day_of_booking}')
        scheduler.add_job(
            create_booking_job(username, password, places, hour_from, hour_to),
            'cron', day_of_week=day_of_booking, hour=HOUR, minute=MINUTE, second=SECOND, jitter=JITTER,
        )
    return scheduler


if __name__ == '__main__':
    create_scheduler().start()
