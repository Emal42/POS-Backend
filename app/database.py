import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME")

client = MongoClient(MONGO_URI)

database = client[DATABASE_NAME]

def check_database():
    try:
        client.admin.command("ping")
        print("MongoDB connected successfully!")
        return True
    except Exception as error:
        print("MongoDB connection failed:", error)
        return False