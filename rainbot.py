# coding=UTF-8
import datetime
import os
import warnings

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from bs4 import BeautifulSoup
from flask import Flask

PARIS_TENNIS_URL = 'https://tennis.paris.fr/tennis/jsp/site/Portal.jsp'
DAY_OF_WEEK = os.getenv('DAY_OF_WEEK', 'tue')
HOUR = os.getenv('HOUR', 8)
MINUTE = os.getenv('MINUTE', 2)

scheduler = BlockingScheduler()
app = Flask(__name__)


@app.route('/')
def hello_world():
    return 'Hello RainBot!'


@scheduler.scheduled_job('cron', day_of_week=DAY_OF_WEEK, hour=HOUR, minute=MINUTE, jitter=100)
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
        'username': os.getenv('USERNAME'),
        'password': os.getenv('PASSWORD'),
        'Submit': '',
    }
    session.post('https://moncompte.paris.fr/moncompte/jsp/site/plugins/mylutece/DoMyLuteceLogin.jsp', login_data)

    # Find time spot
    booking_date = (datetime.datetime.now() + datetime.timedelta(days=6)).strftime('%d/%m/%Y')
    search_data = {
        'hourRange': '20-22',
        'when': booking_date,
        'selWhereTennisName': ['Bertrand Dauvin', 'Jules Ladoumègue', 'Docteurs Déjerine', 'Sept Arpents'],
        'selCoating': ['96', '2095', '94', '1324', '2016', '92'],
        'selInOut': ['V', 'F'],  # V = couvert, F = non couvert
    }
    response = session.post(
        PARIS_TENNIS_URL,
        search_data,
        params={'page': 'recherche', 'action': 'rechercher_creneau'}
    )
    soup = BeautifulSoup(response.text, features='html5lib')
    courts = soup.findAll('button', {'class': 'buttonAllOk'})

    if not courts:
        raise ValueError(f'No court available on {booking_date}')

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

    # Page paiement
    response = session.get(
        PARIS_TENNIS_URL,
        params={'page': 'reservation', 'view': 'methode_paiement'}
    )
    soup = BeautifulSoup(response.text, features='html5lib')
    if soup.find('table', {'nbtickets': 10}):
        return warnings.warn('Insufficient credit to proceed with payment. Reservation on hold for 15 minutes.')

    payment_data = {
        'page': 'reservation',
        'action': 'selection_methode_paiement',
        'paymentMode': 'existingTicket',
        'nbTickets': '1',
    }
    session.post(PARIS_TENNIS_URL, payment_data)
    return 'Court successfully booked'


if __name__ == '__main__':
    PORT = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=PORT, debug=True)
    scheduler.start()
