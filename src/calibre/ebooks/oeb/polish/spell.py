#!/usr/bin/env python
# vim:fileencoding=utf-8
from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

__license__ = 'GPL v3'
__copyright__ = '2014, Kovid Goyal <kovid at kovidgoyal.net>'

import sys
from collections import defaultdict

from calibre.spell.break_iterator import split_into_words, index_of
from calibre.spell.dictionary import parse_lang_code
from calibre.ebooks.oeb.base import barename
from calibre.ebooks.oeb.polish.container import OPF_NAMESPACES, get_container
from calibre.ebooks.oeb.polish.toc import find_existing_toc

_patterns = None

class Patterns(object):

    __slots__ = ('sanitize_invisible_pat', 'split_pat', 'digit_pat')

    def __init__(self):
        import regex
        # Remove soft hyphens/zero width spaces/control codes
        self.sanitize_invisible_pat = regex.compile(
            r'[\u00ad\u200b\u200c\u200d\ufeff\0-\x08\x0b\x0c\x0e-\x1f\x7f]', regex.VERSION1 | regex.UNICODE)
        self.split_pat = regex.compile(
            r'\W+', flags=regex.VERSION1 | regex.WORD | regex.FULLCASE | regex.UNICODE)
        self.digit_pat = regex.compile(
            r'^\d+$', flags=regex.VERSION1 | regex.WORD | regex.UNICODE)

def patterns():
    global _patterns
    if _patterns is None:
        _patterns = Patterns()
    return _patterns

class Location(object):

    __slots__ = ('file_name', 'sourceline', 'original_word', 'location_node', 'node_item')

    def __init__(self, file_name=None, sourceline=None, original_word=None, location_node=None, node_item=(None, None)):
        self.file_name, self.sourceline, self.original_word = file_name, sourceline, original_word
        self.location_node, self.node_item = location_node, node_item

    def __repr__(self):
        return '%s:%s' % (self.file_name, self.sourceline)
    __str__ = __repr__

def filter_words(word):
    if not word:
        return False
    p = patterns()
    if p.digit_pat.match(word) is not None:
        return False
    return True

def get_words(text, lang):
    try:
        ans = split_into_words(unicode(text), lang)
    except (TypeError, ValueError):
        return ()
    return filter(filter_words, ans)

def add_words(text, node, words, file_name, locale, node_item):
    candidates = get_words(text, locale.langcode)
    if candidates:
        p = patterns()
        for word in candidates:
            sword = p.sanitize_invisible_pat.sub('', word)
            loc = Location(file_name, node.sourceline, word, node, node_item)
            words[(sword, locale)].append(loc)

def add_words_from_attr(node, attr, words, file_name, locale):
    text = node.get(attr, None)
    if text:
        add_words(text, node, words, file_name, locale, (True, attr))

def add_words_from_text(node, attr, words, file_name, locale):
    add_words(getattr(node, attr), node, words, file_name, locale, (False, attr))

_opf_file_as = '{%s}file-as' % OPF_NAMESPACES['opf']

def read_words_from_opf(root, words, file_name, book_locale):
    for tag in root.xpath('//*[namespace-uri()="%s"]' % OPF_NAMESPACES['dc']):
        tagname = barename(tag.tag)
        if not tag.text or tagname in {'identifier', 'language', 'date'}:
            continue
        add_words_from_text(tag, 'text', words, file_name, book_locale)
        add_words_from_attr(tag, _opf_file_as, words, file_name, book_locale)

def read_words_from_ncx(root, words, file_name, book_locale):
    for tag in root.xpath('//*[local-name()="text"]'):
        if not tag.text:
            continue
        add_words_from_text(tag, 'text', words, file_name, book_locale)

def read_words_from_html_tag(tag, words, file_name, parent_locale, locale):
    tagname = barename(tag.tag)
    if tagname not in {'script', 'style', 'link', 'head'}:
        if tag.text is not None:
            add_words_from_text(tag, 'text', words, file_name, locale)
        for attr in {'alt', 'title'}:
            add_words_from_attr(tag, attr, words, file_name, locale)
    if tag.tail is not None:
        add_words_from_text(tag, 'tail', words, file_name, parent_locale)

def locale_from_tag(tag):
    if 'lang' in tag.attrib:
        loc = parse_lang_code(tag.get('lang'))
        if loc is not None:
            return loc
    if '{http://www.w3.org/XML/1998/namespace}lang' in tag.attrib:
        loc = parse_lang_code(tag.get('{http://www.w3.org/XML/1998/namespace}lang'))
        if loc is not None:
            return loc

def read_words_from_html(root, words, file_name, book_locale):
    stack = [(root, book_locale)]
    while stack:
        parent, parent_locale = stack.pop()
        locale = locale_from_tag(parent) or parent_locale
        read_words_from_html_tag(parent, words, file_name, parent_locale, locale)
        stack.extend((tag, parent_locale) for tag in parent.iterchildren('*'))

def group_sort(locations):
    order = {}
    for loc in locations:
        if loc.file_name not in order:
            order[loc.file_name] = len(order)
    return sorted(locations, key=lambda l:(order[l.file_name], l.sourceline))

def get_all_words(container, book_locale):
    words = defaultdict(list)
    file_names = [name for name, linear in container.spine_names] + [container.opf_name]
    toc = find_existing_toc(container)
    if toc is not None and container.exists(toc):
        file_names.append(toc)
    for file_name in file_names:
        if not container.exists(file_name):
            continue
        root = container.parsed(file_name)
        if file_name == container.opf_name:
            read_words_from_opf(root, words, file_name, book_locale)
        elif file_name == toc:
            read_words_from_ncx(root, words, file_name, book_locale)
        else:
            read_words_from_html(root, words, file_name, book_locale)

    return {k:group_sort(v) for k, v in words.iteritems()}

def merge_locations(locs1, locs2):
    return group_sort(locs1 + locs2)

def replace(text, original_word, new_word, lang):
    indices = []
    original_word, new_word, text = unicode(original_word), unicode(new_word), unicode(text)
    q = text
    offset = 0
    while True:
        idx = index_of(original_word, q, lang=lang)
        if idx == -1:
            break
        indices.append(offset + idx)
        offset += idx + len(original_word)
        q = text[offset:]
    for idx in reversed(indices):
        text = text[:idx] + new_word + text[idx+len(original_word):]
    return text, bool(indices)

def replace_word(container, new_word, locations, locale):
    changed = set()
    for loc in locations:
        node = loc.location_node
        is_attr, attr = loc.node_item
        if is_attr:
            text = node.get(attr)
        else:
            text = getattr(node, attr)
        text, replaced = replace(text, loc.original_word, new_word, locale.langcode)
        if replaced:
            if is_attr:
                node.set(attr, text)
            else:
                setattr(node, attr, text)
            container.replace(loc.file_name, node.getroottree().getroot())
            changed.add(loc.file_name)
    return changed

if __name__ == '__main__':
    import pprint
    from calibre.gui2.tweak_book import set_book_locale, dictionaries
    container = get_container(sys.argv[-1], tweak_mode=True)
    set_book_locale(container.mi.language)
    pprint.pprint(get_all_words(container, dictionaries.default_locale))
