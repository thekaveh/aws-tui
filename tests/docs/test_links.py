from scripts.docs.links import (
    REPO_URL,
    SITE_URL,
    WIKI_URL,
    find_links,
    is_forbidden,
)


def test_find_links_extracts_targets():
    md = "See [a](one.md) and [b](https://x.test/y) and ![img](p.png)."
    assert [ln.target for ln in find_links(md)] == ["one.md", "https://x.test/y", "p.png"]


def test_site_forbids_repo_and_wiki_links():
    assert is_forbidden(f"{REPO_URL}/blob/main/src/x.py", "site") is True
    assert is_forbidden(f"{WIKI_URL}/Home", "site") is True
    assert is_forbidden(SITE_URL, "site") is False  # linking to self is fine
    assert is_forbidden("architecture.md", "site") is False  # internal


def test_wiki_forbids_repo_and_site_links():
    assert is_forbidden(f"{REPO_URL}/tree/main/docs", "wiki") is True
    assert is_forbidden(f"{SITE_URL}architecture/", "wiki") is True


def test_repo_forbids_site_and_wiki_but_allows_repo():
    assert is_forbidden(f"{SITE_URL}architecture/", "repo") is True
    assert is_forbidden(f"{WIKI_URL}/Home", "repo") is True
    assert is_forbidden(f"{REPO_URL}/blob/main/CHANGELOG.md", "repo") is False


def test_wiki_url_contains_repo_url_but_is_classified_as_wiki():
    # WIKI_URL startswith REPO_URL — must not be misclassified as a repo link.
    assert is_forbidden(f"{WIKI_URL}/Architecture", "site") is True
    assert is_forbidden(f"{WIKI_URL}/Architecture", "repo") is True
