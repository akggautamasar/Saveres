from os import getenv
from time import time

class PyroConf(object):
    API_ID = int(getenv("API_ID"))
    API_HASH = getenv("API_HASH")
    BOT_TOKEN = getenv("BOT_TOKEN")
    SESSION_STRING = getenv("SESSION_STRING")

    BOT_START_TIME = time()
    MAX_CONCURRENT_DOWNLOADS = int(getenv("MAX_CONCURRENT_DOWNLOADS", "5"))
    MAX_CONCURRENT_UPLOADS = int(getenv("MAX_CONCURRENT_UPLOADS", "5"))
    MAX_CONCURRENT_TRANSMISSIONS = int(getenv("MAX_CONCURRENT_TRANSMISSIONS", "5"))
    BATCH_SIZE = int(getenv("BATCH_SIZE", "100000000"))
    FLOOD_WAIT_DELAY = int(getenv("FLOOD_WAIT_DELAY", "5"))
