"""The browser token address in Django. Add path("siren/token", siren_token) to urls.py."""

import os

import siren
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

client = siren.Siren(
    api_key=os.environ["SIREN_API_KEY"],
    base_url=os.environ.get("SIREN_BASE_URL", "https://api.sirenaffiliates.com/siren/v1"),
)

tokens = client.events.browser_token_handler(events=["signup-completed"])


@csrf_exempt
def siren_token(request):
    status, body = tokens(request.body, request.GET)
    return JsonResponse(body, status=status)
