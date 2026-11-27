"""Bounded single-image dynamic alternatives using named existing image assets."""
import re

from .business_profile import SECRET_PATTERN
from .validation import fail, object_only, validate_https_destination


FIELDS = ('images','titles','bodies','descriptions','link_urls','call_to_action_types','ad_formats')


def validate_assets(value):
    if (type(value) is not list or not 2 <= len(value) <= 5
            or any(type(item) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',item) for item in value)
            or len(set(value)) != len(value)):
        fail('META_DYNAMIC_IMAGE_SET')


def variants(value, *, maximum):
    if (type(value) is not list or not 1 <= len(value) <= 4
            or any(type(item) is not str or not 1 <= len(item.strip()) <= maximum or SECRET_PATTERN.search(item)
                   or any((ord(char) < 32 and char != '\n') or ord(char) == 127 for char in item)
                   for item in value) or len(set(value)) != len(value)):
        fail('META_DYNAMIC_TEXT_SET')


def build(params):
    value = params['dynamic_media']
    object_only(value, ('images','alternate_primary_texts','alternate_headlines'),
                ('images','alternate_primary_texts','alternate_headlines'))
    validate_assets(value['images'])
    variants(value['alternate_primary_texts'],maximum=1024)
    variants(value['alternate_headlines'],maximum=120)
    bodies = [params['primary_text'], *value['alternate_primary_texts']]
    titles = [params['headline'], *value['alternate_headlines']]
    feed = dict(images=[dict(hash=item) for item in value['images']], titles=[dict(text=item) for item in titles],
                bodies=[dict(text=item) for item in bodies], descriptions=[dict(text=params['description'])],
                link_urls=[dict(website_url=params['destination_url'])], call_to_action_types=[params['call_to_action']],
                ad_formats=['SINGLE_IMAGE'])
    return validate_feed(feed)


def validate_feed(feed):
    from .meta_write_mapping import CREATIVE_CTAS

    object_only(feed,FIELDS,FIELDS)
    if feed['ad_formats'] != ['SINGLE_IMAGE'] or feed['call_to_action_types'] not in ([item] for item in CREATIVE_CTAS):
        fail('META_DYNAMIC_CREATIVE_FORMAT')
    for field, key, size in (('images','hash',5),('titles','text',5),('bodies','text',5),
                             ('descriptions','text',1),('link_urls','website_url',1)):
        rows = feed[field]
        if type(rows) is not list or not 1 <= len(rows) <= size:
            fail('META_DYNAMIC_CREATIVE_FORMAT')
        for row in rows:
            object_only(row,(key,),(key,))
    validate_assets([row['hash'] for row in feed['images']])
    target = feed['link_urls'][0]['website_url']
    validate_https_destination(target,'META_DYNAMIC_DESTINATION')
    if len(target)>1000 or any(c.isspace() or ord(c)<32 or c=='\\' for c in target):
        fail('META_DYNAMIC_DESTINATION')
    for key, limit in (('titles',120),('bodies',1024),('descriptions',500)):
        values = [row['text'] for row in feed[key]]
        if ((key in ('titles','bodies') and len(values)<2) or any(
                type(value) is not str or not value.strip() or len(value)>limit or SECRET_PATTERN.search(value)
                or any((ord(c)<32 and c!='\n') or ord(c)==127 for c in value) for value in values)
                or len(set(values))!=len(values)):
            fail('META_DYNAMIC_TEXT_SET')
    return dict(feed)


def project_feed(source):
    if type(source) is not dict:
        fail('META_DYNAMIC_CREATIVE_FORMAT')
    values = {key:source[key] for key in FIELDS if key in source}
    for field, key in (('images','hash'),('titles','text'),('bodies','text'),
                       ('descriptions','text'),('link_urls','website_url')):
        rows = values.get(field)
        if type(rows) is not list or any(type(row) is not dict for row in rows):
            fail('META_DYNAMIC_CREATIVE_FORMAT')
        values[field] = [{key:row.get(key)} for row in rows]
    return validate_feed(values)
