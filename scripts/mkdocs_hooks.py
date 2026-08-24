# ABOUTME: mkdocs build hook that rewrites repo-relative doc links for the site
# ABOUTME: GitHub needs assets/docs/X.md; the flattened site needs X.md

"""Rewrite documentation links at site-build time.

README.md and QUICK_START.md live at the repository root and link to guides as
``assets/docs/GUIDE.md`` — correct on GitHub, where that is the real path. The
documentation site symlinks those guides to the ``docs/`` root, so mkdocs
serves them at ``/GUIDE/`` and the same link resolves to
``/QUICK_START/assets/docs/GUIDE.md`` → 404.

No single link string satisfies both (GitHub wants the prefix, the site wants
it gone), so the repository keeps the GitHub-correct form and this hook strips
the prefix while building.
"""

import re

# ']( assets/docs/GUIDE.md' -> ']( GUIDE.md', including nested
# distribution/ and providers/ paths and any ../ prefix.
_DOC_LINK = re.compile(r"(\]\(\s*)(?:\.\./)*assets/docs/")

# Guides reach back to the root docs as '../../README.md' / '../../QUICK_START.md'.
# On the site both sit at the docs root — README as the index page.
_ROOT_README = re.compile(r"(\]\(\s*)(?:\.\./)+README\.md")
_ROOT_DOC = re.compile(r"(\]\(\s*)(?:\.\./)+(QUICK_START\.md)")

# LICENSE is not published as a site page; point at the repository copy.
_LICENSE = re.compile(r"\]\(\s*(?:\.\./)*LICENSE\s*\)")
_LICENSE_URL = "](https://github.com/cdharma/claude-with-bedrock/blob/main/LICENSE)"


def on_page_markdown(markdown: str, page, config, files) -> str:
    """Rewrite repo-relative links so they resolve on the flattened site."""
    markdown = _DOC_LINK.sub(r"\1", markdown)
    markdown = _ROOT_README.sub(r"\1index.md", markdown)
    markdown = _ROOT_DOC.sub(r"\1\2", markdown)
    return _LICENSE.sub(_LICENSE_URL, markdown)
