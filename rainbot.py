# coding=UTF-8
import logging
import os
from datetime import datetime

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler

from src.schedulers.cron_jobs import booking_job, cancel_job, send_remainder

logging.basicConfig(level=logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.ERROR)

# Cron info
HOUR = int(os.getenv("HOUR", 0))
MINUTE = int(os.getenv("MINUTE", 0))
SECOND = int(os.getenv("SECOND", 10))
JITTER = int(os.getenv("JITTER", 0))


if __name__ == "__main__":
    offset = pytz.timezone("Europe/Paris").utcoffset(datetime.now()).total_seconds()
    scheduler = BlockingScheduler()
    scheduler.add_job(
        booking_job, "interval", hours=HOUR, minutes=MINUTE, seconds=SECOND, jitter=JITTER
    )
    scheduler.add_job(
        cancel_job, "interval", hours=HOUR, minutes=MINUTE, seconds=SECOND, jitter=JITTER
    )
    for second in range(0, 10, 2):
        scheduler.add_job(
            booking_job, "cron", hour=int(8 - offset // 3600), second=second, jitter=JITTER
        )
    scheduler.add_job(send_remainder, "cron", hour=int(2 - offset // 3600))
    scheduler.start()
