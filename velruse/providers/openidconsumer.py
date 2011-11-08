import re
import logging

from openid.consumer import consumer
from openid.extensions import ax
from openid.extensions import sreg
from pyramid.request import Response
from pyramid.httpexceptions import HTTPFound
from pyramid.util import DottedNameResolver

from velruse.api import OpenIDAuthenticationComplete
from velruse.exceptions import AuthenticationDenied
from velruse.exceptions import MissingParameter
import velruse.utils as utils

dotted_resolver = DottedNameResolver(None)

log = logging.getLogger(__name__)

# Setup our attribute objects that we'll be requesting
ax_attributes = dict(
    nickname = 'http://axschema.org/namePerson/friendly',
    email    =  'http://axschema.org/contact/email',
    full_name = 'http://axschema.org/namePerson',
    birthday = 'http://axschema.org/birthDate',
    gender = 'http://axschema.org/person/gender',
    postal_code = 'http://axschema.org/contact/postalCode/home',
    country = 'http://axschema.org/contact/country/home',
    timezone = 'http://axschema.org/pref/timezone',
    language = 'http://axschema.org/pref/language',
    name_prefix = 'http://axschema.org/namePerson/prefix',
    first_name = 'http://axschema.org/namePerson/first',
    last_name = 'http://axschema.org/namePerson/last',
    middle_name = 'http://axschema.org/namePerson/middle',
    name_suffix = 'http://axschema.org/namePerson/suffix',
    web = 'http://axschema.org/contact/web/default',
)

#Change names later to make things a little bit clearer
alternate_ax_attributes = dict(
    nickname = 'http://schema.openid.net/namePerson/friendly',
    email = 'http://schema.openid.net/contact/email',
    full_name = 'http://schema.openid.net/namePerson',
    birthday = 'http://schema.openid.net/birthDate',
    gender = 'http://schema.openid.net/person/gender',
    postal_code = 'http://schema.openid.net/contact/postalCode/home',
    country = 'http://schema.openid.net/contact/country/home',
    timezone = 'http://schema.openid.net/pref/timezone',
    language = 'http://schema.openid.net/pref/language',
    name_prefix = 'http://schema.openid.net/namePerson/prefix',
    first_name = 'http://schema.openid.net/namePerson/first',
    last_name = 'http://schema.openid.net/namePerson/last',
    middle_name = 'http://schema.openid.net/namePerson/middle',
    name_suffix = 'http://schema.openid.net/namePerson/suffix',
    web = 'http://schema.openid.net/contact/web/default',
)

# Translation dict for AX attrib names to sreg equiv
trans_dict = dict(
    full_name = 'fullname',
    birthday = 'dob',
    postal_code = 'postcode',
)

attributes = ax_attributes


class AttribAccess(object):
    """Uniform attribute accessor for Simple Reg and Attribute Exchange values"""
    def __init__(self, sreg_resp, ax_resp):
        self.sreg_resp = sreg_resp or {}
        self.ax_resp = ax_resp or ax.AXKeyValueMessage()

    def get(self, key, ax_only=False):
        """Get a value from either Simple Reg or AX"""
        # First attempt to fetch it from AX
        v = self.ax_resp.getSingle(attributes[key])
        if v:
            return v
        if ax_only:
            return None
        
        # Translate the key if needed
        if key in trans_dict:
            key = trans_dict[key]
        
        # Don't attempt to fetch keys that aren't valid sreg fields
        if key not in sreg.data_fields:
            return None
        
        return self.sreg_resp.get(key)


