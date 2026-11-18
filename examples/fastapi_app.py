"""The browser token address in FastAPI. Run with: uvicorn fastapi_app:app"""

import os

import siren
from fastapi import FastAPI

client = siren.Siren(
    api_key=os.environ["SIREN_API_KEY"],
    base_url=os.environ.get("SIREN_BASE_URL", "https://api.sirenaffiliates.com/siren/v1"),
)

tokens = client.events.browser_token_handler(events=["signup-completed"])

app = FastAPI()
app.add_route("/siren/token", tokens.asgi, methods=["GET", "POST"])
