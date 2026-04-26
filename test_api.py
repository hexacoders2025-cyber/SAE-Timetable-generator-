import requests

url = "http://127.0.0.1:5000/auto_fill_suggestion"
payload = {
    "class_name": "SE-A",
    "subject_id": 33,
    "duration": 1,
    "current_cells": []
}

try:
    response = requests.post(url, json=payload)
    print("Status:", response.status_code)
    print("Response:", response.text)
except Exception as e:
    print("Error:", e)
