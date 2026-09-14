"""Topic flags for cached articles.

An article's topics are derived from its MeSH terms, publication types, title
and abstract when it is saved (see backend.signals) and stored on
``PubmedArticle.topics`` with a GIN index, so the intake page and the journal
browser filter in SQL instead of re-reading every abstract per request.
Change a term list here, then run ``manage.py refresh_article_topics``.
"""

PAEDIATRIC_MESH_TERMS = {
    "Adolescent",
    "Child",
    "Child, Preschool",
    "Infant",
    "Infant, Newborn",
    "Pediatrics",
}
PAEDIATRIC_TEXT_TERMS = {
    "adolescent",
    "child",
    "children",
    "infant",
    "neonat",
    "newborn",
    "paediatric",
    "pediatric",
}
HUMANS_MESH_TERM = "Humans"
REVIEW_PUBLICATION_TYPES = ["Meta-Analysis", "Review", "Systematic Review"]
TRIAL_PUBLICATION_TYPES = ["Clinical Trial", "Randomized Controlled Trial"]

PAIN_TEXT_TERMS = {
    "analgesia",
    "analgesic",
    "nocicept",
    "opioid",
    "pain",
    "regional anaesthesia",
    "regional anesthesia",
}
PAIN_MESH_TERMS = {
    "Analgesia",
    "Pain",
    "Pain Management",
}
ICU_TEXT_TERMS = {
    "critical care",
    "icu",
    "intensive care",
    "sepsis",
    "ventilat",
}
ICU_MESH_TERMS = {
    "Critical Care",
    "Intensive Care Units",
    "Respiration, Artificial",
    "Sepsis",
}
CARDIAC_TEXT_TERMS = {
    "cardiac anaesthesia",
    "cardiac anesthesia",
    "cardiac surgery",
    "cardiopulmonary bypass",
    "cardiothoracic",
    "heart surgery",
}
CARDIAC_MESH_TERMS = {
    "Anesthesia, Cardiovascular",
    "Cardiac Surgical Procedures",
    "Cardiopulmonary Bypass",
}
NEONATAL_TEXT_TERMS = {
    "neonat",
    "newborn",
    "premature",
    "preterm",
}
NEONATAL_MESH_TERMS = {
    "Infant, Newborn",
    "Infant, Premature",
    "Premature Birth",
}

# name -> (MeSH terms, free-text terms, publication types); a topic applies when any group matches.
TOPIC_DEFINITIONS = {
    "paediatric": (PAEDIATRIC_MESH_TERMS, PAEDIATRIC_TEXT_TERMS, ()),
    "humans": ({HUMANS_MESH_TERM}, (), ()),
    "review": ((), (), REVIEW_PUBLICATION_TYPES),
    "trial": ((), (), TRIAL_PUBLICATION_TYPES),
    "pain": (PAIN_MESH_TERMS, PAIN_TEXT_TERMS, ()),
    "icu": (ICU_MESH_TERMS, ICU_TEXT_TERMS, ()),
    "cardiac": (CARDIAC_MESH_TERMS, CARDIAC_TEXT_TERMS, ()),
    "neonatal": (NEONATAL_MESH_TERMS, NEONATAL_TEXT_TERMS, ()),
}
TOPIC_NAMES = tuple(TOPIC_DEFINITIONS)


def _metadata_values(metadata, key):
    values = (metadata or {}).get(key) or []
    if not isinstance(values, list):
        return set()
    return {str(value).strip().lower() for value in values if str(value or "").strip()}


def compute_topics(title, abstract, metadata):
    """Topic names that apply to an article, from plain values (works for historical models too)."""
    mesh = _metadata_values(metadata, "mesh_terms")
    publication_types = _metadata_values(metadata, "publication_types")
    text = " ".join(
        [
            title or "",
            abstract or "",
            " ".join(_metadata_values(metadata, "keywords")),
            " ".join(mesh),
        ]
    ).lower()
    topics = []
    for name, (mesh_terms, text_terms, pub_types) in TOPIC_DEFINITIONS.items():
        if (
            any(term.lower() in mesh for term in mesh_terms)
            or any(term.lower() in publication_types for term in pub_types)
            or any(term.lower() in text for term in text_terms)
        ):
            topics.append(name)
    return topics


def article_topics(article):
    return compute_topics(article.title, article.abstract, article.metadata_json)
