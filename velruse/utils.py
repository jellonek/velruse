"""Utilities for the auth functionality"""
from pyramid.compat import PY3
if PY3:
    from urllib.parse import urlencode
else:
    from urllib import urlencode


def flat_url(url, **kw):
    """Creates a URL with the query param encoded"""
    url += '?' + urlencode(kw)
    return url
