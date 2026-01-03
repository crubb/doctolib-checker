import datetime
import http.client
import json
import logging
import pathlib
import time
import urllib
import urllib.request

import yaml

# load config.yaml
with open(f"{pathlib.Path(__file__).parent.resolve()}/config.yaml", "r") as file:
    config = yaml.safe_load(file)

# yyyy-mm-dd
limit_date = config["limit_date"]
start_date = config["start_date"]

# Validate dates upon startup
datetime.datetime.strptime(limit_date, "%Y-%m-%d")
datetime.datetime.strptime(start_date, "%Y-%m-%d")

# max number of days in advance
limit = config["limit"]

# Configure logging
logging_level = getattr(logging, config.get("logging_level", "info").upper(), logging.INFO)

# Set up logging format
logging.basicConfig(
    level=logging_level,
    format="[%(levelname)s] %(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Create logger
logger = logging.getLogger(__name__)

# the url to fetch the data from. The start_date and limit will be replaced by the actual values
url = config["url"] % {"start_date": start_date, "limit": limit}


def send_pushover_notification(message):
    """Send a pushover notification"""

    conn = http.client.HTTPSConnection("api.pushover.net:443")
    conn.request(
        "POST",
        "/1/messages.json",
        urllib.parse.urlencode(
            {
                "token": config["pushover_credentials"]["api_token"],
                "user": config["pushover_credentials"]["user_key"],
                "message": message,
            }
        ),
        {"Content-type": "application/x-www-form-urlencoded"},
    )
    conn.getresponse()


def get_closest_available_time_slot(json_data, limit_date_obj):
    """Get the closest available time slot"""

    availabilities = json_data["availabilities"]
    closest_slot = None

    logger.debug(f"Checking {len(availabilities)} availability entries...")

    for slot in availabilities:
        if slot["slots"] == []:
            logger.debug(f"No slots available on {slot['date']}")
            continue

        logger.debug(f"Found {len(slot['slots'])} slot(s) on {slot['date']}:")
        for i, s in enumerate(slot["slots"]):
            logger.debug(f"  - Slot {i+1}: {format_string_to_date(s)}")

        if datetime.datetime.strptime(
            slot["date"][:10], "%Y-%m-%d"
        ) <= limit_date_obj:
            closest_slot = slot["slots"][0]
            logger.debug(f"Closest available slot selected: {format_string_to_date(closest_slot)}")
            break

    return closest_slot


def format_string_to_date(date):
    """Get a proper date string
    Input will be something like "2024-11-07T11:20:00.000+01:00"
    Output will be something like "2024-11-07 11:20:00"
    """
    return str(datetime.datetime.strptime(date[:-10], "%Y-%m-%dT%H:%M:%S"))


def main():
    logger.info(f"Starting with start_date={start_date}, limit_date={limit_date}, and limit={limit}...")

    while True:
        try:
            logger.debug(f"Fetching data from URL: {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "Magic Browser"})
            con = urllib.request.urlopen(req)
            logger.debug(f"HTTP response status: {con.getcode()}")
            json_data = json.loads(con.read())
            logger.debug(f"API response keys: {list(json_data.keys())}")

            logger.debug(f"Total appointments: {json_data.get('total', 0)}")
            limit_date_obj = datetime.datetime.strptime(limit_date, "%Y-%m-%d")

            closest_slot = None
            next_slot_date = None

            # there is a free slot in the next <limit> days
            if json_data["total"] > 0:
                logger.debug(f"Found {json_data['total']} appointment(s), checking for closest slot within limit_date {limit_date}")
                closest_slot = get_closest_available_time_slot(json_data, limit_date_obj)
                if closest_slot:
                    appointments = []
                    for slot in json_data["availabilities"]:
                        if slot["slots"] and datetime.datetime.strptime(slot["date"][:10], "%Y-%m-%d") <= limit_date_obj:
                            for s in slot["slots"]:
                                dt = format_string_to_date(s)
                                appointments.append(f"{dt[:10]} {dt[11:16]}")
                    appointments.sort()
                    
                    logger.info(f"Sending notification: Found {json_data['total']} appointment(s) with closest slot {format_string_to_date(closest_slot)}")
                    send_pushover_notification(
                        f"Found {json_data['total']} appointments available on Doctolib within {limit} day(s) from {start_date}:\n" + "\n".join(appointments)
                    )
                else:
                    logger.debug(f"Found {json_data['total']} appointment(s) but closest slot is after limit_date {limit_date}")

            # if next available slot is before the limit date
            elif json_data.get("next_slot"):
                logger.debug(f"Next slot: {format_string_to_date(json_data['next_slot'])}")
                next_slot_date = datetime.datetime.strptime(json_data["next_slot"][:10], "%Y-%m-%d")
                if next_slot_date <= limit_date_obj:
                    logger.info(f"Sending notification: next_slot {format_string_to_date(json_data['next_slot'])} is within limit_date {limit_date}")
                    send_pushover_notification(
                        f"New appointment available on Doctolib within your limit date! \nEarliest appointment: {format_string_to_date(json_data['next_slot'])}"
                    )
                else:
                    logger.debug(f"next_slot {next_slot_date.date()} exceeds limit_date {limit_date_obj.date()}")

            logger.info(
                f"Total: {json_data.get('total', 0)} | "
                f"Closest within limit: {(format_string_to_date(closest_slot)[:10] if closest_slot else '-')} | "
                f"Next slot (any): {(format_string_to_date(json_data['next_slot'])[:10] if json_data.get('next_slot') else '-')} | "
                f"Limit: {limit_date}"
            )

            # send an alive message at <hour_of_alive_check> o'clock and minute 0 everyday if <alive_check> is True
            if (
                config["alive_check"]
                and datetime.datetime.now().hour == config["hour_of_alive_check"]
                and datetime.datetime.now().minute == 0
            ):
                logger.debug(f"Sending alive check notification")
                send_pushover_notification(
                    f"Doctolib script is still running. \nLooking for appointments up to {limit_date}"
                )

            if config["run_in_loop"]:
                logger.debug(f"Waiting {config['interval_in_seconds']} seconds before next check")
                time.sleep(config["interval_in_seconds"])
            else:
                logger.debug("Single run mode - exiting")
                break

        except Exception as e:
            logger.error(f"Exception occurred: {e}")
            logger.error(f"Exception type: {type(e).__name__}")
            if logging_level == logging.DEBUG:
                import traceback
                logger.error(f"Traceback:\n{traceback.format_exc()}")
            send_pushover_notification(
                f"An error occured while running the Doctolib script: {e}"
            )


if __name__ == "__main__":
    main()
