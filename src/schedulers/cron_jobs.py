# type: ignore
import json
import logging
import multiprocessing as mp
import os
from datetime import datetime
from itertools import chain

mp.set_start_method("fork")

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from inflection import camelize, underscore

from src.booking_service import BookingService
from src.emails import EmailService
from src.spreadsheet import DriveClient
from src.utils import date_of_next_day

load_dotenv()
DAYS_OF_WEEK = dict(zip(["mon", "tue", "wed", "thu", "fri", "sat", "sun"], range(7)))
DAYS_FRENCH_TO_ENGLISH = {
    "lundi": "mon",
    "mardi": "tue",
    "mercredi": "wed",
    "jeudi": "thu",
    "vendredi": "fri",
    "samedi": "sat",
    "dimanche": "sun",
}
logger = logging.getLogger(__name__)
email_service = EmailService()
drive_client = DriveClient()


def book(row):
    message = f"Booking for {row['username']} playing on {row['match_day']}"
    logger.log(logging.INFO, message)
    booking_service = BookingService()
    place, time = booking_service.find_courts_without_login(**row)
    subject = None
    if not place:
        message = f"No court available for {row['username']} playing on {row['match_day']}"
        logger.log(logging.INFO, message)
        return
    try:
        logger.log(logging.INFO, f"Found court for {row['username']}, booking it")
        response = booking_service.book_court(
            place=place, **{**row, "hour_from": f"{time:02d}", "hour_to": f"{time + 1:02d}"}
        )
        subject = "Nouvelle réservation Rainbot !"
        drive_client.append_series_to_sheet(
            sheet_title="Historique",
            data=(
                pd.Series(
                    {
                        **row,
                        "request_id": row["row_id"],
                        **booking_service.reservation,
                    }
                ).rename(underscore)
            ),
        )
    except Exception as e:
        info = pd.Series(row.copy()).astype(str).to_dict()
        del info["password"]
        logger.log(logging.ERROR, f"Raising error for\n{json.dumps(info, indent=4)}:\n {e}")
        subject = f"{subject} : {e}"
    finally:
        if subject is not None:
            email_service.send_mail(
                {
                    "email": row["username"],
                    "subject": subject,
                    "message": getattr(response, "text") if hasattr(response, "text") else "",
                }
            )
        booking_service.logout()


def booking_job():
    users = (
        drive_client.users.rename(columns=underscore)
        .assign(username=lambda df: df.username.str.lower())
        .loc[lambda df: df.password != ""]
        .loc[lambda df: df["payé/montant"] != ""][["username", "password"]]
    )
    places = (
        drive_client.get_sheet_as_dataframe("Tennis")
        .rename(columns={"nomSrtm": "name"})
        .set_index("name")
        .id
    )
    booking_references = (
        drive_client.get_sheet_as_dataframe("Requests")
        .rename(columns=underscore)
        .assign(username=lambda df: df.username.str.lower())
        .replace({"in_out": {"Couvert": "V", "Découvert": "F", "": "V,F"}})
        .merge(users, on=["username"], how="inner")
        .assign(
            places=lambda df: df.filter(regex=r"court_\d").agg(
                lambda r: r[r != ""].to_list(), axis=1
            ),
            places_id=lambda df: df.places.map(lambda _places: [places.get(_p) for _p in _places]),
            in_out=lambda df: df.in_out.str.split(","),
        )
        .replace({"": np.NaN})
        .dropna(subset=["match_day", "places"])
        .filter(regex=r"^(?!(court_\d)$)")
        .assign(
            match_day=lambda df: (
                df.match_day.str.lower()
                .str.strip()
                .replace(DAYS_FRENCH_TO_ENGLISH)
                .replace(DAYS_OF_WEEK)
                .map(date_of_next_day)
            ),
            partenaire_first_name=lambda df: df["partenaire/full name"]
            .str.split(" ", expand=True)[0]
            .fillna("Roger"),
            partenaire_last_name=lambda df: df["partenaire/full name"]
            .str.split(" ", expand=True)[1]
            .fillna("Federer"),
            match_date=lambda df: pd.to_datetime(df.match_day, dayfirst=True),
            active=lambda df: df.active.replace({"TRUE": True, "FALSE": False}).astype("bool"),
        )
        .loc[lambda df: df.active]
        .loc[lambda df: df.match_date > datetime.now()]
        .drop("active", axis=1)
        .loc[lambda df: df.places.map(len) > 0]
        .set_index("row_id")
        .sort_values("match_date", ascending=False)
    )
    with mp.Pool(processes=len(booking_references)) as pool:
        pool.map(book, booking_references.reset_index().to_dict("records"))


