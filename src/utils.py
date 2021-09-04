import datetime


def date_of_next_day(day_code):
    """
    Return the date of the next day represented by day_code
    Args:
        day_code (int): code of the days ued by datetime (mon=0, ... sun=6)

    Returns:

    """
    today = datetime.date.today()
    return (today + datetime.timedelta(days=(day_code - today.weekday() + 7) % 7)).strftime(
        "%d/%m/%Y"
    )
