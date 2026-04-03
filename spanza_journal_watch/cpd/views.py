import logging
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from spanza_journal_watch.backend.models import PubmedArticleUserState
from spanza_journal_watch.cpd.models import CPDReport
from spanza_journal_watch.submissions.models import Issue

logger = logging.getLogger(__name__)


@login_required
@require_POST
def toggle_cpd_tracking(request):
    request.user.cpd_tracking_enabled = not request.user.cpd_tracking_enabled
    request.user.save(update_fields=["cpd_tracking_enabled"])
    response = render(request, "fragments/cpd_tracking_toggle.html", {"user": request.user})
    response["HX-Trigger"] = "cpdTrackingChanged"
    return response


@login_required
def report_page(request):
    today = date.today()
    reports = CPDReport.objects.filter(user=request.user).order_by("-created")[:20]

    # Default date range: Jan 1 – Dec 31 of current year
    default_from = date(today.year, 1, 1)
    default_to = date(today.year, 12, 31)

    # Article count preview
    article_count = 0
    if request.user.cpd_tracking_enabled:
        article_count = PubmedArticleUserState.objects.filter(
            user=request.user,
            full_text_clicked_at__gte=default_from,
            full_text_clicked_at__lt=default_to + timedelta(days=1),
        ).count()

    sidebar_issues = list(Issue.objects.filter(active=True).order_by("-date", "-created")[:3])
    Issue.attach_display_images(sidebar_issues)

    return render(
        request,
        "cpd/report_page.html",
        {
            "reports": reports,
            "default_from": default_from,
            "default_to": default_to,
            "article_count": article_count,
            "sidebar_issues": sidebar_issues,
        },
    )


@login_required
@require_POST
def generate_report(request):
    from spanza_journal_watch.cpd.tasks import generate_cpd_report_task

    try:
        date_from = date.fromisoformat(request.POST["date_from"])
        date_to = date.fromisoformat(request.POST["date_to"])
    except (KeyError, ValueError):
        return HttpResponse("Invalid dates", status=400)

    if date_from > date_to:
        return HttpResponse("Start date must be before end date", status=400)

    user_reports = list(CPDReport.objects.filter(user=request.user).order_by("-created")[:20])

    # Prevent generating while one is already in progress
    if any(r.status in (CPDReport.Status.PENDING, CPDReport.Status.GENERATING) for r in user_reports):
        return render(request, "cpd/_report_list.html", {"reports": user_reports})

    # Enforce max 5 reports per user — delete oldest to make room
    if len(user_reports) >= 5:
        stale = user_reports[4:]
        for r in stale:
            if r.file:
                r.file.delete(save=False)
        CPDReport.objects.filter(pk__in=[r.pk for r in stale]).delete()

    report = CPDReport.objects.create(
        user=request.user,
        date_from=date_from,
        date_to=date_to,
    )
    generate_cpd_report_task.delay(report.pk)

    # Re-fetch to include the newly created report
    reports = CPDReport.objects.filter(user=request.user).order_by("-created")[:20]
    return render(request, "cpd/_report_list.html", {"reports": reports})


@login_required
def report_status(request):
    reports = CPDReport.objects.filter(user=request.user).order_by("-created")[:20]
    return render(request, "cpd/_report_list.html", {"reports": reports})


@login_required
def download_report(request, report_id):
    report = get_object_or_404(CPDReport, pk=report_id, user=request.user)
    if report.status != CPDReport.Status.READY or not report.file:
        return HttpResponse("Report not ready", status=404)

    response = HttpResponse(report.file.read(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="cpd_report_{report.date_from}_{report.date_to}.pdf"'
    return response


@login_required
def article_count_preview(request):
    try:
        date_from = date.fromisoformat(request.GET["date_from"])
        date_to = date.fromisoformat(request.GET["date_to"])
    except (KeyError, ValueError):
        return HttpResponse("0")

    count = PubmedArticleUserState.objects.filter(
        user=request.user,
        full_text_clicked_at__gte=date_from,
        full_text_clicked_at__lt=date_to + timedelta(days=1),
    ).count()
    return HttpResponse(str(count))
