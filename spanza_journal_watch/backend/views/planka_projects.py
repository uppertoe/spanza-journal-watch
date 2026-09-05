"""Provisioning and maintaining an issue's Planka project and board."""

import io
import logging
import re
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.files.base import ContentFile
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from PIL import Image, UnidentifiedImageError

from spanza_journal_watch.submissions.models import (
    Issue,
)

from ..forms import (
    PlankaProjectBackgroundForm,
    PlankaProjectNameForm,
    PlankaProjectSetupForm,
)
from ..models import (
    IssueContributor,
    PlankaBoardBackgroundAsset,
    PlankaIssueBinding,
    PlankaProjectSetupJob,
)
from ..planka import PlankaAPIError
from ..tasks import (
    provision_planka_project_task,
)
from . import planka_boards, shared
from .issue_context import _build_planka_publish_summary, _issue_builder_base_context
from .planka_boards import _build_planka_scope_counts, _filter_board_cards_by_scope, _register_planka_webhook
from .planka_panels import _render_planka_panel, _render_planka_project_context_card
from .shared import (
    PLANKA_INSTRUCTIONS_DIR,
    PLANKA_INSTRUCTIONS_LIST_COLORS,
    PLANKA_INSTRUCTIONS_LIST_LABELS,
    PLANKA_INSTRUCTIONS_LIST_ORDER,
    PLANKA_LIST_COLORS,
    PLANKA_LIST_LABELS,
    PLANKA_LIST_ORDER,
    _is_planka_board_not_found_error,
    _is_planka_connection_error,
    _safe_planka_error,
)

logger = logging.getLogger(__name__)


def _parse_instruction_cards(markdown_text):
    cards = []
    current_title = None
    current_body = []

    for line in (markdown_text or "").splitlines():
        heading_match = re.match(r"^##\s+(.+?)\s*$", line)
        if heading_match:
            if current_title:
                cards.append({"title": current_title, "body": "\n".join(current_body).strip()})
            current_title = heading_match.group(1).strip()
            current_body = []
            continue
        current_body.append(line)

    if current_title:
        cards.append({"title": current_title, "body": "\n".join(current_body).strip()})

    return [card for card in cards if card["title"]]


def _load_instruction_cards_by_bucket():
    cards_by_bucket = {}
    for bucket in PLANKA_INSTRUCTIONS_LIST_ORDER:
        path = PLANKA_INSTRUCTIONS_DIR / f"{bucket}.md"
        if not path.exists():
            cards_by_bucket[bucket] = []
            continue
        cards_by_bucket[bucket] = _parse_instruction_cards(path.read_text(encoding="utf-8"))

    return cards_by_bucket


def _normalize_background_to_webp(uploaded_file):
    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as image:
            image = image.convert("RGB")
            image.thumbnail((1920, 1080), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="WEBP", quality=90, method=6)
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("Uploaded file is not a valid image.") from error


def _resolve_background_asset(form, user):
    selected_asset = form.cleaned_data.get("background_asset")
    uploaded_file = form.cleaned_data.get("background_upload")

    if uploaded_file:
        webp_bytes = _normalize_background_to_webp(uploaded_file)
        filename_slug = slugify(Path(uploaded_file.name).stem) or "background"
        asset = PlankaBoardBackgroundAsset(
            name=f"{filename_slug} ({timezone.now().strftime('%Y-%m-%d %H:%M')})",
            uploaded_by=user,
        )
        asset.image.save(
            f"{filename_slug}-{timezone.now().strftime('%Y%m%d%H%M%S')}.webp", ContentFile(webp_bytes), save=True
        )
        return asset

    return selected_asset


