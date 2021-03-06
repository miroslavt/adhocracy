"""The base Controller API

Provides the BaseController class for subclassing.
"""
import logging

from paste.deploy.converters import asbool, asint
from pylons.controllers import WSGIController
from pylons import request, tmpl_context as c
from pylons.i18n import _
from sqlalchemy.orm.scoping import ScopedSession

from adhocracy import config
from adhocracy import i18n, model
from adhocracy.lib import helpers as h
from adhocracy.lib.templating import ret_abort

log = logging.getLogger(__name__)


class BaseController(WSGIController):

    def __call__(self, environ, start_response):
        """Invoke the Controller"""

        c.instance = model.instance_filter.get_instance()
        if c.instance is not None:
            # setup a global variable to mark the current item in
            # the global navigation
            c.active_global_nav = 'instances'
        else:
            c.active_global_nav = 'home'
        c.user = environ.get('repoze.who.identity', {}).get('user')

        # make sure we're not using a detached user object
        if c.user:
            c.user = model.meta.Session.merge(c.user)

        if c.user and (c.user.banned or c.user.delete_time):
            c.user = None
        c.active_controller = request.environ.get('pylons.routes_dict')\
            .get('controller')
        c.debug = config.get_bool('debug')
        i18n.handle_request()

        monitor_page_time_interval = config.get_int(
            'adhocracy.monitor_page_time_interval', -1)
        c.page_stats_url = h.base_url('/stats/on_page')
        if monitor_page_time_interval > 0:
            c.monitor_page_time_interval = monitor_page_time_interval

        if config.get_bool('adhocracy.monitor_external_links', False):
            c.monitor_external_links_url = h.base_url('/stats/record_external')

        if config.get_bool('adhocracy.monitor_browser_values', False):
            c.monitor_browser_values = "enabled"
        if config.get_bool('adhocracy.monitor_extended', False):
            c.monitor_extended = "enabled"
        if config.get_bool('adhocracy.monitor_page_performance', False):
            c.monitor_page_performance = "enabled"

        if config.get_bool('adhocracy.monitor_pager_clicks', False):
            c.monitor_pager_clicks = "enabled"

        h.add_rss("%s News" % h.site.name(),
                  h.base_url('/feed.rss', None))
        if c.instance:
            h.add_rss("%s News" % c.instance.label,
                      h.base_url('/instance/%s.rss' % c.instance.key))

        h.add_meta("description", config.get(
            'adhocracy.site.description',
            _(u"A liquid democracy platform for making decisions in "
              u"distributed, open groups by cooperatively creating "
              u"proposals and voting on them to establish their "
              u"support.")))

        h.add_meta("keywords",
                   _("adhocracy, direct democracy, liquid democracy, liqd, "
                     "democracy, wiki, voting,participation, group decisions, "
                     "decisions, decision-making"))

        try:
            return WSGIController.__call__(self, environ, start_response)
        except Exception, e:
            log.exception(e)
            model.meta.Session.rollback()
            raise
        finally:
            if isinstance(model.meta.Session, ScopedSession):
                model.meta.Session.remove()

    def bad_request(self, format='html'):
        log.debug("400 Request: %s" % request.params)
        return ret_abort(_("Invalid request. Please go back and try again."),
                         code=400, format=format)

    def not_implemented(self, format='html'):
        return ret_abort(_("The method you used is not implemented."),
                         code=400, format=format)
