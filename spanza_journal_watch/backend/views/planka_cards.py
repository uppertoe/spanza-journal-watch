"""Planka card content: metadata markers, labels, list mapping and review body parsing."""

import hashlib
import logging
import re

from ..models import (
    PlankaIssueBinding,
)
from ..planka import PlankaAPIError
from .shared import (
    PLANKA_JOURNAL_LABEL_COLORS,
    PLANKA_LEGACY_REVIEW_DESCRIPTION_TEMPLATE,
    PLANKA_LIST_LABELS,
    PLANKA_REVIEW_INSTRUCTIONS,
    PLANKA_REVIEW_SCAFFOLD,
    PLANKA_REVIEW_SEPARATOR_MARKER,
    _safe_planka_error,
)

logger = logging.getLogger(__name__)


def _get_issue_planka_candidates_list(batch, *, require_candidates_list=True):
    issue = batch.issue
    if not issue:
        return None, None, "Assign this batch to an issue before pushing to Planka."

    binding = PlankaIssueBinding.objects.filter(issue=issue).first()
    if not binding:
        return issue, None, "No Planka project linked to this issue. Set up Planka first."

    candidates_list_id = binding.get_list_id("candidates")
    if require_candidates_list and not candidates_list_id:
        return issue, binding, "Candidates list is not configured for this Planka board."

    return issue, binding, ""


def _build_pubmed_article_citation(article):
    parts = []
    if article.source_journal_name:
        parts.append(str(article.source_journal_name).strip())
    if article.publication_date:
        parts.append(article.publication_date.strftime("%Y-%m-%d"))
    if article.doi:
        parts.append(f"DOI: {article.doi}")
    if article.pmid:
        parts.append(f"PMID: {article.pmid}")
    return " · ".join([part for part in parts if part])


def _decode_planka_escaped_text(value):
    text = str(value or "")
    if not text:
        return ""

    def _replace_unicode(match):
        try:
            return chr(int(match.group(1), 16))
        except Exception:
            return match.group(0)

    decoded = re.sub(r"\\u([0-9a-fA-F]{4})", _replace_unicode, text)
    decoded = decoded.replace("\\n", "\n").replace("\\r", "\r")
    # Planka's markdown renderer escapes angle brackets and other special chars
    decoded = decoded.replace("\\<", "<").replace("\\>", ">")
    return decoded


def _parse_planka_card_metadata(description_text):
    description = _decode_planka_escaped_text(description_text)
    if PLANKA_REVIEW_SEPARATOR_MARKER in description:
        header_text, _ = description.split(PLANKA_REVIEW_SEPARATOR_MARKER, 1)
    else:
        header_text = description

    metadata = {
        "journal_name": "",
        "article_url": "",
        "article_year": "",
        "article_abstract": "",
        "article_citation": "",
    }

    journal_match = re.search(r"(?mi)^Journal:\s*(.+?)\s*$", header_text)
    if journal_match:
        metadata["journal_name"] = journal_match.group(1).strip()

    url_match = re.search(r"(?mi)^Article URL:\s*(.+?)\s*$", header_text)
    if url_match:
        metadata["article_url"] = url_match.group(1).strip().strip("<>")

    publication_match = re.search(r"(?mi)^Publication date:\s*([0-9]{4})(?:-[0-9]{2}(?:-[0-9]{2})?)?\s*$", header_text)
    if publication_match:
        metadata["article_year"] = publication_match.group(1).strip()

    abstract_match = re.search(r"(?ms)^Abstract\s*\n[-]{2,}\s*\n(?P<body>.+)$", header_text.strip())
    if abstract_match:
        metadata["article_abstract"] = abstract_match.group("body").strip()

    return metadata


def _get_board_label_map(*, client, board_id):
    _, included = client.get_board(board_id)
    labels = included.get("labels", []) or []
    return {
        str(label.get("name") or "").strip().lower(): str(label.get("id") or "").strip()
        for label in labels
        if str(label.get("name") or "").strip() and str(label.get("id") or "").strip()
    }


def _get_board_labels(*, client, board_id):
    _, included = client.get_board(board_id)
    return included.get("labels", []) or []


def _get_board_list_type_map(*, client, board_id):
    _, included = client.get_board(board_id)
    lists = included.get("lists", []) or []
    mapping = {}
    for item in lists:
        list_id = str(item.get("id") or "").strip()
        if not list_id:
            continue
        mapping[list_id] = str(item.get("type") or "").strip().lower()
    return mapping


def _get_next_board_label_position(*, client, board_id):
    labels = _get_board_labels(client=client, board_id=board_id)
    positions = []
    for label in labels:
        try:
            positions.append(int(float(label.get("position") or 0)))
        except (TypeError, ValueError):
            continue

    if not positions:
        return 65536
    return max(positions) + 65536


def _pick_journal_label_color(journal_name):
    normalized = str(journal_name or "").strip().lower()
    if not normalized:
        return "berry-red"

    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    index = int(digest[:8], 16) % len(PLANKA_JOURNAL_LABEL_COLORS)
    return PLANKA_JOURNAL_LABEL_COLORS[index]


def _pick_non_used_label_color(*, client, board_id, preferred_color):
    labels = _get_board_labels(client=client, board_id=board_id)
    used = {str(label.get("color") or "").strip() for label in labels if str(label.get("color") or "").strip()}

    preferred = str(preferred_color or "").strip()
    if preferred and preferred in PLANKA_JOURNAL_LABEL_COLORS and preferred not in used:
        return preferred

    for color in PLANKA_JOURNAL_LABEL_COLORS:
        if color not in used:
            return color

    if preferred and preferred in PLANKA_JOURNAL_LABEL_COLORS:
        return preferred
    return "berry-red"


