"""The browser token address and Siren's request handler in Flask.
Run with: flask --app flask_app run"""

import os

import siren
from flask import Flask
from werkzeug.middleware.dispatcher import DispatcherMiddleware

client = siren.Siren(
    api_key=os.environ["SIREN_API_KEY"],
    base_url=os.environ.get("SIREN_BASE_URL", "https://api.sirenaffiliates.com/siren/v1"),
)

tokens = client.events.browser_token_handler(events=["signup-completed"])
answers = client.apps.request_handler(os.environ["SIREN_SIGNING_SECRET"])


@answers.on("distributionResolver", "split-by-score")
def split_by_score(request):
    pool, metrics = request.data["pool"], request.data["metrics"]
    shares = client.apps.split_pool(pool, {m["metricId"]: m["score"] for m in metrics})
    return {"outcome": "allocated", "units": pool["units"], "shares": shares}


app = Flask(__name__)
app.add_url_rule("/siren", view_func=answers.flask, methods=["POST"])
app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {"/siren/token": tokens.wsgi})
