import urllib
import cgi

from pylons.i18n import _

import adhocracy.model as model
from adhocracy.lib import cache

import proposal_helper as proposal
import url as _url

def icon_url(page, size=16):
    import text_helper as text
    return text.icon_url(page.head, page, size=size)


@cache.memoize('page_redlink')
def redlink(title):
    title = title.encode('utf-8')
    title = title.replace("&lt;", "<")
    title = title.replace("&gt;", ">")
    title = title.replace("&amp;", "&")
    url = urllib.quote(title)
    url = u"/page/new?title=%s" % url
    #url = instance_url(c.instance, path=url)
    return u"<a class='page_link new' href='%s'>%s</a>" % (url.encode('utf-8'), 
                                                           page.encode('utf-8'))


@cache.memoize('page_link')
def link(page, variant=model.Text.HEAD, link=True, icon=True, icon_size=16):
    import text_helper as text
    buf = cgi.escape(page.title)
    text_ = page.variant_head(variant)
    if variant != text_.HEAD:
        buf = u"%s <code>(%s)</code>" % (buf, variant)
    if icon: 
        buf = u"<img class='dgb_icon' src='%s' /> %s" % (text.icon_url(text_, page, size=icon_size), buf)
    if link and not page.is_deleted():
        buf = u"<a class='page_link exists' href='%s'>%s</a>" % (text.url(text_), buf)
    return buf
    

@cache.memoize('page_url')
def url(page, in_context=True, member=None, **kwargs):
    if in_context and page.proposal and not member:
        return proposal.url(page.proposal, **kwargs)
    label = urllib.quote(page.label.encode('utf-8'))
    return _url.build(page.instance, 'page', label, member=member, **kwargs)


@cache.memoize('page_bc')
def breadcrumbs(page):
    bc = _url.root()
    bc += _url.link(_("Documents"), u'/page')
    if page is not None:
        bc += _url.BREAD_SEP + _url.link(page.title, url(page))
    return bc