def send_remainder():
    courts = drive_client.get_sheet_as_dataframe("Courts").set_index("_airId")["_airNom"]
    tennis = drive_client.get_sheet_as_dataframe("Tennis").set_index("id")["nomSrtm"]
    ongoing_bookings = (
        drive_client.get_sheet_as_dataframe("Historique")
        .rename(columns=underscore)
        .loc[lambda df: df.date_deb != ""]
        .assign(
            date_deb=lambda df: pd.to_datetime(df.date_deb, utc=True),
            heure_deb=lambda df: df.date_deb.dt.hour,
            court=lambda df: df.court_id.replace(courts),
            equipment=lambda df: df.equipment_id.replace(tennis),
        )
        .dropna(subset=["date_deb"])
        .loc[lambda df: df.date_deb >= pd.Timestamp.today(tz="utc")]
        .loc[lambda df: df.date_deb < pd.Timestamp.today(tz="utc") + pd.Timedelta(days=1)]
    )
    message = """
    Aujourd'hui c'est jour de match !
    <br/>
    <br/>
    Ça commence à <b>{heure_deb} heures</b>.
    <br/>
    Ça se passe à {equipment}, {court}
    <br/>
    <br/>
    Penser à prendre sa raquette, de l'eau et des balles.
    """
    for _, row in ongoing_bookings.iterrows():
        email_service.send_mail(
            {
                "email": row.username,
                "subject": "Jour de match !",
                "message": message.format(**row.to_dict()),
            }
        )
        if row["partenaire/id"] != "":
            email_service.send_mail(
                {
                    "email": row["partenaire/id"],
                    "subject": "Jour de match !",
                    "message": message.format(**row.to_dict()),
                }
            )


def update_data():
    BOOKING_URL = os.environ["BOOKING_URL"]
    response = requests.get(
        BOOKING_URL,
        params={"page": "tennisParisien", "view": "les_tennis_parisiens"},
        timeout=10,
    )
    soup = BeautifulSoup(response.text, features="html5lib")
    script = soup.find("div", {"class": "map-container"}).text.replace("\n", "").replace("\t", "")
    start = script.find("var tennis = ")
    stop = script.find("var markers =")
    tennis = [
        t["properties"]
        for t in json.loads(script[start:stop].replace("var tennis = ", "").replace(";", ""))[
            "features"
        ]
    ]

    drive_client.set_sheet_from_dataframe(
        "Tennis",
        (
            pd.DataFrame([t["general"] for t in tennis])
            .rename(columns=lambda c: c[1:])
            .assign(equCom=lambda df: df.equCom.str.replace(r"\n|\r", "", regex=True))
            .drop_duplicates(subset=["nomSrtm"])
            .assign(gps=lambda df: df.gpsLat.astype(str) + "," + df.gpsLon.astype(str))
        ),
    )

    drive_client.set_sheet_from_dataframe(
        "Courts",
        (
            pd.DataFrame(
                list(
                    chain.from_iterable(
                        [
                            [
                                {
                                    **c,
                                    "id": t["general"]["_id"],
                                    "surface": c["_coating"]["_revLib"],
                                }
                                for c in t["courts"]
                            ]
                            for t in tennis
                        ]
                    )
                )
            )
            .rename(columns={"_airCvt": "couvert", "_airEcl": "eclaire", "_airOuvRes": "ouvert"})
            .filter(items=["_airId", "_airNom", "id", "surface", "eclaire", "ouvert", "couvert"])
            .drop_duplicates(subset=["_airId"])
        ),
    )
