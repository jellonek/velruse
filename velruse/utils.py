"""Utilities for the auth functionality"""
from pyramid.compat import PY3
if PY3:
    from urllib import urlencode
else:
    from urllib.parse import urlencode


def flat_url(url, **kw):
    """Creates a URL with the query param encoded"""
    url += '?' + urlencode(kw)
    return url
