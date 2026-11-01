"""Sign-in gateway for Planka.

Our Planka build (github.com/uppertoe/kanban-gateway-signin) signs people in
from identity headers that an authenticating reverse proxy attaches to every
request. On this site Django is the authority behind that proxy. Before Caddy
forwards a request to Planka it asks ``planka_gateway_check``; a 2xx answer
lets the request through with the Remote-* headers copied onto it, and any
other answer is returned to the browser as it is. Planka trusts the headers
only alongside a shared secret that Caddy adds and Django never sees.

The check runs on every Planka request, so it does only the session lookup
and the permission check. Anyone it cannot let through is sent to
``planka_entry`` on this site, which signs them in or explains the refusal
with the site's own styling, and then returns them to Planka.
"""

from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

MAX_FORWARDED_PATH_LENGTH = 2000


def may_use_planka(user):
    """Planka is for contributors and staff; a reader's account must not create a Planka user."""
    return user.is_staff or user.has_perm("submissions.invited_contributor")


def planka_groups(user):
    """The gateway groups Planka reads the role from.

    Chief editors become Planka administrators. Everyone else who may use
    Planka becomes a board user; board editing rights (adding lists, cards and
    labels) are granted per board by the issue builder, not by the role.
    """
    if not user.is_authenticated or not may_use_planka(user):
        return []
    if user.has_perm("submissions.chief_editor"):
        return [settings.PLANKA_GATEWAY_ADMIN_GROUP]
    return [settings.PLANKA_GATEWAY_USER_GROUP]


def safe_planka_path(value):
    """A path on the Planka host that the visitor may be returned to, or "/"."""
    if not value or not isinstance(value, str) or len(value) > MAX_FORWARDED_PATH_LENGTH:
        return "/"
    if not value.startswith("/") or value.startswith("//") or value.startswith("/\\"):
        return "/"
    if any(ord(ch) < 32 or ch in "\x7f" for ch in value):
        return "/"
    return value


def _entry_url(to):
    """Absolute URL of the entry page on this site, carrying the Planka path to return to."""
    url = settings.SITE_URL.rstrip("/") + reverse("planka_entry")
    if to and to != "/":
        url += "?" + urlencode({"to": to})
    return url


@never_cache
@require_GET
def planka_gateway_check(request):
    """Answer Caddy's forward_auth subrequest for planka.<domain>.

    Caddy sends the original request's cookies, so the Journal Watch session
    is visible here provided SESSION_COOKIE_DOMAIN covers the Planka host.
    """
    user = request.user
    if user.is_authenticated and may_use_planka(user):
        response = HttpResponse(status=204)
        response["Remote-Email"] = user.email
        response["Remote-User"] = user.email
        response["Remote-Groups"] = " ".join(planka_groups(user))
        return response

    # Caddy rewrites the subrequest to GET / and carries the original path here.
    return HttpResponseRedirect(_entry_url(safe_planka_path(request.headers.get("X-Forwarded-Uri", "/"))))


@never_cache
@login_required
def planka_entry(request):
    """Send a signed-in editorial account on to Planka, or explain why not.

    This page sits on the site's own host so that signing in can return here
    (allauth only follows a ``next`` on an allowed host) and the refusal page
    renders with the site's static files.
    """
    if not may_use_planka(request.user):
        return render(request, "backend/planka_not_permitted.html", status=403)

    base_url = (getattr(settings, "PLANKA_EXTERNAL_URL", "") or "").strip().rstrip("/")
    if not base_url:
        return render(request, "backend/planka_not_permitted.html", {"planka_unconfigured": True}, status=503)
    return HttpResponseRedirect(base_url + safe_planka_path(request.GET.get("to", "/")))