def _provision_planka_project(client, project_name, background_asset=None):
    """
    Create a Planka project, Reviews board, Instructions board (with lists and
    instruction cards), and optional background image.

    Returns a dict with keys: project, board, instructions_board,
    list_mapping, instruction_list_mapping.
    """
    project = client.create_project(project_name)

    if background_asset:
        with background_asset.image.open("rb") as image_file:
            background_image = client.upload_project_background_image(
                project["id"],
                image_file,
                filename=Path(background_asset.image.name).name,
                content_type="image/webp",
            )
        background_image_id = background_image.get("id")
        if background_image_id:
            client.update_project_background(
                project["id"],
                background_type="image",
                background_image_id=background_image_id,
            )

    board = client.create_board(project["id"], name="Reviews")
    instructions_board = client.create_board(project["id"], name="Instructions", position=2 * 65536)

    list_mapping = {}
    for index, key in enumerate(PLANKA_LIST_ORDER, start=1):
        list_obj = client.create_list(
            board_id=board["id"],
            name=PLANKA_LIST_LABELS[key],
            position=index * 65536,
            list_type="active",
        )
        list_color = PLANKA_LIST_COLORS.get(key)
        if list_color:
            client.update_list(list_obj["id"], color=list_color)
        list_mapping[key] = list_obj["id"]

    instruction_cards = _load_instruction_cards_by_bucket()
    instruction_list_mapping = {}
    for index, key in enumerate(PLANKA_INSTRUCTIONS_LIST_ORDER, start=1):
        list_obj = client.create_list(
            board_id=instructions_board["id"],
            name=PLANKA_INSTRUCTIONS_LIST_LABELS[key],
            position=index * 65536,
            list_type="active",
        )
        list_color = PLANKA_INSTRUCTIONS_LIST_COLORS.get(key)
        if list_color:
            client.update_list(list_obj["id"], color=list_color)
        instruction_list_mapping[key] = list_obj["id"]

        cards_for_list = instruction_cards.get(key, [])
        for card_index, card in enumerate(cards_for_list, start=1):
            client.create_card(
                list_id=list_obj["id"],
                name=card["title"],
                description=card["body"],
                position=card_index * 65536,
                card_type="story",
            )

    return {
        "project": project,
        "board": board,
        "instructions_board": instructions_board,
        "list_mapping": list_mapping,
        "instruction_list_mapping": instruction_list_mapping,
    }


def _render_planka_setup_card(request, issue, panel_status=None, panel_status_level="info"):
    context = _issue_builder_base_context(
        issue=issue,
        planka_panel_status=panel_status,
        planka_panel_status_level=panel_status_level,
    )
    return render(request, "backend/issue_builder/_planka_setup_card.html", context)


def _render_planka_setup_status(request, issue, job, variant):
    return render(
        request,
        "backend/issue_builder/_planka_setup_status.html",
        {"selected_issue": issue, "job": job, "variant": variant},
    )


def _planka_setup_variant(request):
    """Which container submitted the setup form: the Setup tab card or the Pull Reviews panel."""
    if request.method == "POST":
        return "setup" if request.POST.get("from_setup_page") else "panel"
    return "setup" if (request.GET.get("variant") or "setup") == "setup" else "panel"


