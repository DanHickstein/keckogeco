"""Sphinx configuration for keckogeco."""

import keckogeco

project = "keckogeco"
copyright = "2026, the keckogeco developers"
author = "Daniel Hickstein"
version = release = keckogeco.__version__

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "myst_parser",
    "sphinx_copybutton",
]

autosummary_generate = True
autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
}
# Vendor SDKs (wsapi, mcculw) and heavy deps aren't installed on the docs
# builder; mock them so autodoc can import every module.
autodoc_mock_imports = ["wsapi", "mcculw", "PyQt6", "pyqtgraph"]

napoleon_google_docstring = False  # NumPy style only
napoleon_numpy_docstring = True

myst_enable_extensions = ["colon_fence"]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
}

templates_path = ["_templates"]
exclude_patterns = ["_build"]

html_theme = "pydata_sphinx_theme"
html_title = "keckogeco"
html_theme_options = {
    "github_url": "https://github.com/danhickstein/keckogeco",
    "navbar_end": ["theme-switcher", "navbar-icon-links"],
}

# PDF (latexpdf) settings
latex_documents = [
    ("index", "keckogeco.tex", "keckogeco Documentation", author, "manual"),
]

# pdflatex has no glyphs for these Unicode characters used in the Markdown
# sources; map each to a LaTeX equivalent so the PDF build doesn't abort.
latex_elements = {
    "preamble": r"""
\DeclareUnicodeCharacter{2070}{\ensuremath{^{0}}}
\DeclareUnicodeCharacter{00B9}{\ensuremath{^{1}}}
\DeclareUnicodeCharacter{2079}{\ensuremath{^{9}}}
\DeclareUnicodeCharacter{207B}{\ensuremath{^{-}}}
\DeclareUnicodeCharacter{2082}{\ensuremath{_{2}}}
\DeclareUnicodeCharacter{2083}{\ensuremath{_{3}}}
\DeclareUnicodeCharacter{2085}{\ensuremath{_{5}}}
\DeclareUnicodeCharacter{2192}{\ensuremath{\rightarrow}}
\DeclareUnicodeCharacter{2194}{\ensuremath{\leftrightarrow}}
\DeclareUnicodeCharacter{2212}{\ensuremath{-}}
\DeclareUnicodeCharacter{2248}{\ensuremath{\approx}}
\DeclareUnicodeCharacter{2264}{\ensuremath{\leq}}
\DeclareUnicodeCharacter{03C0}{\ensuremath{\pi}}
\DeclareUnicodeCharacter{03A9}{\ensuremath{\Omega}}
\DeclareUnicodeCharacter{00B5}{\ensuremath{\mu}}
\DeclareUnicodeCharacter{1F512}{\textbf{[locked]}}
""",
}
