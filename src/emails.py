import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


class EmailService:
    def __init__(self):
        self.contact_address = os.environ["CONTACT"]
        self.contact_password = os.environ["PASSWORD"]

    def send_mail(self, data):
        message = MIMEMultipart('alternative')
        message["From"] = self.contact_address
        message["To"] = data["email"]
        message["Subject"] = data.get("subject")
        message.attach(MIMEText(data.get("message"), "plain"))
        message.attach(MIMEText(data.get("message"), "html"))
        session = smtplib.SMTP("mail.gandi.net", 587)
        session.starttls()
        session.login(self.contact_address, self.contact_password)
        text = message.as_string()
        session.sendmail(self.contact_address, data["email"], text)
        session.quit()
