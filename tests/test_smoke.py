from seo_scout import __version__


def test_version_is_pinned() -> None:
    assert __version__ == "0.1.0"
