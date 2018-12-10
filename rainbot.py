# coding=UTF-8
import datetime
import logging
import os

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.DEBUG)
logging.getLogger('apscheduler').setLevel(logging.DEBUG)

PARIS_TENNIS_URL = 'https://tennis.paris.fr/tennis/jsp/site/Portal.jsp'
# Cron info
DAYS_OF_BOOKING = os.getenv('DAYS_OF_BOOKING', 'tue').split(',')
HOUR = int(os.getenv('HOUR', 7))
MINUTE = int(os.getenv('MINUTE', 2))
JITTER = int(os.getenv('JITTER', 100))
# User info
USERNAMES = os.getenv('USERNAMES', '').split(',')
PASSWORDS = os.getenv('PASSWORDS', '').split(',')
HOUR_FROM = int(os.getenv('HOUR_FROM', 20))
HOUR_TO = int(os.getenv('HOUR_TO', 22))
TENNIS_LIST = os.getenv('TENNIS_LIST', 'Sept Arpents,Bertrand Dauvin,Jules Ladoumègue,Docteurs Déjerine').split(',')
IN_OUT = os.getenv('IN_OUT', 'V').split(',')  # V = couvert, F = non couvert


def create_booking_job(username, password):
    def book_tennis_court():
        # Login request
        session = requests.session()
        response = session.get('https://moncompte.paris.fr/moncompte/jsp/site/Portal.jsp')
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
        session.post('https://moncompte.paris.fr/moncompte/jsp/site/plugins/mylutece/DoMyLuteceLogin.jsp', login_data)

        # Find time spot
        booking_date = (datetime.datetime.now() + datetime.timedelta(days=6)).strftime('%d/%m/%Y')
        search_data = {
            'hourRange': f'{HOUR_FROM}-{HOUR_TO}',
            'when': booking_date,
            'selWhereTennisName': TENNIS_LIST,
            'selCoating': ['96', '2095', '94', '1324', '2016', '92'],
            'selInOut': IN_OUT,
        }
        response = session.post(
            PARIS_TENNIS_URL,
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
            return logging.log(logging.WARNING, f'No court available on {booking_date}')

        courts.sort(key=lambda court: court.attrs['datedeb'])
        reservation_data = {
            'equipmentId': courts[0].attrs['equipmentid'],
            'courtId': courts[0].attrs['courtid'],
            'dateDeb': courts[0].attrs['datedeb'],
            'dateFin': courts[0].attrs['datefin'],
            'annulation': False
        }
        session.post(
            PARIS_TENNIS_URL,
            reservation_data,
            params={'page': 'reservation', 'view': 'reservation_creneau'}
        )
        player_data = {
            'player1': ['Roger', 'Federer', ''],
            'counter': '',
            'submitControle': 'submit'
        }
        session.post(
            PARIS_TENNIS_URL,
            player_data,
            params={'page': 'reservation', 'action': 'validation_court'}
        )

        # Payment page
        response = session.get(
            PARIS_TENNIS_URL,
            params={'page': 'reservation', 'view': 'methode_paiement'}
        )
        soup = BeautifulSoup(response.text, features='html5lib')
        if soup.find('table', {'nbtickets': 10}):
            return logging.log(
                logging.WARNING,
                'Insufficient credit to proceed with payment. Reservation on hold for 15 minutes.'
            )

        payment_data = {
            'page': 'reservation',
            'action': 'selection_methode_paiement',
            'paymentMode': 'existingTicket',
            'nbTickets': '1',
        }
        session.post(PARIS_TENNIS_URL, payment_data)
        return logging.log(logging.INFO, 'Court successfully booked')

    return book_tennis_court


def create_scheduler():
    scheduler = BlockingScheduler()
    for username, password, day_of_booking in zip(USERNAMES, PASSWORDS, DAYS_OF_BOOKING):
        logging.log(logging.INFO, f'Creating booking job for {username} on {day_of_booking}')
        scheduler.add_job(
            create_booking_job(username, password),
            'cron', day_of_week=day_of_booking, hour=HOUR, minute=MINUTE, jitter=JITTER,
        )
    return scheduler


if __name__ == '__main__':
    create_scheduler().start()
