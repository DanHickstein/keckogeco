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
