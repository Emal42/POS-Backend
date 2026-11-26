from fastapi import FastAPI
from app.database import check_database

app = FastAPI()

@app.get("/")
def home():
    return {"message": "POSperity Support API is running"}

@app.get("/test-database")
def test_database():
    if check_database():
        return {"message": "MongoDB connection successful!"}

    return {"message": "MongoDB connection failed"}