"""The browser token address and Siren's request handler in Django. Add
path("siren", answers.django) and path("siren/token", siren_token) to urls.py."""

import os

import siren
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

client = siren.Siren(
    api_key=os.environ["SIREN_API_KEY"],
    base_url=os.environ.get("SIREN_BASE_URL", "https://api.sirenaffiliates.com/siren/v1"),
)

tokens = client.events.browser_token_handler(events=["signup-completed"])
answers = client.apps.request_handler(os.environ["SIREN_SIGNING_SECRET"])


@answers.on("postConversionAction", "tell-the-crm")
def tell_the_crm(request):
    # Siren may send it twice, so add it once, keyed by request.id.
    print("Conversion", request.data["conversion"]["id"], "for request", request.id)


@csrf_exempt
def siren_token(request):
    status, body = tokens(request.body, request.GET)
    return JsonResponse(body, status=status)
