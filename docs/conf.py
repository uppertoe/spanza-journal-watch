# Configuration file for the Sphinx documentation builder.
# https://www.sphinx-doc.org/en/master/usage/configuration.html

project = "SPANZA Journal Watch"
copyright = "2023, Eamonn Upperton"
author = "Eamonn Upperton"

extensions = [
    "myst_parser",
]

exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "furo"