def _planka_setup_renderer(variant):
    return _render_planka_setup_card if variant == "setup" else _render_planka_panel


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def planka_setup_issue_project(request, issue_id):
    """Queue creation of the issue's Planka project and return a polling card.

    Provisioning used to run inside this request. A 3 MB background upload
    plus 15 seconds of Planka calls, with a header-only redirect as the sole
    result, was dropped somewhere between the origin and the browser and the
    page spun forever. Now the request only stores the inputs and enqueues the
    work; the card polls the job until the binding exists.
    """
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    variant = _planka_setup_variant(request)
    render_card = _planka_setup_renderer(variant)

    if hasattr(issue, "planka_binding"):
        return render_card(
            request,
            issue,
            panel_status="This issue is already linked to a Planka project.",
            panel_status_level="info",
        )

    existing_job = PlankaProjectSetupJob.objects.filter(issue=issue).first()
    if existing_job and existing_job.is_in_progress:
        return _render_planka_setup_status(request, issue, existing_job, variant)

    form = PlankaProjectSetupForm(request.POST, request.FILES)
    if not form.is_valid():
        return render_card(
            request,
            issue,
            panel_status="Check the background image and try again.",
            panel_status_level="danger",
        )

    # The project takes the issue's name unless the form (older clients, tests) supplies one.
    project_name = form.cleaned_data.get("project_name") or issue.name
    try:
        background_asset = _resolve_background_asset(form, request.user)
    except ValueError as error:
        return render_card(
            request,
            issue,
            panel_status=str(error),
            panel_status_level="danger",
        )

    job, _created = PlankaProjectSetupJob.objects.update_or_create(
        issue=issue,
        defaults={
            "project_name": project_name,
            "background_asset": background_asset,
            "requested_by": request.user,
            "state": PlankaProjectSetupJob.STATE_PENDING,
            "note": "Queued. Waiting for the background worker to pick this up.",
            "task_id": "",
        },
    )
    provision_planka_project_task.delay(job.pk)
    return _render_planka_setup_status(request, issue, job, variant)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def planka_setup_issue_project_status(request, issue_id):
    """Polled by the setup card until the job reaches a terminal state."""
    issue = get_object_or_404(Issue, pk=issue_id)
    variant = _planka_setup_variant(request)
    render_card = _planka_setup_renderer(variant)

    if hasattr(issue, "planka_binding"):
        return render_card(
            request,
            issue,
            panel_status="Planka project linked to this issue.",
            panel_status_level="success",
        )

    job = PlankaProjectSetupJob.objects.filter(issue=issue).first()
    if job is None:
        return render_card(request, issue)
    if job.is_in_progress:
        return _render_planka_setup_status(request, issue, job, variant)
    if job.state == PlankaProjectSetupJob.STATE_ERROR:
        return render_card(request, issue, panel_status=job.note, panel_status_level="danger")
    if job.is_stale:
        return render_card(
            request,
            issue,
            panel_status=(
                "The Planka setup did not finish. Check Planka for a partly created project before trying again."
            ),
            panel_status_level="warning",
        )
    return render_card(
        request,
        issue,
        panel_status="The setup finished but no Planka project is linked. Try again.",
        panel_status_level="warning",
    )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def planka_recreate_issue_board(request, issue_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)
    card_scope = (request.POST.get("card_scope") or "publish").strip().lower()
    if card_scope not in {"publish", "all"}:
        card_scope = "publish"

    try:
        client = shared._build_planka_client()

        # Check whether the project still exists. A 404 means the whole project
        # is gone, so we do a full rebuild instead of just recreating the board.
        project_gone = False
        try:
            client.get_project(binding.project_id)
        except PlankaAPIError as probe_error:
            if _is_planka_board_not_found_error(probe_error):
                project_gone = True
            else:
                raise

        if project_gone:
            # Full rebuild: new project + Reviews board + Instructions board
            result = _provision_planka_project(
                client,
                binding.project_name or issue.name,
                background_asset=binding.background_asset,
            )
            binding.project_id = result["project"]["id"]
            binding.project_name = result["project"].get("name") or binding.project_name
            binding.board_id = result["board"]["id"]
            binding.board_name = result["board"].get("name") or "Reviews"
            binding.instructions_board_id = result["instructions_board"]["id"]
            binding.instructions_board_name = result["instructions_board"].get("name") or "Instructions"
            binding.lists = result["list_mapping"]
            binding.instructions_lists = result["instruction_list_mapping"]
            binding.save(
                update_fields=[
                    "project_id",
                    "project_name",
                    "board_id",
                    "board_name",
                    "instructions_board_id",
                    "instructions_board_name",
                    "lists",
                    "instructions_lists",
                    "modified",
                ]
            )
            _register_planka_webhook(client, binding)
            status_msg = "Planka project, Reviews board, and Instructions board recreated from scratch."
        else:
            # Project exists — recreate only the Reviews board
            board = client.create_board(binding.project_id, name=(binding.board_name or "Reviews"))
            list_mapping = {}
            for index, key in enumerate(PLANKA_LIST_ORDER, start=1):
                list_obj = client.create_list(
                    board_id=board["id"],
                    name=PLANKA_LIST_LABELS[key],
                    position=index * 65536,
                    list_type="active",
                )
                list_color = PLANKA_LIST_COLORS.get(key)
                if list_color:
                    client.update_list(list_obj["id"], color=list_color)
                list_mapping[key] = list_obj["id"]

            binding.board_id = board["id"]
            binding.board_name = board.get("name") or "Reviews"
            binding.lists = list_mapping
            binding.save(update_fields=["board_id", "board_name", "lists", "modified"])
            _register_planka_webhook(client, binding)
            status_msg = "Reviews board recreated and remapped for this issue."

        # Re-sync all contributors (invited + active) so they have access to the new board.
        contributors_to_sync = IssueContributor.objects.filter(
            issue=issue,
            status__in=[IssueContributor.Status.INVITED, IssueContributor.Status.ACTIVE],
        )
        sync_errors = []
        for contributor in contributors_to_sync:
            ok, err = planka_boards._sync_contributor_to_planka(contributor)
            if not ok:
                sync_errors.append(f"{contributor.email}: {err}")
        if sync_errors:
            status_msg += f" Warning: {len(sync_errors)} contributor(s) could not be synced."

        board_cards = planka_boards._extract_board_cards(binding)
        scoped_cards = _filter_board_cards_by_scope(board_cards, card_scope)
        return _render_planka_panel(
            request,
            issue,
            publish_cards=scoped_cards,
            panel_status=status_msg,
            panel_status_level="success",
            planka_card_scope=card_scope,
            planka_scope_counts=_build_planka_scope_counts(board_cards),
        )
    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error)
        disconnected = _is_planka_connection_error(error)
        return _render_planka_panel(
            request,
            issue,
            publish_cards=[],
            panel_status=(
                "Not connected to Planka. Retrying in background…"
                if disconnected
                else f"Could not recreate Reviews board: {safe_error}"
            ),
            panel_status_level="danger",
            planka_disconnected=disconnected,
            planka_card_scope=card_scope,
            planka_scope_counts={"publish": 0, "all": 0},
            planka_board_missing=_is_planka_board_not_found_error(error),
        )


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def planka_update_project_name(request, issue_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)
    form = PlankaProjectNameForm(request.POST)
    redirect_url = f"{reverse('backend:issue_builder')}?issue={issue.pk}#planka-panel"

    if not form.is_valid():
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status="Please enter a valid project name.",
                card_status_level="danger",
            )
        messages.error(request, "Please enter a valid project name.")
        return redirect(redirect_url)

    project_name = form.cleaned_data["project_name"]

    try:
        client = shared._build_planka_client()
        client.update_project_name(binding.project_id, project_name)
    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error)
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status=f"Could not rename project: {safe_error}",
                card_status_level="danger",
            )
        messages.error(request, f"Could not rename project: {safe_error}")
        return redirect(redirect_url)

    binding.project_name = project_name
    binding.save(update_fields=["project_name", "modified"])

    if request.headers.get("HX-Request") == "true":
        return _render_planka_project_context_card(
            request,
            issue,
            card_status="Project name updated.",
            card_status_level="success",
        )

    messages.success(request, "Project name updated.")
    return redirect(redirect_url)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def planka_make_project_shared(request, issue_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)
    redirect_url = f"{reverse('backend:issue_builder')}?issue={issue.pk}#planka-panel"

    try:
        client = shared._build_planka_client()
        client.make_project_shared(binding.project_id)
    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error)
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status=f"Could not update project visibility: {safe_error}",
                card_status_level="danger",
            )
        messages.error(request, f"Could not update project visibility: {safe_error}")
        return redirect(redirect_url)

    if request.headers.get("HX-Request") == "true":
        return _render_planka_project_context_card(
            request,
            issue,
            card_status="Project is now shared — visible to added users and admins.",
            card_status_level="success",
        )

    messages.success(request, "Project is now shared — visible to added users and admins.")
    return redirect(redirect_url)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def planka_update_project_background(request, issue_id):
    if request.method != "POST":
        return HttpResponseBadRequest("Bad Request - POST only")

    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)
    form = PlankaProjectBackgroundForm(request.POST, request.FILES)
    redirect_url = f"{reverse('backend:issue_builder')}?issue={issue.pk}#planka-panel"

    if not form.is_valid():
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status="Please fix background image form errors.",
                card_status_level="danger",
            )
        messages.error(request, "Please fix background image form errors.")
        return redirect(redirect_url)

    try:
        background_asset = _resolve_background_asset(form, request.user)
    except ValueError as error:
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status=str(error),
                card_status_level="danger",
            )
        messages.error(request, str(error))
        return redirect(redirect_url)

    if not background_asset:
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status="Select or upload a background image first.",
                card_status_level="info",
            )
        messages.info(request, "Select or upload a background image first.")
        return redirect(redirect_url)

    try:
        client = shared._build_planka_client()
        with background_asset.image.open("rb") as image_file:
            background_image = client.upload_project_background_image(
                binding.project_id,
                image_file,
                filename=Path(background_asset.image.name).name,
                content_type="image/webp",
            )

        background_image_id = background_image.get("id")
        if not background_image_id:
            raise PlankaAPIError("Planka did not return a background image id.")

        client.update_project_background(
            binding.project_id,
            background_type="image",
            background_image_id=background_image_id,
        )
    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error)
        if request.headers.get("HX-Request") == "true":
            return _render_planka_project_context_card(
                request,
                issue,
                card_status=f"Could not update background image: {safe_error}",
                card_status_level="danger",
            )
        messages.error(request, f"Could not update background image: {safe_error}")
        return redirect(redirect_url)

    binding.background_asset = background_asset
    binding.save(update_fields=["background_asset", "modified"])

    if request.headers.get("HX-Request") == "true":
        return _render_planka_project_context_card(
            request,
            issue,
            card_status="Background image updated.",
            card_status_level="success",
        )

    messages.success(request, "Background image updated.")
    return redirect(redirect_url)


