# type: ignore
import logging
import os
import re
import time
from collections import ChainMap
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from twocaptcha import TwoCaptcha

load_dotenv()
BOOKING_URL = os.getenv("BOOKING_URL")
LOGIN_URL = os.getenv("LOGIN_URL")
AUTH_BASE_URL = os.getenv("AUTH_BASE_URL")
ACCOUNT_BASE_URL = os.getenv("ACCOUNT_BASE_URL")
CAPTCHA_URL = os.getenv("CAPTCHA_URL")
CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY")

logger = logging.getLogger(__name__)


class BookingService:
    def __init__(self):
        self.session = requests.session()
        self._username = None
        self._is_booking = False
        self.reservation = {}
        self._query_data = {}

    def find_courts(self, places, match_day, in_out, hour_from, hour_to, *_, **__):
        """
        Args:
            places (list): places where to look spot in
            match_day (str): dd/mm/YYYY
            in_out (list): containing V, F both or None
            hour_from (str): beginning of the spot
            hour_to (str): end of the spot
        """
        search_data = {
            "where": places,
            "selWhereTennisName": places,
            "when": match_day,
            "selCoating": ["96", "2095", "94", "1324", "2016", "92"],
            "selInOut": in_out,
            "hourRange": f"{int(hour_from)}-{int(hour_to)}",
        }
        request_object = self.session if self._is_booking else requests
        response = request_object.post(
            BOOKING_URL,
            search_data,
            params={"page": "recherche", "action": "rechercher_creneau"},
        )
        return response

    @staticmethod
    def soup(response):
        return BeautifulSoup(response.text, features="html5lib")

    def parse_courts(self, response):
        soup = self.soup(response)
        if not self._is_booking:
            return [court.text[:2] for court in soup.findAll("h4", {"class": "panel-title"})]

        if soup.find("button", {"class": "buttonHasReservation"}):
            message = f"User {self._username} has already an active reservation"
            logger.log(logging.WARNING, message)
            return

        courts = soup.findAll("button", {"class": "buttonAllOk"})
        if not courts:
            message = f"No court available for {self._username}"
            logger.log(logging.INFO, message)
            return

        courts.sort(key=lambda court: court.attrs["datedeb"])
        return courts

    def request(self, method, *args, **kwargs):
        response = self.session.__getattribute__(method)(*args, **kwargs)
        self._query_data = {
            **self._query_data,
            **parse_qs(urlparse(response.url).query),
            **ChainMap(*[parse_qs(urlparse(r.url).query) for r in response.history]),
        }
        return response

    def login(self, username, password):
        self.logout()
        response = self.request("get", LOGIN_URL)
        referer = response.url
        soup = self.soup(response)
        form = soup.find(id="form-login")
        if form is None:
            raise ValueError("Could not find route in form-login")

        route = form.attrs["action"]
        self._query_data.update(parse_qs(urlparse(route).query))
        login_data = {
            "username": username,
            "password": password,
            "Submit": "",
        }

        self.request("post", route, login_data, headers={"referer": referer})
        self.request("get", BOOKING_URL, params={"page": "recherche", "view": "recherche_creneau"})
        self.request(
            "get",
            urljoin(referer, urlparse(referer).path),
            params={
                "response_type": "code",
                "scope": "openid",
                "client_id": "moncompte_bandeau",
                "nonce": self._query_data["nonce"][0],
                "prompt": "none",
                "redirect_uri": f"{AUTH_BASE_URL}/banner/AccessCode.jsp",
            },
        )
        self.request(
            "post",
            f"{AUTH_BASE_URL}/auth/realms/paris/protocol/openid-connect/token",
            params={
                "code": self._query_data["code"][0],
                "grant_type": "authorization_code",
                "client_id": "moncompte_bandeau",
                "redirect_uri": self._query_data["redirect_uri"][0],
            },
        )

        self.request("get", f"{AUTH_BASE_URL}/auth/realms/paris/protocol/openid-connect/userinfo")

        self.request(
            "post",
            f"{ACCOUNT_BASE_URL}/moncompte/rest/banner/api/1/validateSession",
            params={"login": username},
        )

        self._username = username
        return response

    def pay(self):
        if not self._is_booking:
            return

        response = self.session.get(
            BOOKING_URL, params={"page": "reservation", "view": "methode_paiement"}
        )
        if self.soup(response).find("table", {"nbtickets": 10}):
            message = (
                f"Insufficient credit to proceed with payment for {self._username}. "
                "Reservation on hold for 15 "
                f"minutes."
            )
            logger.log(logging.WARNING, message)
            return response

        payment_data = {
            "page": "reservation",
            "action": "selection_methode_paiement",
            "paymentMode": "existingTicket",
            "nbTickets": "1",
        }
        response = self.session.post(BOOKING_URL, payment_data)
        if response.status_code != 200:
            time.sleep(2)
            response = self.session.post(BOOKING_URL, payment_data)
        if response.status_code != 200:
            message = f"Cannot pay court for {self._username}"
            logger.log(logging.ERROR, message)
            return response

        message = f"Court successfully paid for {self._username}"
        logger.log(logging.INFO, message)
        return response

    def book_court(self, *args, **kwargs):
        self._is_booking = True
        response = self.find_courts(*args, **kwargs)
        courts = self.parse_courts(response)
        if not courts:
            self._is_booking = False
            return
        self.select_court(courts, places_order=kwargs["places_id"])
        if not self.reservation:
            self._is_booking = False
            return

        response = self.session.post(
            BOOKING_URL,
            self.reservation,
            params={"page": "reservation", "view": "reservation_captcha"},
        )
        response = self.session.get(
            BOOKING_URL,
            params={"page": "reservation", "view": "return_reservation_captcha"},
        )

        captcha_check_result = self.solve_captcha(response)

        response = self.request(
            "post",
            BOOKING_URL,
            {
                "li-antibot-token": captcha_check_result["antibotToken"],
                "li-antibot-token-code": "",
                "submitControle": "submit",
            },
            params={"page": "reservation", "action": "reservation_captcha"},
        )
        response = self.session.post(
            BOOKING_URL,
            self.reservation,
            params={"page": "reservation", "view": "reservation_creneau"},
        )
        return response

    def solve_captcha(self, response):
        antibot_params = re.search(r"LI_ANTIBOT\.loadAntibot\(\[(.*?)\]", response.text, re.DOTALL)
        if not antibot_params:
            logger.error("Failed to extract LI_ANTIBOT parameters")
            return None

        config_items = [item.strip().strip('"') for item in antibot_params.group(1).split(",")]
        x_li_sp_key = config_items[3]

        captcha_transaction_url = (
            "https://captcha.liveidentity.com/captcha/public/frontend/api/v3/captchas/transaction"
        )
        headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "dnt": "1",
            "origin": "https://tennis.paris.fr",
            "referer": "https://tennis.paris.fr/",
            "sec-ch-ua": '"Not;A=Brand";v="24", "Chromium";v="128"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "x-li-js-version": "v4",
            "x-li-sp-key": x_li_sp_key,
        }
        antibot_params = self.session.post(captcha_transaction_url, headers=headers).json()

        captcha_headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/x-www-form-urlencoded",
            "dnt": "1",
            "origin": "https://tennis.paris.fr",
            "referer": "https://tennis.paris.fr/",
            "sec-ch-ua": '"Not;A=Brand";v="24", "Chromium";v="128"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "x-li-antibot-id": antibot_params["antibotId"],
            "x-li-js-version": "v4",
            "x-li-request-id": antibot_params["requestId"],
            "x-li-sp-key": x_li_sp_key,
        }
        captcha_url = "https://captcha.liveidentity.com/captcha/public/frontend/api/v3/captchas"
        captcha_response = self.session.post(
            captcha_url, headers=captcha_headers, data={"type": "IMAGE", "locale": "FR"}
        )

        if captcha_response.status_code != 200:
            logger.error(f"Failed to get captcha. Status code: {captcha_response.status_code}")
            return None

        captcha_data = captcha_response.json()
        captcha_image_url = (
            f"https://captcha.liveidentity.com/captcha{captcha_data['questions'][0]}"
        )
        captcha_image_headers = {
            "accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "accept-language": "en-US,en;q=0.9",
            "dnt": "1",
            "referer": "https://tennis.paris.fr/",
            "sec-ch-ua": '"Not;A=Brand";v="24", "Chromium";v="128"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "image",
            "sec-fetch-mode": "no-cors",
            "sec-fetch-site": "cross-site",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        }
        captcha_image_response = self.session.get(captcha_image_url, headers=captcha_image_headers)

        if captcha_image_response.status_code != 200:
            logger.error(
                f"Failed to get captcha image. Status code: {captcha_image_response.status_code}"
            )
            return None

        # Save the captcha image to a temporary file
        image_file = NamedTemporaryFile(suffix=".png", delete=False)
        with open(image_file.name, "wb") as f:
            f.write(captcha_image_response.content)

        solver = TwoCaptcha(CAPTCHA_API_KEY)
        result = solver.normal(image_file.name)

        captcha_check_headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/x-www-form-urlencoded",
            "dnt": "1",
            "origin": "https://tennis.paris.fr",
            "referer": "https://tennis.paris.fr/",
            "sec-ch-ua": '"Not;A=Brand";v="24", "Chromium";v="128"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "x-li-js-version": "v4",
            "x-li-sp-key": x_li_sp_key,
        }
        captcha_check_url = (
            f"https://captcha.liveidentity.com/captcha{captcha_data['captchaValidationUrl']}"
        )
        captcha_check_response = self.session.post(
            captcha_check_url, headers=captcha_check_headers, data={"answer": result["code"]}
        )

        if captcha_check_response.status_code != 200:
            logger.error(
                f"Failed to check captcha. Status code: {captcha_check_response.status_code}"
            )
            return None

        return captcha_check_response.json()

    def select_court(self, courts, places_order):
        ordered_selection = (
            pd.DataFrame([court.attrs for court in courts])
            .filter(items=["equipmentid", "courtid", "datedeb", "datefin"])
            .assign(
                equipmentid=lambda df: pd.Categorical(
                    df.equipmentid.astype(int), categories=places_order
                )
            )
            .sort_values("equipmentid")
            .dropna()
            .to_dict("records")
        )
        if not ordered_selection:
            logger.log(
                logging.ERROR,
                f"Selected courts does not have correct attributes",
            )
            self._is_booking = False
            return
        court = ordered_selection[0]
        self.reservation = {
            "equipmentId": court["equipmentid"],
            "courtId": court["courtid"],
            "dateDeb": court["datedeb"],
            "dateFin": court["datefin"],
            "annulation": False,
        }

    def post_player(self, first_name="Roger", last_name="Federer"):
        if not self._is_booking:
            return

        logger.log(logging.INFO, f"Booking with {first_name} {last_name}")
        player_data = {
            "player1": [first_name, last_name, ""],
            "counter": "",
            "submitControle": "submit",
        }
        return self.session.post(
            BOOKING_URL, player_data, params={"page": "reservation", "action": "validation_court"}
        )

    def logout(self):
        self._username = None
        self.session = requests.session()
        self._is_booking = False
        self.reservation = {}
        self._query_data = {}

    def get_reservation(self):
        """
        Fetch data from profile page
        """
        response = self.session.get(
            BOOKING_URL, params={"page": "profil", "view": "ma_reservation"}
        )
        soup = self.soup(response)
        if not soup.find("span", {"class": "tennis-name"}):
            return {}
        tennis_date, tennis_hours = soup.find("span", {"class": "tennis-hours"}).text.split(" - ")
        hour_from, hour_to = re.findall(r"\d+", tennis_hours)
        return {
            "username": self._username,
            "date_deb": pd.to_datetime(tennis_date + f" {hour_from}:00"),
            "date_fin": pd.to_datetime(tennis_date + f" {hour_to}:00"),
            "tennis_name": soup.find("span", {"class": "tennis-name"}).text.replace("TENNIS ", ""),
            "court_name": soup.find("span", {"class": "tennis-court"}).text,
        }

    def get_reservations(self, users):
        """
        Fetch all reservations for users
        Args:
            users (pandas.DataFrame): with columns

        Returns:

        """
        reservations = []
        for _, user in users.iterrows():
            try:
                self.login(user.username, user.password)
                reservations += [self.get_reservation()]
                self.logout()
            except KeyError:
                logger.log(logging.WARNING, f"{user.username} cannot log in")
        return pd.DataFrame(reservations).dropna()

    def cancel(self):
        response = self.request(
            "post",
            BOOKING_URL,
            params={"page": "profil", "view": "ma_reservation"},
            data={"annulation": "true"},
        )
        return response
