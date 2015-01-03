# -*- coding: utf-8 -*-
from __future__ import print_function

import urlparse
import re
import collections
import itertools
import urllib

import unidecode

from restnavigator import exc, registry


def fix_scheme(url):
    '''Prepends the http:// scheme if necessary to a url. Fails if a scheme
    other than http is used'''
    splitted = url.split('://')
    if len(splitted) == 2:
        if splitted[0] in ('http', 'https'):
            return url
        else:
            raise exc.WileECoyoteException(
                'Bad scheme! Got: {}, expected http or https'.format(
                    splitted[0]))
    elif len(splitted) == 1:
        return 'http://' + url
    else:
        raise exc.ZachMorrisException('Too many schemes!')


def normalize_getitem_args(args):
    '''Turns the arguments to __getitem__ magic methods into a uniform
    list of tuples and strings
    '''
    if not isinstance(args, tuple):
        args = (args,)
    return_val = []
    for arg in args:
        if isinstance(arg, (basestring, int)):
            return_val.append(arg)
        elif isinstance(arg, slice):
            return_val.append((arg.start, arg.stop))
        else:
            raise TypeError(
                'Brackets cannot contain objects of type {.__name__}'
                .format(type(arg)))
    return return_val


def namify(root_uri):
    '''Turns a root uri into a less noisy representation that will probably
    make sense in most circumstances. Used by Navigator's __repr__, but can be
    overridden if the Navigator is created with a 'name' parameter.'''

    root_uri = unidecode.unidecode(urllib.unquote(root_uri).decode('utf-8'))

    generic_domains = set(['localhost', 'herokuapp', 'appspot'])
    urlp = urlparse.urlparse(fix_scheme(root_uri))
    formatargs = collections.defaultdict(list)

    netloc = urlp.netloc.lower()
    if ']' in netloc:
        domain = netloc.rsplit(']:', 1)[0]  # don't need port
    elif ':' in netloc:
        domain = netloc.rsplit(':', 1)[0]  # don't need port
    else:
        domain = netloc

    if not domain.translate(None, "abcdef:.[]").isdigit():
        if '.' in domain:
            domain, tld = domain.rsplit('.', 1)
        else:
            tld = ''
        if '.' in domain:
            subdomain, domain = domain.rsplit('.', 1)
        else:
            subdomain = ''

        if subdomain != 'www':
            formatargs['subdomain'] = subdomain.split('.')
        if domain not in generic_domains:
            formatargs['domain'].append(domain)
        if len(tld) == 2:
            formatargs['tld'].append(tld.upper())
        elif tld != 'com':
            formatargs['tld'].append(tld)

    formatargs['path'].extend(p for p in urlp.path.lower().split('/') if p)
    formatargs['qargs'].extend(r for q in urlp.query.split(',')
                               for r in q.split('=') if q and r)

    def capify(s):
        '''Capitalizes the first letter of a string, but doesn't downcase the
        rest like .title()'''
        return s if not s else s[0].upper() + s[1:]

    def piece_filter(piece):
        if piece.lower() == 'api':
            formatargs['api'] = True
            return ''
        elif re.match(r'v[\d.]+', piece):
            formatargs['version'].extend(['.', piece])
            return ''
        elif 'api' in piece:
            return piece.replace('api', 'API')
        else:
            return piece

    chain = itertools.chain
    imap = itertools.imap
    pieces = imap(capify, imap(piece_filter, chain(
        formatargs['subdomain'],
        formatargs['domain'],
        formatargs['tld'],
        formatargs['path'],
        formatargs['qargs'],
    )))
    return '{pieces}{api}{vrsn}'.format(pieces=''.join(pieces),
                                        api='API' if formatargs['api'] else '',
                                        vrsn=''.join(formatargs['version']),
    )


def objectify_uri(relative_uri):
    '''Converts uris from path syntax to a json-like object syntax.
    In addition, url escaped characters are unescaped, but non-ascii
    characters a romanized using the unidecode library.

    Examples:
       "/blog/3/comments" becomes "blog[3].comments"
       "car/engine/piston" becomes "car.engine.piston"
    '''
    def path_clean(chunk):
        if not chunk:
            return chunk
        if re.match(r'\d+$', chunk):
            return '[{}]'.format(chunk)
        else:
            return '.' + chunk

    byte_arr = relative_uri.encode('utf-8')
    unquoted = urllib.unquote(byte_arr).decode('utf-8')
    nice_uri = unidecode.unidecode(unquoted)
    return ''.join(path_clean(c) for c in nice_uri.split('/'))


class LinkList(list):
    '''A list subclass that offers different ways of grabbing the values based
    on various metadata stored for each entry in the dictionary.

    Note: Removing items from this list isn't really the point, so no attempt
    has been made to make this convenient. Deleting items will not remove them
    from the list's metadata.'''

    def __init__(self, items=None):
        super(LinkList, self).__init__()
        self._meta = {}
        items = items or []
        for obj, properties in items:
            self.append_with(obj, **properties)

    # Values coming in on properties might be unhashable, so we serialize them
    serialize = staticmethod(unicode)  # json comes in as unicode

    def append_with(self, obj, **properties):
        '''Add an item to the dictionary with the given metadata properties'''
        for prop, val in properties.iteritems():
            val = self.serialize(val)
            self._meta.setdefault(prop, {}).setdefault(val, []).append(obj)
        self.append(obj)

    def get_by(self, prop, val, raise_exc=False):
        '''Retrieve an item from the dictionary with the given metadata
        properties. If there is no such item, None will be returned, if there
        are multiple such items, the first will be returned.'''
        try:
            val = self.serialize(val)
            return self._meta[prop][val][0]
        except (KeyError, IndexError):
            if raise_exc:
                raise
            else:
                return None

    def getall_by(self, prop, val):
        '''Retrieves all items from the dictionary with the given metadata'''
        try:
            val = self.serialize(val)
            return self._meta[prop][val][:]  # return a copy of the list
        except KeyError:
            return []

    def named(self, name):
        '''Returns .get_by('name', name)'''
        name = self.serialize(name)
        return self.get_by('name', name)


class LinkDict(dict):
    '''dict subclass that allows specifying a default curie. This
    enables multiple ways to access an item'''

    def __init__(self, default_curie, d):
        super(LinkDict, self).__init__(d)
        self.default_curie = default_curie

    def __getitem__(self, key):
        if (':' in key
            or (key in self and key in registry.iana_rels)
            or self.default_curie is None):
            return super(LinkDict, self).__getitem__(key)
        implicit_key = '{}:{}'.format(self.default_curie, key)
        return super(LinkDict, self).__getitem__(implicit_key)