@login_required
@permission_required("submissions.chief_editor", raise_exception=True)
def planka_refresh_publish_cards(request, issue_id):
    issue = get_object_or_404(Issue, pk=issue_id)
    binding = get_object_or_404(PlankaIssueBinding, issue=issue)

    card_scope = (request.GET.get("scope") or "publish").strip().lower()
    if card_scope not in {"publish", "all"}:
        card_scope = "publish"

    try:
        board_cards = planka_boards._extract_board_cards(binding)
        scoped_cards = _filter_board_cards_by_scope(board_cards, card_scope)
    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error)
        disconnected = _is_planka_connection_error(error)
        board_missing = _is_planka_board_not_found_error(error)
        return _render_planka_panel(
            request,
            issue,
            publish_cards=[],
            panel_status=(
                "Not connected to Planka. Retrying in background…"
                if disconnected
                else "Linked Reviews board was not found in Planka. You can recreate the board for this issue."
                if board_missing
                else f"Could not refresh Planka cards: {safe_error}"
            ),
            panel_status_level="danger",
            planka_disconnected=disconnected,
            planka_card_scope=card_scope,
            planka_scope_counts={"publish": 0, "all": 0},
            planka_board_missing=board_missing,
        )

    summary = _build_planka_publish_summary(scoped_cards)
    return _render_planka_panel(
        request,
        issue,
        publish_cards=scoped_cards,
        panel_status=(
            f"Refresh complete. {summary['total']} cards loaded in this view "
            f"({summary['valid']} ready, {summary['missing']} with missing fields, "
            f"{summary['already_imported']} already imported/protected)."
        ),
        panel_status_level="success",
        planka_card_scope=card_scope,
        planka_scope_counts=_build_planka_scope_counts(board_cards),
    )
