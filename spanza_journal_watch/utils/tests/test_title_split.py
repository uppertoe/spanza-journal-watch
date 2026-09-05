import pytest

from spanza_journal_watch.utils.functions import split_title


@pytest.mark.parametrize(
    "title,expected",
    [
        # colon: the common case
        (
            "Optimizing pediatric tonsillectomy outcomes with an opioid sparing anesthesia protocol: "
            "learning and continuously improving with real-world data",
            (
                "Optimizing pediatric tonsillectomy outcomes with an opioid sparing anesthesia protocol",
                "learning and continuously improving with real-world data",
            ),
        ),
        # hyphenated words before the colon must not split
        (
            "Point-of-care ultrasonography to predict fluid responsiveness in children: A systematic review",
            ("Point-of-care ultrasonography to predict fluid responsiveness in children", "A systematic review"),
        ),
        # hyphenated words and no colon: no subtitle at all
        (
            "Special considerations in the premature and ex-premature infant",
            ("Special considerations in the premature and ex-premature infant", ""),
        ),
        (
            "Postanesthesia complications in children with previous SARS-CoV-2 infection",
            ("Postanesthesia complications in children with previous SARS-CoV-2 infection", ""),
        ),
        # spaced dashes of any kind do split
        (
            "Supplemental intraoperative crystalloids for postoperative nausea - A systematic review",
            ("Supplemental intraoperative crystalloids for postoperative nausea", "A systematic review"),
        ),
        (
            "Caudal block in infants \u2013 a prospective evaluation",
            ("Caudal block in infants", "a prospective evaluation"),
        ),
        (
            "Caudal block in infants \u2014 a prospective evaluation",
            ("Caudal block in infants", "a prospective evaluation"),
        ),
        # the earliest separator wins
        (
            "Anaesthesia for scoliosis surgery - part two: outcomes",
            ("Anaesthesia for scoliosis surgery", "part two: outcomes"),
        ),
        # refuse splits that leave a stub title or an empty subtitle
        ("Editorial: the year in paediatric anaesthesia", ("Editorial: the year in paediatric anaesthesia", "")),
        (
            "A trailing separator should not produce a subtitle:",
            ("A trailing separator should not produce a subtitle:", ""),
        ),
        ("", ("", "")),
        (None, ("", "")),
    ],
)
def test_split_title(title, expected):
    assert split_title(title) == expected
