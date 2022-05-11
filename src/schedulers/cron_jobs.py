import logging

import numpy as np
import pandas as pd
from inflection import underscore

from src.booking_service import BookingService
from src.emails import EmailService
from src.spreadsheet import DriveClient
from src.utils import date_of_next_day

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
booking_service = BookingService()
email_service = EmailService()
drive_client = DriveClient()


def booking_job():
    users = (
        drive_client.get_sheet_as_dataframe("Users")
        .rename(columns=underscore)
        .loc[lambda df: df.password != ""][["username", "password"]]
    )
    booking_references = (
        drive_client.get_sheet_as_dataframe("Requests")
        .rename(columns=underscore)
        .replace({"in_out": {"Couvert": "V", "Découvert": "F", "": "V,F"}})
        .drop("password", axis=1)
        .merge(users, on=["username"], how="inner")
        .assign(
            places=lambda df: df.filter(regex=r"court_\d").agg(
                lambda r: r[r != ""].to_list(), axis=1
            ),
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
        )
        .loc[lambda df: df.active == "TRUE"]
        .drop("active", axis=1)
        .loc[lambda df: df.places.map(len) > 0]
        .set_index("row_id")
    )
    for _, row in booking_references.iterrows():
        response = booking_service.find_courts(**row.drop(["username", "password"]))
        courts = booking_service.parse_courts(response)
        if not courts:
            message = f"No court available for {row.username} playing on {row.match_day}"
            logger.log(logging.INFO, message)
        else:
            try:
                booking_service.login(row.username, row.password)
                booking_service.book_court(**row)
                booking_service.post_player(
                    first_name=row.partenaire_first_name, last_name=row.partenaire_last_name
                )
                response = booking_service.pay()
                if response is not None:
                    if "Mode de paiement" in response.text:
                        subject = "Rainbot a besoin d'argent !"
                    else:
                        subject = "Nouvelle réservation Rainbot !"
                        drive_client.append_series_to_sheet(
                            sheet_title="Historique",
                            data=row.append(
                                pd.Series({"request_id": row.name, **booking_service.reservation})
                            ).rename(underscore),
                        )
                    email_service.send_mail(
                        {
                            "email": row.username,
                            "subject": subject,
                            "message": response.text,
                        }
                    )
                    update_tabs()
            except Exception as e:
                logger.log(logging.ERROR, f"Raising error {e} for\n{row}")
                email_service.send_mail(
                    {
                        "email": row.username,
                        "subject": "Erreur RainBot",
                        "message": response.text,
                    }
                )
            finally:
                booking_service.logout()


def update_tabs(username=None):
    """
    A job for updating the Current tab
    """
    update_requests = drive_client.get_sheet_as_dataframe("Update").loc[
        lambda df: df.request_update == "TRUE"
    ]
    if update_requests.empty:
        return
    users = (
        drive_client.get_sheet_as_dataframe("Users")
        .rename(columns=underscore)
        .loc[lambda df: df.username.isin([username] if username is not None else df.username)]
        .loc[lambda df: df.username.str.len() > 0]
        .loc[lambda df: df.password.str.len() > 0]
    )
    reservations = booking_service.get_reservations(users)
    drive_client.clear_sheet(sheet_title="Current")
    for _, reservation in reservations.iterrows():
        drive_client.append_series_to_sheet(
            sheet_title="Current",
            data=reservation,
        )
    drive_client.clear_sheet(sheet_title="Update")
    for _, update_request in update_requests.assign(request_update="FALSE").iterrows():
        drive_client.append_series_to_sheet(sheet_title="Update", data=update_request)
    logger.log(logging.INFO, "Current tab updated")


def cancel_job():
    users = (
        drive_client.get_sheet_as_dataframe("Users")
        .rename(columns=underscore)
        .loc[lambda df: df.annulation == "TRUE"]
    )
    for _, row in users.iterrows():
        try:
            booking_service.login(row.username, row.password)
            response = booking_service.cancel()
            if response is not None:
                subject = "Réservation annulée !"
                email_service.send_mail(
                    {
                        "email": row.username,
                        "subject": subject,
                        "message": response.text,
                    }
                )
                drive_client.worksheets["Users"].update_cell(
                    row.name + 2, drive_client.headers["Users"].index("annulation") + 1, "FALSE"
                )

        except Exception as e:
            logger.log(logging.ERROR, f"Cannot cancel for {row}")
            raise e
        finally:
            booking_service.logout()


def send_remainder():
    ongoing_bookings = (
        drive_client.get_sheet_as_dataframe("Historique")
        .rename(columns=underscore)
        .astype({"date_deb": "datetime64"})
        .loc[lambda df: df.date_deb >= pd.Timestamp.today()]
        .loc[lambda df: df.date_deb < pd.Timestamp.today() + pd.Timedelta(days=1)]
    )
    message = f"""
    Aujourd'hui c'est jour de match !

    Penser à prendre sa raquette, de l'eau et des balles.
    """
    for _, row in ongoing_bookings.iterrows():
        email_service.send_mail(
            {
                "email": row.username,
                "subject": "Jour de match !",
                "message": message,
            }
        )
        if row["partenaire/id"] != "":
            email_service.send_mail(
                {
                    "email": row["partenaire/id"],
                    "subject": "Jour de match !",
                    "message": message,
                }
            )
