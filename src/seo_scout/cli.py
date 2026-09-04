"""Typer CLI entry point."""

from __future__ import annotations

import typer

from seo_scout import __version__

app = typer.Typer(help="SEO Scout: crawl, audit, and propose validated title/meta rewrites.")


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()
