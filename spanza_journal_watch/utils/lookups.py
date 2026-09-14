"""Custom ORM lookups.

``icontains`` renders as ``UPPER(col) LIKE UPPER(%s)``, which a trigram GIN
index cannot serve. ``ilike_contains`` renders as ``col ILIKE %s`` instead, so
substring searches on indexed text columns (article titles and abstracts) use
the index rather than scanning the table.
"""

from django.db.models import CharField, Lookup, TextField


class ILikeContains(Lookup):
    lookup_name = "ilike_contains"
    prepare_rhs = False

    def get_prep_lookup(self):
        value = str(self.rhs)
        escaped = value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
        return f"%{escaped}%"

    def as_sql(self, compiler, connection):
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        return f"{lhs} ILIKE {rhs}", [*lhs_params, *rhs_params]


def register_lookups():
    TextField.register_lookup(ILikeContains)
    CharField.register_lookup(ILikeContains)


def article_text_query(query, *, prefix="article__"):
    """Q for a free-text article search that stays on the trigram indexes.

    Title and abstract are searched with ``ilike_contains``. A PMID (all digits)
    or a DOI (``10.`` with a slash) is matched on its own column instead, because
    an OR that also carries an unindexed arm forces a sequential scan.
    """
    from django.db.models import Q

    text = (query or "").strip()
    if text.isdigit():
        return Q(**{f"{prefix}pmid": text}) | Q(**{f"{prefix}title__ilike_contains": text})
    if text.startswith("10.") and "/" in text:
        return Q(**{f"{prefix}doi__ilike_contains": text})
    return Q(**{f"{prefix}title__ilike_contains": text}) | Q(**{f"{prefix}abstract__ilike_contains": text})
