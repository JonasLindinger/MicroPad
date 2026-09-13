# AI-assisted development — firmware, HA automation, config generator and docs were created with AI (LLM) help, reviewed and tested by the author. Provided as-is, without warranty; verify on your own hardware, don't use for safety-critical applications.
# ruff: noqa: E501
"""Validation and deterministic traversal of the page graph."""

from micropad.models import Page


class PageGraphError(ValueError):
    """Raised when page topology is invalid or cannot be traversed."""


def index_pages(pages: list[Page]) -> dict[str, Page]:
    """Index pages by identifier without changing their order."""
    index: dict[str, Page] = {}
    for page in pages:
        if page.page_id in index:
            raise PageGraphError(f"duplicate page_id: {page.page_id}")
        index[page.page_id] = page
    return index


def ancestor_chain(pages: list[Page], page_id: str) -> tuple[Page, ...]:
    """Return the requested page's ancestry in root-to-page order."""
    index = index_pages(pages)
    if page_id not in index:
        raise PageGraphError(f"unknown page_id: {page_id}")
    current = index[page_id]
    chain: list[Page] = []
    seen: set[str] = set()
    while True:
        if current.page_id in seen:
            raise PageGraphError(f"cycle at page_id: {current.page_id}")
        seen.add(current.page_id)
        chain.append(current)
        if not current.parent:
            break
        if current.parent not in index:
            raise PageGraphError(f"unknown parent: {current.parent}")
        current = index[current.parent]
    chain.reverse()
    return tuple(chain)


def validate_page_graph(pages: list[Page]) -> None:
    """Validate page topology without mutating list order."""
    index = index_pages(pages)
    if "home" not in index:
        raise PageGraphError("configuration requires exactly one home page")
    for page in pages:
        if page.parent and page.parent not in index:
            raise PageGraphError(f"unknown parent: {page.parent}")
        ancestor_chain(pages, page.page_id)
        for item in page.items:
            if item.target_page and item.target_page not in index:
                raise PageGraphError(f"unknown target_page: {item.target_page}")
    if index["home"].parent:
        raise PageGraphError("home page must not have a parent")
    for page in pages:
        if page.page_id != "home" and not page.parent:
            raise PageGraphError("non-home page must have a parent")
