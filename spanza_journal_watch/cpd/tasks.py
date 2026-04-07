import logging
from datetime import timedelta

from django.core.files.base import ContentFile
from django.utils import timezone

from config.celery_app import app as celery_app
from spanza_journal_watch.backend.models import PubmedArticleUserState
from spanza_journal_watch.cpd.models import CPDReport
from spanza_journal_watch.cpd.pdf import generate_cpd_pdf

logger = logging.getLogger(__name__)


@celery_app.task(bind=True)
def generate_cpd_report_task(self, report_id):
    try:
        report = CPDReport.objects.select_related("user").get(pk=report_id)
    except CPDReport.DoesNotExist:
        logger.error("CPDReport %s not found", report_id)
        return

    report.status = CPDReport.Status.GENERATING
    report.celery_task_id = self.request.id or ""
    report.save(update_fields=["status", "celery_task_id"])

    try:
        # Query articles with full-text clicks in the date range
        # date_to is inclusive, so add 1 day for the upper bound
        states = (
            PubmedArticleUserState.objects.filter(
                user=report.user,
                full_text_clicked_at__gte=report.date_from,
                full_text_clicked_at__lt=report.date_to + timedelta(days=1),
            )
            .select_related("article")
            .order_by("full_text_clicked_at")
        )

        # Group articles by access date (first full-text click)
        from collections import OrderedDict

        grouped: OrderedDict = OrderedDict()
        for state in states:
            access_date = state.full_text_clicked_at.date()
            grouped.setdefault(access_date, []).append(state.article)
        articles_by_date = list(grouped.items())
        article_count = sum(len(arts) for _, arts in articles_by_date)

        pdf_bytes = generate_cpd_pdf(
            user_name=report.user.name or report.user.email,
            user_email=report.user.email,
            date_from=report.date_from,
            date_to=report.date_to,
            articles_by_date=articles_by_date,
        )

        filename = f"cpd_report_{report.user.pk}_{report.date_from}_{report.date_to}.pdf"
        report.file.save(filename, ContentFile(pdf_bytes), save=False)
        report.article_count = article_count
        report.status = CPDReport.Status.READY
        report.save(update_fields=["file", "article_count", "status"])

        logger.info("CPD report %s generated: %d articles", report_id, article_count)

    except Exception:
        logger.exception("Error generating CPD report %s", report_id)
        report.status = CPDReport.Status.ERROR
        report.error_message = "An error occurred while generating the report."
        report.save(update_fields=["status", "error_message"])
        raise


@celery_app.task
def cleanup_expired_cpd_reports():
    """Delete CPD reports older than 30 days."""
    cutoff = timezone.now() - timedelta(days=30)
    expired = CPDReport.objects.filter(created__lt=cutoff)
    # Delete files from storage before bulk-deleting records
    for report in expired.only("file"):
        if report.file:
            report.file.delete(save=False)
    count, _ = expired.delete()
    if count:
        logger.info("Cleaned up %d expired CPD reports", count)
