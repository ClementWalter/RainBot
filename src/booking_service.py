import logging
import os

import requests
from bs4 import BeautifulSoup

from src.producers import p, topic_prefix

BOOKING_URL = os.getenv('BOOKING_URL')
LOGIN_URL = os.getenv('LOGIN_URL')
logger = logging.getLogger(__name__)


class BookingService:

    def __init__(self):
        self.session = requests.session()
        self._username = None
        self._is_booking = False
        self.reservation = {}

    def find_courts(self, places, match_day, in_out, hour_from, hour_to):
        """
        Args:
            places (list): places where to look spot in
            match_day (str): dd/mm/YYYY
            in_out (list): containing V, F both or None
            hour_from (str): beginning of the spot
            hour_to (str): end of the spot
        """
        search_data = {
            'where': places,
            'selWhereTennisName': places,
            'when': match_day,
            'selCoating': ['96', '2095', '94', '1324', '2016', '92'],
            'selInOut': in_out,
            'hourRange': f'{hour_from}-{hour_to}',
        }
        request_object = self.session if self._is_booking else requests
        return request_object.post(
            BOOKING_URL,
            search_data,
            params={'page': 'recherche', 'action': 'rechercher_creneau'}
        )

    @staticmethod
    def soup(response):
        return BeautifulSoup(response.text, features='html5lib')

    def parse_courts(self, response):
        soup = self.soup(response)
        if not self._is_booking:
            return [court.text[:2] for court in soup.findAll('h4', {'class': 'panel-title'})]

        if soup.find('button', {'class': 'buttonHasReservation'}):
            message = f'User {self._username} has already an active reservation'
            logger.log(logging.WARNING, message)
            p.produce(f'{topic_prefix}default', message)
            return

        courts = soup.findAll('button', {'class': 'buttonAllOk'})
        if not courts:
            message = f'No court available for {self._username}'
            logger.log(logging.WARNING, message)
            p.produce(f'{topic_prefix}default', message)
            return

        courts.sort(key=lambda court: court.attrs['datedeb'])
        return courts

    def login(self, username, password):
        response = self.session.get(LOGIN_URL)
        soup = BeautifulSoup(response.text, features='html5lib')
        token_input = soup.find(id='form-login')
        route = token_input.attrs['action']
        login_data = {
            'username': username,
            'password': password,
            'Submit': '',
        }
        self._username = username
        return self.session.post(route, login_data)

    def pay(self):
        if not self._is_booking:
            return

        response = self.session.get(
            BOOKING_URL,
            params={'page': 'reservation', 'view': 'methode_paiement'}
        )
        if self.soup(response).find('table', {'nbtickets': 10}):
            message = (
                f'Insufficient credit to proceed with payment for {self._username}. Reservation on hold for 15 '
                f'minutes.'
            )
            p.produce(f'{topic_prefix}default', message)
            logger.log(logging.WARNING, message)
            return

        payment_data = {
            'page': 'reservation',
            'action': 'selection_methode_paiement',
            'paymentMode': 'existingTicket',
            'nbTickets': '1',
        }
        response = self.session.post(BOOKING_URL, payment_data)
        if response.status_code == 200:
            message = f'Court successfully paid for {self._username}'
            p.produce(f'{topic_prefix}default', message)
            logger.log(logging.INFO, message)
        message = f'Cannot pay court for {self._username}'
        p.produce(f'{topic_prefix}default', message)
        logger.log(logging.ERROR, message)
        return response

    def book_court(self, *args, **kwargs):
        self._is_booking = True
        response = self.find_courts(*args, **kwargs)
        courts = self.parse_courts(response)
        if not courts:
            self._is_booking = False
            return

        self.reservation = {
            'equipmentId': courts[0].attrs['equipmentid'],
            'courtId': courts[0].attrs['courtid'],
            'dateDeb': courts[0].attrs['datedeb'],
            'dateFin': courts[0].attrs['datefin'],
            'annulation': False
        }
        return self.session.post(
            BOOKING_URL,
            self.reservation,
            params={'page': 'reservation', 'view': 'reservation_creneau'}
        )

    def post_player(self, first_name='Roger', last_name='Federer'):
        if not self._is_booking:
            return

        player_data = {
            'player1': [first_name, last_name, ''],
            'counter': '',
            'submitControle': 'submit'
        }
        return self.session.post(
            BOOKING_URL,
            player_data,
            params={'page': 'reservation', 'action': 'validation_court'}
        )

    def logout(self):
        self._username = None
        self.session = requests.session()
        self._is_booking = False
        self.reservation = {}
