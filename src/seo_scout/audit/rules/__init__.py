"""Importing this package registers every rule, in the order the brief lists them."""

# isort: off
from seo_scout.audit.rules import (  # noqa: F401 - imported for their registration side effect
    title,
    meta,
    headings,
    content,
    canonical,
    robots_meta,
    images,
    links,
    redirects,
    performance,
    misc,
)
# isort: on
