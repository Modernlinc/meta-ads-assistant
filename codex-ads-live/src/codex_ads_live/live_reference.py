"""Select a declared Live object from a playback hint and optional saved evidence."""
import re
from urllib.parse import parse_qs, urlsplit

from .client_workspace import numeric, workspace_identity
from .monitor import complete_row
from .monitor_tools import open_monitor
from .validation import fail


VIDEO_ID = r'[1-9][0-9]{0,39}'
PLAYBACK_HOSTS = {'facebook.com','www.facebook.com','m.facebook.com','web.facebook.com'}
PLAYBACK_PATHS = {'/watch','/watch/','/watch/live','/watch/live/','/live','/live/'}


def playback_video_id(url):
    if type(url) is not str or len(url)>4096 or re.search(r'[\x00-\x20\x7f\\]',url):
        fail('WORKSPACE_LIVE_LINK_FORMAT')
    try:
        parts = urlsplit(url)
        query = parse_qs(parts.query,keep_blank_values=True,max_num_fields=64)
    except ValueError:
        fail('WORKSPACE_LIVE_LINK_FORMAT')
    if parts.scheme!='https' or parts.netloc.lower() not in PLAYBACK_HOSTS:
        fail('WORKSPACE_LIVE_LINK_FORMAT')
    direct = re.fullmatch(r'/[A-Za-z0-9._-]{1,100}/videos/('+VIDEO_ID+r')/?',parts.path)
    if direct:
        video = direct[1]
        if 'v' in query and query['v']!=[video]:
            fail('WORKSPACE_LIVE_LINK_VIDEO_AMBIGUOUS')
        return video
    if parts.path in PLAYBACK_PATHS:
        video = query.get('v',[])
        if len(video)>1:
            fail('WORKSPACE_LIVE_LINK_VIDEO_AMBIGUOUS')
        if video and re.fullmatch(VIDEO_ID,video[0]):
            return video[0]
    fail('WORKSPACE_LIVE_LINK_FORMAT')


def saved_live_object(profile, page_id, video_id, directory, *, previous_page_run):
    monitor = open_monitor(directory,existing_only=True)
    state = (monitor.previous_page_run_status(require_relation=True) if previous_page_run
             else monitor.status(existing_only=True))
    if (state['mode']!='READ_ONLY' or state.get('workspace_identity_sha256')!=workspace_identity(profile)):
        fail('WORKSPACE_LIVE_LINK_SAVED_SCOPE')
    sources = state.get('sources',{})
    live = complete_row(sources,'live')
    page = complete_row(sources,'page')
    if page.get('id')!=page_id or live.get('from',{}).get('id')!=page_id:
        fail('WORKSPACE_LIVE_LINK_SAVED_PAGE_UNVERIFIED')
    if live.get('video',{}).get('id')!=video_id or sources['live']['observed_at'] is None:
        fail('WORKSPACE_LIVE_LINK_SAVED_VIDEO_MISMATCH')
    historical = None
    if previous_page_run:
        historical = dict(state['historical_page_run'],cycle=state['cycle_count'])
    return live['id'], sources['live']['observed_at'], historical


def select_live_reference(profile, page_id, live_id, video_id, url, directory, *, previous_page_run=False):
    live_id = numeric(live_id) if live_id is not None else None
    video_id = numeric(video_id) if video_id is not None else None
    if previous_page_run and (url is None or directory is None):
        fail('WORKSPACE_PREVIOUS_PAGE_RUN_REQUIRES_LINK_AND_STATE')
    if url is None:
        if directory is not None: fail('WORKSPACE_LIVE_STATE_REQUIRES_LINK')
        if live_id is None: fail('WORKSPACE_LIVE_ID_OR_LINK_REQUIRED')
        return live_id, video_id, None

    hint = playback_video_id(url)
    if video_id is not None and video_id!=hint:
        fail('WORKSPACE_LIVE_LINK_VIDEO_MISMATCH')
    prior_at = None
    historical = None
    basis = 'DECLARED_LIVE_ID_AND_PLAYBACK_LINK' if live_id is not None else 'PLAYBACK_LINK_ONLY'
    if directory is not None:
        prior_id, prior_at, historical = saved_live_object(profile,page_id,hint,directory,previous_page_run=previous_page_run)
        if live_id is not None and live_id!=prior_id:
            fail('WORKSPACE_LIVE_LINK_SAVED_LIVE_MISMATCH')
        live_id = prior_id
        basis = ('PREVIOUS_PAGE_LIVE_ROUND_AND_PLAYBACK_LINK' if historical is not None
                 else 'SAVED_PAGE_LIVE_RELATION_AND_PLAYBACK_LINK')
    reference = dict(basis=basis,video_id_hint=hint,prior_observed_at=prior_at,
                     current_relation_verified=False,network_used=False)
    if historical is not None:
        reference['historical_observation'] = historical
    return live_id, hint, reference
