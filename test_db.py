import logging
logging.basicConfig(level=logging.DEBUG)

print("Importing...")
from app.infrastructure.database import Database
from config import DATABASE_URL_LIVE
print("Instantiating...")
db = Database(DATABASE_URL_LIVE)
print("Done!")