def extract_openid_data(identifier, sreg_resp, ax_resp):
    """Extract the OpenID Data from Simple Reg and AX data
    
    This normalizes the data to the appropriate format.
    
    """
    attribs = AttribAccess(sreg_resp, ax_resp)
    
    ud = {'identifier': identifier}
    if 'google.com' in identifier:
        ud['providerName'] = 'Google'
    elif 'yahoo.com' in identifier:
        ud['providerName'] = 'Yahoo'
    else:
        ud['providerName'] = 'OpenID'
    
    # Sort out the display name and preferred username
    if ud['providerName'] == 'Google':
        # Extract the first bit as the username since Google doesn't return
        # any usable nickname info
        email = attribs.get('email')
        if email:
            ud['preferredUsername'] = re.match('(^.*?)@', email).groups()[0]
    else:
        ud['preferredUsername'] = attribs.get('nickname')
    
    # We trust that Google and Yahoo both verify their email addresses
    if ud['providerName'] in ['Google', 'Yahoo']:
        ud['verifiedEmail'] = attribs.get('email', ax_only=True)
    else:
        ud['emails'] = [attribs.get('email')]
    
    # Parse through the name parts, assign the properly if present
    name = {}
    name_keys = ['name_prefix', 'first_name', 'middle_name', 'last_name', 'name_suffix']
    pcard_map = {'first_name': 'givenName', 'middle_name': 'middleName', 'last_name': 'familyName',
                 'name_prefix': 'honorificPrefix', 'name_suffix': 'honorificSuffix'}
    full_name_vals = []
    for part in name_keys:
        val = attribs.get(part)
        if val:
            full_name_vals.append(val)
            name[pcard_map[part]] = val
    full_name = ' '.join(full_name_vals).strip()
    if not full_name:
        full_name = attribs.get('full_name')

    name['formatted'] = full_name
    ud['name'] = name
    
    ud['displayName'] = full_name or ud.get('preferredUsername')
    
    urls = attribs.get('web')
    if urls:
        ud['urls'] = [urls]
    
    for k in ['gender', 'birthday']:
        ud[k] = attribs.get(k)
    
    # Now strip out empty values
    for k, v in ud.items():
        if not v or (isinstance(v, list) and not v[0]):
            del ud[k]
    
    return ud


def includeme(config):
    settings = config.registry.settings
    if 'velruse.openid.store' not in settings:
        raise Exception("Missing 'velruse.openid.store' in config settings.")
    store = dotted_resolver.resolve(settings['velruse.openid.store'])()
    realm = settings['velruse.openid.realm']
    consumer = OpenIDConsumer(storage=store, realm=realm,
                              process_url='openid_process')
    config.add_route("openid_login", "/openid/login")
    config.add_route("openid_process", "/openid/process",
                     use_global_views=True,
                     factory=consumer.process)
    config.add_view(consumer.login, route_name="openid_login")


