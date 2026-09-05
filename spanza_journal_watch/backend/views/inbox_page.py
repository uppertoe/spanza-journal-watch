"""The editorial email inbox and replies."""

import logging
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .site_settings import _get_inbox_from_email

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def inbox(request):
    from ..models import EmailThread

    query = (request.GET.get("q") or "").strip()
    threads = EmailThread.objects.prefetch_related("inbound_messages", "sent_messages")
    if query:
        threads = threads.filter(Q(external_address__icontains=query) | Q(subject__icontains=query))

    paginator = Paginator(threads, 30)
    page = paginator.get_page(request.GET.get("page"))
    context = {"page": page, "query": query}
    if request.headers.get("HX-Request") == "true":
        return render(request, "backend/_inbox_thread_list.html", context)
    return render(request, "backend/inbox.html", context)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def inbox_mark_all_read(request):
    from ..models import EmailThread, InboundEmail

    query = (request.POST.get("q") or "").strip()
    threads = EmailThread.objects.all()
    if query:
        threads = threads.filter(Q(external_address__icontains=query) | Q(subject__icontains=query))

    thread_ids = list(threads.filter(has_unread=True).values_list("id", flat=True))
    if thread_ids:
        InboundEmail.objects.filter(thread_id__in=thread_ids, read=False).update(read=True)
        EmailThread.objects.filter(id__in=thread_ids).update(has_unread=False)

    if request.headers.get("HX-Request") == "true":
        mutable_get = request.GET.copy()
        if query:
            mutable_get["q"] = query
        else:
            mutable_get.pop("q", None)
        request.GET = mutable_get
        return inbox(request)

    messages.success(request, "All visible inbox threads marked as read.")
    if query:
        return redirect(f"{reverse('backend:inbox')}?q={query}")
    return redirect("backend:inbox")


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def inbox_thread(request, thread_id):
    from ..models import EmailThread

    thread = get_object_or_404(EmailThread, pk=thread_id)

    inbound_msgs = list(thread.inbound_messages.order_by("sent_timestamp", "pk"))
    sent_msgs = list(thread.sent_messages.order_by("created"))

    # Interleave into a unified chronological timeline
    timeline = sorted(
        [{"kind": "inbound", "obj": m, "at": m.sent_timestamp or m.created} for m in inbound_msgs]
        + [{"kind": "sent", "obj": m, "at": m.created} for m in sent_msgs],
        key=lambda x: x["at"],
    )

    # Mark thread as read
    if thread.has_unread:
        thread.inbound_messages.filter(read=False).update(read=True)
        thread.has_unread = False
        thread.save(update_fields=["has_unread"])

    return render(request, "backend/inbox_thread.html", {"thread": thread, "timeline": timeline})


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
@require_POST
def inbox_reply(request, thread_id):
    from email.utils import make_msgid, parseaddr

    from django.core.mail import EmailMessage

    from ..models import EmailThread, SentEmail

    thread = get_object_or_404(EmailThread, pk=thread_id)
    body = request.POST.get("body", "").strip()
    if not body:
        messages.error(request, "Reply body cannot be empty.")
        return redirect("backend:inbox_thread", thread_id=thread_id)

    subject = thread.subject or "(no subject)"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    from_email = _get_inbox_from_email()
    from_address = parseaddr(from_email)[1]
    from_domain = from_address.split("@")[-1].strip() if "@" in from_address else ""

    # Generate a Message-ID we track so future replies can be threaded
    msg_id = make_msgid(domain=from_domain or "journalwatch.org.au")

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=from_email,
        to=[thread.external_address],
        headers={"Message-ID": msg_id},
    )
    try:
        email.send()
    except Exception:
        logger.exception("Failed to send inbox reply to %s (thread %s)", thread.external_address, thread_id)
        messages.error(request, "Failed to send reply. Please try again.")
        return redirect("backend:inbox_thread", thread_id=thread_id)

    SentEmail.objects.create(
        thread=thread,
        recipient=thread.external_address,
        subject=subject,
        body=body,
        message_id=msg_id,
        sent_by=request.user,
    )
    thread.last_message_at = timezone.now()
    thread.save(update_fields=["last_message_at"])

    messages.success(request, f"Reply sent to {thread.external_address}.")
    return redirect("backend:inbox_thread", thread_id=thread_id)


# Docs
# ---------------------------------------------------------------------------

_DOCS_ROOT = Path(settings.BASE_DIR) / "docs" / "_build" / "html"

# The user guide is public so prospective coordinators and invited reviewers can
# read it before they have an account. Everything else in the built docs (the
# operations runbooks, the search index, the page sources) stays behind an
# editorial login.
_PUBLIC_DOCS_PREFIXES = ("user-guide/", "_static/", "_images/")
_PUBLIC_DOCS_FILES = frozenset({"index.html"})
