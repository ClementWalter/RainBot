# coding=UTF-8
import logging
import os

from apscheduler.schedulers.blocking import BlockingScheduler

from src.schedulers.cron_jobs import booking_job

logging.basicConfig(level=logging.WARNING)
logging.getLogger('apscheduler').setLevel(logging.INFO)

# Cron info
HOUR = int(os.getenv('HOUR', 0))
MINUTE = int(os.getenv('MINUTE', 0))
SECOND = int(os.getenv('SECOND', 10))
JITTER = int(os.getenv('JITTER', 0))

if __name__ == '__main__':
    scheduler = BlockingScheduler()
    scheduler.add_job(booking_job, 'interval', hours=HOUR, minutes=MINUTE, seconds=SECOND, jitter=JITTER)
    scheduler.start()
