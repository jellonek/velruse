"""Facebook Authentication Views"""
from pyramid.compat import PY3

import datetime
import uuid
from json import loads

if PY3:
    from urllib.parse import parse_qs
else:  # pragma: no cover
    from urlparse import parse_qs

import requests

from pyramid.httpexceptions import HTTPFound
from pyramid.security import NO_PERMISSION_REQUIRED

from velruse.api import (
    AuthenticationComplete,
    AuthenticationDenied,
    register_provider,
)
from velruse.exceptions import CSRFError
from velruse.exceptions import ThirdPartyFailure
from velruse.settings import ProviderSettings
from velruse.utils import flat_url


class FacebookAuthenticationComplete(AuthenticationComplete):
    """Facebook auth complete"""


def includeme(config):
    config.add_directive('add_facebook_login', add_facebook_login)
    config.add_directive('setup_facebook_login_from_settings',
                         add_facebook_login_from_settings)


def add_facebook_login_from_settings(config, prefix='velruse.facebook.'):
    settings = config.registry.settings
    p = ProviderSettings(settings, prefix)
    p.update('consumer_key', required=True)
    p.update('consumer_secret', required=True)
    p.update('scope')
    p.update('login_path')
    p.update('callback_path')
    config.add_facebook_login(**p.kwargs)


def add_facebook_login(config,
                       consumer_key,
                       consumer_secret,
                       scope=None,
                       login_path='/login/facebook',
                       callback_path='/login/facebook/callback',
                       name='facebook'):
    """
    Add a Facebook login provider to the application.
    """
    provider = FacebookProvider(name, consumer_key, consumer_secret, scope)

    config.add_route(provider.login_route, login_path)
    config.add_view(provider.login, route_name=provider.login_route,
                    permission=NO_PERMISSION_REQUIRED)

    config.add_route(provider.callback_route, callback_path,
                     use_global_views=True,
                     factory=provider.callback)

    register_provider(config, name, provider)


class FacebookProvider(object):
    def __init__(self, name, consumer_key, consumer_secret, scope):
        self.name = name
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.scope = scope

        self.login_route = 'velruse.%s-login' % name
        self.callback_route = 'velruse.%s-callback' % name

    def login(self, request):
        """Initiate a facebook login"""
        scope = request.POST.get('scope', self.scope)
        request.session['state'] = state = uuid.uuid4().hex
        fb_url = flat_url(
            'https://www.facebook.com/dialog/oauth/',
            scope=scope,
            client_id=self.consumer_key,
            redirect_uri=request.route_url(self.callback_route),
            state=state)
        return HTTPFound(location=fb_url)

    def callback(self, request):
        """Process the facebook redirect"""
        if request.GET.get('state') != request.session.get('state'):
            raise CSRFError(
                'CSRF Validation check failed. Request state %s is not '
                'the same as session state %s' % (
                    request.GET.get('state'), request.session.get('state')))
        code = request.GET.get('code')
        if not code:
            reason = request.GET.get('error_reason', 'No reason provided.')
            return AuthenticationDenied(reason)

        # Now retrieve the access token with the code
        access_url = flat_url(
            'https://graph.facebook.com/oauth/access_token',
            client_id=self.consumer_key,
            client_secret=self.consumer_secret,
            redirect_uri=request.route_url(self.callback_route),
            code=code)
        r = requests.get(access_url)
        content = r.content.decode('UTF-8')
        if r.status_code != 200:
            raise ThirdPartyFailure("Status %s: %s" % (
                r.status_code, content))
        access_token = parse_qs(content)['access_token'][0]

        # Retrieve profile data
        graph_url = flat_url('https://graph.facebook.com/me',
                             access_token=access_token)
        r = requests.get(graph_url)
        content = r.content.decode('UTF-8')
        if r.status_code != 200:
            raise ThirdPartyFailure("Status %s: %s" % (
                r.status_code, content))
        fb_profile = loads(content)
        profile = extract_fb_data(fb_profile)

        cred = {'oauthAccessToken': access_token}
        return FacebookAuthenticationComplete(profile=profile,
                                              credentials=cred)


def extract_fb_data(data):
    """Extact and normalize facebook data as parsed from the graph JSON"""
    # Setup the normalized contact info
    nick = None

    # Setup the nick and preferred username to the last portion of the
    # FB link URL if its not their ID
    link = data.get('link')
    if link:
        last = link.split('/')[-1]
        if last != data['id']:
            nick = last

    profile = {
        'accounts': [{'domain':'facebook.com', 'userid':data['id']}],
        'displayName': data['name'],
        'verifiedEmail': data.get('email') if data.get('verified') else False,
        'gender': data.get('gender'),
        'preferredUsername': nick or data['name'],
    }
    if data.get('email'):
        profile['emails'] = [{'value':data.get('email')}]

    tz = data.get('timezone')
    if tz:
        parts = str(tz).split(':')
        if len(parts) > 1:
            h, m = parts
        else:
            h, m = parts[0], '00'
        if 1 < len(h) < 3:
            h = '%s0%s' % (h[0], h[1])  # e.g. -5 --> -05
        elif len(h) == 1:
            h = h[0]
        data['utfOffset'] = ':'.join([h, m])
    bday = data.get('birthday')
    if bday:
        try:
            mth, day, yr = bday.split('/')
            profile['birthday'] = datetime.date(int(yr), int(mth), int(day))
        except ValueError:
            pass
    name = {}
    pcard_map = {'first_name': 'givenName', 'last_name': 'familyName'}
    for key, val in pcard_map.items():
        part = data.get(key)
        if part:
            name[val] = part
    name['formatted'] = data.get('name')

    profile['name'] = name

    # Now strip out empty values
    for k, v in profile.items():
        if not v or (isinstance(v, list) and not v[0]):
            del profile[k]

    return profile