def _ensure_existing_label_color(*, client, board_id, label_id, preferred_color):
    label_id = str(label_id or "").strip()
    if not label_id:
        return

    labels = _get_board_labels(client=client, board_id=board_id)
    current = next((label for label in labels if str(label.get("id") or "").strip() == label_id), None)
    if not current:
        return

    current_color = str(current.get("color") or "").strip()
    if not current_color:
        return

    # Keep existing color when it is unique on the board.
    color_counts = {}
    for label in labels:
        color = str(label.get("color") or "").strip()
        if not color:
            continue
        color_counts[color] = color_counts.get(color, 0) + 1

    if color_counts.get(current_color, 0) <= 1:
        return

    target_color = _pick_non_used_label_color(client=client, board_id=board_id, preferred_color=preferred_color)
    if not target_color or target_color == current_color:
        return

    client.update_label(label_id, color=target_color)


def _get_or_create_board_label_id(*, client, board_id, label_name, label_cache):
    normalized_name = str(label_name or "").strip()
    if not normalized_name:
        return ""

    cache_key = normalized_name.lower()
    cached = label_cache.get(cache_key)
    if cached:
        try:
            _ensure_existing_label_color(
                client=client,
                board_id=board_id,
                label_id=cached,
                preferred_color=_pick_journal_label_color(normalized_name),
            )
        except PlankaAPIError:
            pass
        return cached

    try:
        preferred_color = _pick_journal_label_color(normalized_name)
        label = client.create_label(
            board_id=board_id,
            name=normalized_name,
            color=_pick_non_used_label_color(
                client=client,
                board_id=board_id,
                preferred_color=preferred_color,
            ),
            position=_get_next_board_label_position(client=client, board_id=board_id),
        )
        label_id = str(label.get("id") or "").strip()
        if label_id:
            label_cache[cache_key] = label_id
        return label_id
    except PlankaAPIError:
        refreshed_map = _get_board_label_map(client=client, board_id=board_id)
        label_cache.update(refreshed_map)
        label_id = label_cache.get(cache_key, "")
        if label_id:
            try:
                _ensure_existing_label_color(
                    client=client,
                    board_id=board_id,
                    label_id=label_id,
                    preferred_color=_pick_journal_label_color(normalized_name),
                )
            except PlankaAPIError:
                pass
        return label_id


def _attach_journal_label_to_card(*, client, binding, card_id, row, label_cache):
    journal_name = str(row.article.source_journal_name or "").strip()
    if not journal_name:
        return

    label_id = _get_or_create_board_label_id(
        client=client,
        board_id=binding.board_id,
        label_name=journal_name,
        label_cache=label_cache,
    )
    if not label_id:
        return

    try:
        client.add_label_to_card(card_id=card_id, label_id=label_id)
    except PlankaAPIError as error:
        safe_error = _safe_planka_error(error).lower()
        if "e_conflict" in safe_error or "already" in safe_error:
            return
        raise


def _normalize_planka_review_body(text):
    body, _ = _extract_planka_review_body(text)
    return body


def _extract_planka_review_body(text):
    def _canonicalize(value):
        return re.sub(r"\s+", " ", str(value or "")).strip().lower()

    raw_text = _decode_planka_escaped_text(text)
    if PLANKA_REVIEW_SEPARATOR_MARKER in raw_text:
        _, review_text = raw_text.split(PLANKA_REVIEW_SEPARATOR_MARKER, 1)
        review_text = review_text.strip()
        return review_text, True

    body = raw_text.strip()
    if not body:
        return "", False

    if _canonicalize(body) == _canonicalize(PLANKA_LEGACY_REVIEW_DESCRIPTION_TEMPLATE):
        return "", False

    return body, False


def _refresh_binding_lists_from_board(*, client, binding):
    _, included = client.get_board(binding.board_id)
    lists = included.get("lists", []) or []

    key_by_label = {label.lower(): key for key, label in PLANKA_LIST_LABELS.items()}
    existing = dict(binding.lists or {})
    changed = False
    for list_item in lists:
        list_id = str(list_item.get("id") or "").strip()
        list_name = str(list_item.get("name") or "").strip().lower()
        key = key_by_label.get(list_name)
        if not key or not list_id:
            continue
        if existing.get(key) != list_id:
            existing[key] = list_id
            changed = True

    if changed:
        binding.lists = existing
        binding.save(update_fields=["lists", "modified"])


def _ensure_planka_board_mappings(*, client, binding):
    # Refresh list mappings from Planka so stale local ids do not break pushes.
    _refresh_binding_lists_from_board(client=client, binding=binding)


def _build_pubmed_planka_card(row):
    article = row.article
    title = (article.title or "").strip() or f"PMID {article.pmid}"
    lines = []

    if article.source_journal_name:
        lines.append(f"Journal: {article.source_journal_name}")

    if article.publication_date:
        lines.append(f"Publication date: {article.publication_date:%Y-%m-%d}")
    elif article.publication_month:
        lines.append(f"Publication date: {article.publication_month:%Y-%m}")

    if article.article_url:
        lines.append(f"Article URL: {article.article_url}")
    elif article.pubmed_url:
        lines.append(f"Article URL: {article.pubmed_url}")

    abstract = (article.abstract or "").strip()
    if abstract:
        lines.append("")
        lines.append("Abstract")
        lines.append("--------")
        lines.append(abstract)

    lines.append("")
    lines.extend(PLANKA_REVIEW_INSTRUCTIONS.splitlines())
    lines.append("")
    lines.append("---")
    lines.append(PLANKA_REVIEW_SEPARATOR_MARKER)
    lines.append("")
    lines.extend(PLANKA_REVIEW_SCAFFOLD.splitlines())

    return title, "\n".join(lines).strip()
