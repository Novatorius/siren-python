"""The browser token address and Siren's request handler in FastAPI.
Run with: uvicorn fastapi_app:app"""

import os

import siren
from fastapi import FastAPI

client = siren.Siren(
    api_key=os.environ["SIREN_API_KEY"],
    base_url=os.environ.get("SIREN_BASE_URL", "https://api.sirenaffiliates.com/siren/v1"),
)

tokens = client.events.browser_token_handler(events=["signup-completed"])
answers = client.apps.request_handler(os.environ["SIREN_SIGNING_SECRET"])


@answers.on("incentiveResolver", "split-the-reward")
async def split_the_reward(request):
    # An async def function is awaited after the 200 goes back.
    pool, conversions = request.data["pool"], request.data["conversions"]
    rewards = client.apps.split_pool(pool, {c["conversionId"]: c["score"] for c in conversions})
    return {"outcome": "rewards", "units": pool["units"], "rewards": rewards}


app = FastAPI()
app.add_route("/siren", answers.asgi, methods=["POST"])
app.add_route("/siren/token", tokens.asgi, methods=["GET", "POST"])
