"""Collaborative doodling backend.

Module layout mirrors the eventual Cloudflare Worker: everything except
`local_server` is meant to port unchanged.
"""

__all__ = ["claude", "config", "prompts", "svgdoc", "turn"]
