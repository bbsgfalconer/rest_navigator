'''A library to allow navigating rest apis easy.'''

from __future__ import unicode_literals
from __future__ import print_function

import copy
from weakref import WeakValueDictionary
import functools

import requests
import uritemplate

from rest_navigator import exc, utils


def autofetch(fn):
    '''A decorator used by Navigators that fetches the resource if necessary
    prior to calling the function '''

    @functools.wraps(fn)
    def wrapped(self, *args, **qargs):
        if self.response is None:
            self._GET()
        return fn(self, *args, **qargs)
    return wrapped


# TODO: Add __delitem__ to delete a linked resource
class HALNavigator(object):
    '''The main navigation entity'''

    def __init__(self, root, name=None):
        self.root = utils.fix_scheme(root)
        self.name = root if name is None else name
        self.uri = self.root
        self.profile = None
        self.title = None
        self.type = 'application/hal+json'
        self.response = None
        self.state = None
        self.template_uri = None
        self.template_args = None
        self.parameters = None
        self.templated = False
        self._links = None
        # This is the identity map shared by all descendents of this
        # HALNavigator
        self._id_map = WeakValueDictionary({self.root: self})

    def __repr__(self):
        return "HALNavigator('{.name}')".format(self)

    @property
    @autofetch
    def links(self):
        r'''Returns links from the current resource'''
        return self._links

    @property
    def status(self):
        if self.response is not None:
            return (self.response.status_code, self.response.reason)

    def _GET(self):
        r'''Handles GET requests for a resource'''
        if self.templated:
            raise exc.AmbiguousNavigationError(
                'This is a templated Navigator. You must provide values for '
                'the template parameters before fetching the resource or else '
                'explicitly null them out with the syntax: N[:]')
        self.response = requests.get(self.uri)
        body = self.response.json()

        def make_nav(rel, link):
            '''Crafts the Navigators for each link'''
            templated = link.get('templated', False)
            cp = self._copy(uri=link['href'] if not templated else None,
                            template_uri=link['href'] if templated else None,
                            templated=templated,
                            rel=rel,
                            title=link.get('title'),
                            type=link.get('type'),
                            profile=link.get('profile'),
                            )
            if templated:
                cp.uri = None
                cp.parameters = uritemplate.variables(cp.template_uri)
            else:
                cp.template_uri = None
            return cp

        self._links = {rel: make_nav(rel, link)
                       for rel, link in body.get('_links', {}).iteritems()
                       if rel != 'self'}
        self.title = body.get('_links', {}).get('self', {}).get(
            'title', self.title)
        self.state = {k: v for k, v in self.response.json().iteritems()
                      if k not in ('_links', '_embedded')}
        self.state.pop('_links', None)
        self.state.pop('_embedded', None)

    def _copy(self, **kwargs):
        '''Creates a shallow copy of the HALNavigator that extra attributes can
        be set on.

        If the object is already in the identity map, that object is returned
        instead.
        If the object is templated, it doesn't go into the id_map
        '''
        if 'uri' in kwargs and kwargs['uri'] in self._id_map:
            return self._id_map[kwargs['uri']]
        cp = copy.copy(self)
        cp._links = None
        cp.response = None
        cp.state = None
        cp.fetched = False
        for attr, val in kwargs.iteritems():
            if val is not None:
                setattr(cp, attr, val)
        if not cp.templated:
            self._id_map[cp.uri] = cp
        return cp

    def __eq__(self, other):
        return self.uri == other.uri and self.name == other.name

    @autofetch
    def __call__(self):
        return self.state.copy()

    def __iter__(self):
        '''Part of iteration protocol'''
        last = self
        while True:
            current = last.next()
            yield current
            last = current

    def next(self):
        try:
            return self['next']
        except KeyError:
            raise StopIteration()

    def expand(self, _keep_templated=False, **kwargs):
        '''Expand template args in a templated Navigator.

        if :_keep_templated: is True, the resulting Navigator can be further
        expanded. A Navigator created this way is not part of the id map.
        '''

        if not self.templated:
            raise TypeError(
                "This Navigator isn't templated! You can't expand it.")

        for k, v in kwargs.iteritems():
            if v == 0:
                kwargs[k] = '0'  # uritemplate expands 0's to empty string

        if self.template_args is not None:
            kwargs.update(self.template_args)
        cp = self._copy(uri=uritemplate.expand(self.template_uri, kwargs),
                        templated=_keep_templated,
                        )
        if not _keep_templated:
            cp.template_uri = None
            cp.template_args = None
        else:
            cp.template_args = kwargs

        return cp

    def __getitem__(self, getitem_args):
        r'''Subselector for a HALNavigator'''
        @autofetch
        def dereference(n, rels):
            '''Helper to recursively dereference'''
            if len(rels) == 1:
                ret = n.links[rels[0]]
                return ret._copy() if ret.templated else ret
            else:
                return dereference(n[rels[0]], rels[1:])

        rels, qargs, slug, ellipsis = utils.normalize_getitem_args(
            getitem_args)
        if slug and ellipsis:
            raise SyntaxError("':' and '...' syntax cannot be combined!")
        if rels:
            n = dereference(self, rels)
        else:
            n = self
        if qargs or slug:
            n = n.expand(_keep_templated=ellipsis, **qargs)
        return n