class OpenIDConsumer(object):
    """OpenID Consumer base class

    Providors using specialized OpenID based authentication subclass this.

    """
    def __init__(self, storage, realm, protocol=None, schema=None, process_url=None):
        self.openid_store = storage
        self.protocol = protocol
        self.schema = schema
        self.realm = realm
        self.process_url = process_url
        self.log_debug = logging.DEBUG >= log.getEffectiveLevel()
    
    def _lookup_identifier(self, request, identifier):
        """Extension point for inherited classes that want to change or set
        a default identifier"""
        return identifier

    def _update_authrequest(self, request, authrequest):
        """Update the authrequest with the default extensions and attributes
        we ask for

        This method doesn't need to return anything, since the extensions
        should be added to the authrequest object itself.

        """
        # Add on the Attribute Exchange for those that support that            
        ax_request = ax.FetchRequest()
        for attrib in attributes.values():
            ax_request.add(ax.AttrInfo(attrib))
        authrequest.addExtension(ax_request)

        # Form the Simple Reg request
        sreg_request = sreg.SRegRequest(
            optional=['nickname', 'email', 'fullname', 'dob', 'gender', 'postcode',
                      'country', 'language', 'timezone'],
        )
        authrequest.addExtension(sreg_request)
        return None

    def _get_access_token(self, request_token):
        """Called to exchange a request token for the access token

        This method doesn't by default return anything, other OpenID+Oauth
        consumers should override it to do the appropriate lookup for the
        access token, and return the access token.

        """
        return None

    def login(self, request):
        log_debug = self.log_debug
        if log_debug:
            log.debug('Handling OpenID login')

        # Load default parameters that all Auth Responders take
        openid_url = request.params.get('openid_identifier')

        # Let inherited consumers alter the openid identifier if desired
        openid_url = self._lookup_identifier(request, openid_url)

        if not openid_url:
            log.error('Velruse: no openid_url')
            raise MissingParameter('No openid_identifier was found')

        openid_session = {}
        oidconsumer = consumer.Consumer(openid_session, self.openid_store)

        try:
            if log_debug:
                log.debug('About to try OpenID begin')
            authrequest = oidconsumer.begin(openid_url)
        except consumer.DiscoveryFailure:
            if log_debug:
                log.debug('OpenID begin DiscoveryFailure')
            raise

        if authrequest is None:
            if log_debug:
                log.debug('OpenID begin returned empty')
            return self._error_redirect(1, end_point)

        if log_debug:
            log.debug('Updating authrequest')

        # Update the authrequest
        self._update_authrequest(request, authrequest)

        return_to = request.route_url(self.process_url)

        # OpenID 2.0 lets Providers request POST instead of redirect, this
        # checks for such a request.
        if authrequest.shouldSendRedirect():
            if log_debug:
                log.debug('About to initiate OpenID redirect')
                log.debug('realm = %s, return_to = %s, immediate = False' % (self.realm, return_to))
            redirect_url = authrequest.redirectURL(realm=self.realm, 
                                                   return_to=return_to, 
                                                   immediate=False)
            request.session['openid_session'] = openid_session
            return HTTPFound(location=redirect_url)
        else:
            if log_debug:
                log.debug('About to initiate OpenID POST')
                log.debug('realm = %s, return_to = %s, immediate = False' % (self.realm, return_to))
            html = authrequest.htmlMarkup(realm=self.realm, return_to=return_to, 
                                          immediate=False)
            request.session['openid_session'] = openid_session
            return Response(body=html)
    
    def process(self, request):
        """Handle incoming redirect from OpenID Provider"""
        log_debug = self.log_debug
        if log_debug:
            log.debug('Handling processing of response from server')
        
        openid_session = request.session.get('openid_session', None)
        del request.session['openid_session']
        if not openid_session:
            return self._error_redirect(1, end_point)
        
        # Setup the consumer and parse the information coming back
        oidconsumer = consumer.Consumer(openid_session, self.openid_store)
        return_to = request.route_url(self.process_url)
        info = oidconsumer.complete(request.params, return_to)
        
        if info.status in [consumer.FAILURE, consumer.CANCEL]:
            return self._error_redirect(2, end_point)
        elif info.status == consumer.SUCCESS:
            openid_identity = info.identity_url
            if info.endpoint.canonicalID:
                # If it's an i-name, use the canonicalID as its secure even if
                # the old one is compromised
                openid_identity = info.endpoint.canonicalID
            
            user_data = extract_openid_data(identifier=openid_identity, 
                                            sreg_resp=sreg.SRegResponse.fromSuccessResponse(info),
                                            ax_resp=ax.FetchResponse.fromSuccessResponse(info))
            # Did we get any OAuth info?
            oauth = info.extensionResponse('http://specs.openid.net/extensions/oauth/1.0', False)
            cred = {}
            if oauth and 'request_token' in oauth:
                access_token = self._get_access_token(oauth['request_token'])
                if access_token:
                    cred['oauthAccessToken'] = access_token
            
            # Delete the temporary token data used for the OpenID auth
            #self.storage.delete(req.session.id)
            return OpenIDAuthenticationComplete(
                profile=user_data, credentials=cred)
        else:
            return self._error_redirect(1, end_point)
