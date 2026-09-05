"""Serves the built user guide under the editorial site."""

import logging
import posixpath

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.views.static import serve as static_serve

from .inbox_page import _DOCS_ROOT, _PUBLIC_DOCS_FILES, _PUBLIC_DOCS_PREFIXES

logger = logging.getLogger(__name__)


def _docs_path_is_public(path):
    return path in _PUBLIC_DOCS_FILES or path.startswith(_PUBLIC_DOCS_PREFIXES)


def serve_docs(request, path=""):
    """Serve the built Sphinx documentation.

    The user guide is open to everyone; the rest requires an editorial role.
    """
    # Normalise before the public check so "user-guide/../operations/x.html"
    # cannot slip past it. django.views.static.serve does the same normalisation
    # again before touching the filesystem.
    path = posixpath.normpath(path or "index.html").lstrip("/")
    if not _docs_path_is_public(path):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not (
            request.user.has_perm("submissions.chief_editor")
            or request.user.has_perm("submissions.regional_coordinator")
        ):
            raise PermissionDenied
    return static_serve(request, path, document_root=str(_DOCS_ROOT))
