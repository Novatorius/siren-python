"""The browser token address in Flask. Run with: flask --app flask_app run"""

import os

import siren
from flask import Flask
from werkzeug.middleware.dispatcher import DispatcherMiddleware

client = siren.Siren(
    api_key=os.environ["SIREN_API_KEY"],
    base_url=os.environ.get("SIREN_BASE_URL", "https://api.sirenaffiliates.com/siren/v1"),
)

tokens = client.events.browser_token_handler(events=["signup-completed"])

app = Flask(__name__)
app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {"/siren/token": tokens.wsgi})
