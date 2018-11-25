import os
import datetime
import warnings

import requests
from bs4 import BeautifulSoup


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
    recherche_data = {
        'hourRange': '20-22',
        'when': booking_date,
        'selWhereTennisName': ['Bertrand Dauvin', 'Jules Ladoumègue', 'Docteurs Déjerine', 'Sept Arpents'],
        'selCoating': ['96', '2095', '94', '1324', '2016', '92'],
        'selInOut': ['V', 'F'],  # V = couvert, F = non couvert
    }
    response = session.post(
        'https://tennis.paris.fr/tennis/jsp/site/Portal.jsp?page=recherche&action=rechercher_creneau', recherche_data)
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
        'https://tennis.paris.fr/tennis/jsp/site/Portal.jsp?page=reservation&view=reservation_creneau',
        reservation_data,
    )
    player_data = {
        'player1': ['Lafont', 'Marc', ''],
        'counter': '',
        'submitControle': 'submit'
    }
    session.post(
        'https://tennis.paris.fr/tennis/jsp/site/Portal.jsp?page=reservation&action=validation_court',
        player_data,
    )

    # Page paiement
    response = session.get(
        'https://tennis.paris.fr/tennis/jsp/site/Portal.jsp?page=reservation&view=methode_paiement')
    soup = BeautifulSoup(response.text, features='html5lib')
    if soup.find('table', {'nbtickets': 10}):
        return warnings.warn('Insufficient credit to proceed with payment. Reservation on hold for 15 minutes.')

    payment_data = {
        'page': 'reservation',
        'action': 'selection_methode_paiement',
        'paymentMode': 'existingTicket',
        'nbTickets': '1',
    }
    session.post('https://tennis.paris.fr/tennis/jsp/site/Portal.jsp', payment_data)
    return 'Court successfully booked'


if __name__ == '__main__':
    book_tennis_court()
