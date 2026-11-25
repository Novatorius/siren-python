"""Send an app's custom event. Run with SIREN_API_KEY set to the app's key."""

import os

import siren

client = siren.Siren(
    api_key=os.environ["SIREN_API_KEY"],  # the app's key
    base_url=os.environ.get("SIREN_BASE_URL", "https://api.sirenaffiliates.com/siren/v1"),
)

result = client.events.ingest("signup-completed", {"collaboratorId": 12, "plan": "pro"})
print(result.status)
