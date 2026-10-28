"""Toolbar icons as inline SVG data URIs (no external files, works offline)."""
from urllib.parse import quote


def _uri(body):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">{body}</svg>'
    return "data:image/svg+xml;utf8," + quote(svg)


FIT = _uri('<path d="M4 12V4h8M20 4h8v8M4 20v8h8M28 20v8h-8" fill="none" stroke="#000" '
           'stroke-width="3" stroke-linecap="square"/>')
PIN = _uri('<path d="M16 2C9.4 2 5 6.6 5 12.5 5 20 16 30 16 30s11-10 11-17.5C27 6.6 22.6 2 16 2z"/>'
           '<circle cx="16" cy="12.5" r="4.5" fill="#fff"/>')
ZOOM = _uri('<circle cx="13" cy="13" r="8.5" fill="none" stroke="#000" stroke-width="3"/>'
            '<path d="M19.5 19.5L28 28" stroke="#000" stroke-width="3.5" stroke-linecap="round"/>')
PLAY = _uri('<circle cx="16" cy="16" r="13" fill="#fff" stroke="#1db954" stroke-width="2.5"/>'
            '<path d="M13 9.5l9 6.5-9 6.5z" fill="#1db954"/>')
STOP = _uri('<circle cx="16" cy="16" r="13" fill="#fff" stroke="#d93025" stroke-width="2.5"/>'
            '<rect x="11" y="11" width="10" height="10" fill="#d93025"/>')
