from cgi import FieldStorage
import formencode
from formencode import Any, All, htmlfill, Invalid, validators
from pylons import request, tmpl_context as c
from pylons.controllers.util import redirect
from pylons.i18n import _

from adhocracy.forms.common import ValidInstanceGroup
from adhocracy.forms.common import ValidHTMLColor
from adhocracy.forms.common import ContainsChar
from adhocracy.forms.common import ValidBadgeInstance
from adhocracy.forms.common import ValidImageFileUpload
from adhocracy.forms.common import ValidFileUpload
from adhocracy.forms.common import ValidCategoryBadge
from adhocracy.forms.common import ValidParentCategory
from adhocracy.forms.common import ValidateNoCycle
from adhocracy.forms.common import ProposalSortOrder
from adhocracy.model import UPDATE
from adhocracy.model import meta
from adhocracy.model import Badge
from adhocracy.model import Group
from adhocracy.model import CategoryBadge
from adhocracy.model import DelegateableBadge
from adhocracy.model import InstanceBadge
from adhocracy.model import ThumbnailBadge
from adhocracy.model import UserBadge
from adhocracy.model import UPDATE
from adhocracy.lib import helpers as h
from adhocracy.lib.auth.authorization import has
from adhocracy.lib.auth.csrf import RequireInternalRequest
from adhocracy.lib.auth import guard
from adhocracy.lib.base import BaseController
from adhocracy.lib.behavior import behavior_enabled
from adhocracy.lib.pager import PROPOSAL_SORTS
from adhocracy.lib.queue import update_entity
from adhocracy.lib.templating import render


class BadgeForm(formencode.Schema):
    allow_extra_fields = True
    title = All(validators.String(max=40, not_empty=True),
                ContainsChar())
    description = validators.String(max=255)
    color = ValidHTMLColor()
    instance = ValidBadgeInstance()
    impact = validators.Int(min=-10, max=10, if_missing=0)
    if behavior_enabled():
        behavior_proposal_sort_order = ProposalSortOrder()


class CategoryBadgeForm(BadgeForm):
    select_child_description = validators.String(max=255)
    parent = ValidCategoryBadge(not_empty=False)
    chained_validators = [
        # make sure parent has same instance as we
        ValidParentCategory()
    ]


class CategoryBadgeUpdateForm(CategoryBadgeForm):
    id = ValidCategoryBadge(not_empty=True)
    chained_validators = [
        # make sure parent has same instance as we
        ValidParentCategory(),
        # make sure we don't create a cycle
        ValidateNoCycle(),
    ]


class UserBadgeForm(BadgeForm):
    group = Any(validators.Empty, ValidInstanceGroup())
    display_group = validators.StringBoolean(if_missing=False)


class ThumbnailBadgeForm(BadgeForm):
    thumbnail = All(ValidImageFileUpload(not_empty=False),
                    ValidFileUpload(not_empty=False), )


class BadgeController(BaseController):
    """Badge controller base class"""

    form_template = "/badge/form.html"
    index_template = "/badge/index.html"
    base_url_ = None

    def _available_badges(self):
        '''
        Return the badges that are editable by a user.
        '''
        c.groups = [{'permission': 'global.admin',
                     'label': _('In all instances'),
                     'show_label': True}]
        if c.instance:
            c.groups.append(
                {'permission': 'instance.admin',
                 'label': _('In instance "%s"') % c.instance.label,
                 'show_label': h.has_permission('global.admin')})
        badges = {}
        if has('global.admin'):
            badges['global.admin'] = {
                'instance': InstanceBadge.all(instance=None),
                'user': UserBadge.all(instance=None),
                'delegateable': DelegateableBadge.all(instance=None),
                'category': CategoryBadge.all(instance=None),
                'thumbnail': ThumbnailBadge.all(instance=None)}
        if has('instance.admin') and c.instance is not None:
            badges['instance.admin'] = {
                'instance': InstanceBadge.all(instance=c.instance),
                'user': UserBadge.all(instance=c.instance),
                'delegateable': DelegateableBadge.all(instance=c.instance),
                'category': CategoryBadge.all(instance=c.instance),
                'thumbnail': ThumbnailBadge.all(instance=c.instance)}
        return badges

    @property
    def base_url(self):
        if self.base_url_ is None:
            self.base_url_ = h.site.base_url(instance=c.instance,
                                             path='/badge')
        return self.base_url_

    @guard.perm('badge.index')
    def index(self, format='html'):
        c.badges = self._available_badges()
        c.badge_base_url = self.base_url
        return render(self.index_template, overlay=format == u'overlay')

    def _redirect_not_found(self, id):
        h.flash(_("We cannot find the badge with the id %s") % str(id),
                'error')
        redirect(self.base_url)

    def _set_parent_categories(self, exclude=None):
        local_categories = CategoryBadge.all_q(instance=c.instance)

        if exclude is not None:
            local_categories = filter(lambda c: not(c.is_ancester(exclude)),
                                      local_categories)

        c.local_category_parents = sorted(
            [(b.id, b.get_key()) for b in local_categories],
            key=lambda x: x[1])

        if h.has_permission('global.admin'):
            global_categories = CategoryBadge.all_q(instance=None)

            if exclude is not None:
                global_categories = filter(
                    lambda c: not(c.is_ancester(exclude)), global_categories)
            c.global_category_parents = sorted(
                [(b.id, b.get_key()) for b in global_categories],
                key=lambda x: x[1])

    @guard.instance.any_admin()
    def add(self, badge_type=None, errors=None, format=u''):
        data = {
            'form_type': 'add',
            'groups': Group.all_instance(),
            'return_url': self.base_url,
            'sorting_orders': PROPOSAL_SORTS,
        }
        if badge_type is not None:
            data['badge_type'] = badge_type

        defaults = {'visible': True,
                    'select_child_description': '',
                    'impact': 0,
                    }
        defaults.update(dict(request.params))

        self._set_parent_categories()

        html = render(self.form_template, data, overlay=format == u'overlay')
        return htmlfill.render(html,
                               defaults=defaults,
                               errors=errors,
                               force_defaults=False)

    def _dispatch(self, action, badge_type, id=None):
        '''
        dispatch to a suiteable "create" or "edit" action

        Methods are named <action>_<badge_type>_badge().
        '''
        assert action in ['create', 'update']
        types = ['user', 'delegateable', 'category', 'instance', 'thumbnail']
        if badge_type not in types:
            raise AssertionError('Unknown badge_type: %s' % badge_type)

        c.badge_type = badge_type
        c.form_type = action
        c.badge_base_url = self.base_url

        methodname = "%s_%s_badge" % (action, badge_type)
        method = getattr(self, methodname, None)
        if method is None:
            raise AttributeError(
                'Method not found for action "%s", badge_type: %s' %
                (action, badge_type))
        if id is not None:
            return method(id)
        else:
            return method()

    @guard.instance.any_admin()
    @RequireInternalRequest()
    def create(self, badge_type):
        return self._dispatch('create', badge_type)

    @guard.instance.any_admin()
    @RequireInternalRequest()
    def create_instance_badge(self):
        try:
            self.form_result = BadgeForm().to_python(request.params)
        except Invalid as i:
            return self.add('instance', i.unpack_errors())
        title, color, visible, description, impact, instance =\
            self._get_common_fields(self.form_result)
        InstanceBadge.create(title, color, visible, description, impact,
                             instance)
        # commit cause redirect() raises an exception
        meta.Session.commit()
        redirect(self.base_url)

    @guard.instance.any_admin()
    @RequireInternalRequest()
    def create_user_badge(self):
        try:
            self.form_result = UserBadgeForm().to_python(request.params)
        except Invalid as i:
            return self.add('user', i.unpack_errors())

        title, color, visible, description, impact, instance =\
            self._get_common_fields(self.form_result)
        group = self.form_result.get('group')
        display_group = self.form_result.get('display_group')
        UserBadge.create(title, color, visible, description, group,
                         display_group, impact, instance)
        # commit cause redirect() raises an exception
        meta.Session.commit()
        redirect(self.base_url)

    @guard.instance.any_admin()
    @RequireInternalRequest()
    def create_delegateable_badge(self):
        try:
            self.form_result = BadgeForm().to_python(request.params)
        except Invalid as i:
            return self.add('delegateable', i.unpack_errors())
        title, color, visible, description, impact, instance =\
            self._get_common_fields(self.form_result)
        DelegateableBadge.create(title, color, visible, description, impact,
                                 instance)
        # commit cause redirect() raises an exception
        meta.Session.commit()
        redirect(self.base_url)

    @guard.instance.any_admin()
    @RequireInternalRequest()
    def create_category_badge(self):
        try:
            self.form_result = CategoryBadgeForm().to_python(request.params)
        except Invalid as i:
            return self.add('category', i.unpack_errors())
        title, color, visible, description, impact, instance =\
            self._get_common_fields(self.form_result)
        child_descr = self.form_result.get("select_child_description")
        child_descr = child_descr.replace("$badge_title", title)
        parent = self.form_result.get("parent")
        if parent and parent.id == id:
            parent = None
        CategoryBadge.create(title, color, visible, description, impact,
                             instance, parent=parent,
                             select_child_description=child_descr)
        # commit cause redirect() raises an exception
        meta.Session.commit()
        redirect(self.base_url)

    @guard.instance.any_admin()
    @RequireInternalRequest()
    def create_thumbnail_badge(self):
        try:
            self.form_result = BadgeForm().to_python(request.params)
        except Invalid as i:
            return self.add('thumbnail', i.unpack_errors())
        title, color, visible, description, impact, instance =\
            self._get_common_fields(self.form_result)
        thumbnail = self.form_result.get("thumbnail")
        if isinstance(thumbnail, FieldStorage):
            thumbnail = thumbnail.file.read()
        else:
            thumbnail = None
        ThumbnailBadge.create(title, color, visible, description, thumbnail,
                              impact, instance)
        # commit cause redirect() raises an exception
        meta.Session.commit()
        redirect(self.base_url)

    def _get_common_fields(self, form_result):
        '''
        return a tuple of (title, color, visible, description, impact,
                           instance).
        '''
        if h.has_permission('global.admin'):
            instance = form_result.get('instance')
        else:
            # instance only admins can only create/edit
            # badges inside the current instance
            instance = c.instance
        return (form_result.get('title').strip(),
                form_result.get('color').strip(),
                'visible' in form_result,
                form_result.get('description').strip(),
                form_result.get('impact'),
                instance,
                )

    def _get_badge_type(self, badge):
        return badge.polymorphic_identity

    def _get_badge_or_redirect(self, id):
        '''
        Get a badge. Redirect if it does not exist. Redirect if
        the badge is not from the current instance, but the user is
        only an instance admin, not a global admin
        '''
        badge = Badge.by_id(id, instance_filter=False)
        if badge is None:
            self._redirect_not_found(id)
        if badge.instance != c.instance and not has('global.admin'):
            self._redirect_not_found(id)
        return badge

    @guard.instance.any_admin()
    def edit(self, id, errors=None, format=u'html'):
        badge = self._get_badge_or_redirect(id)
        data = {
            'badge_type': self._get_badge_type(badge),
            'badge_thumbnail': (
                h.badge_helper.generate_thumbnail_tag(badge)
                if getattr(badge, "thumbnail", None)
                else None
            ),
            'form_type': 'update',
            'return_url': self.base_url,
            'sorting_orders': PROPOSAL_SORTS,
        }
        self._set_parent_categories(exclude=badge)

        # Plug in current values
        instance_default = badge.instance.key if badge.instance else ''
        defaults = dict(
            title=badge.title,
            description=badge.description,
            color=badge.color,
            visible=badge.visible,
            display_group=badge.display_group,
            impact=badge.impact,
            instance=instance_default,
            behavior_proposal_sort_order=badge.behavior_proposal_sort_order)
        if isinstance(badge, UserBadge):
            c.groups = Group.all_instance()
            defaults['group'] = badge.group and badge.group.code or ''
        if isinstance(badge, CategoryBadge):
            defaults['parent'] = badge.parent and badge.parent.id or ''
            defaults['select_child_description'] =\
                badge.select_child_description

        return htmlfill.render(render(self.form_template, data,
                                      overlay=format == u'overlay'),
                               errors=errors,
                               defaults=defaults,
                               force_defaults=False)

    @guard.instance.any_admin()
    @RequireInternalRequest()
    def update(self, id):
        badge = self._get_badge_or_redirect(id)
        c.badge_type = self._get_badge_type(badge)
        return self._dispatch('update', c.badge_type, id=id)

    @guard.instance.any_admin()
    @RequireInternalRequest()
    def update_user_badge(self, id):
        try:
            self.form_result = UserBadgeForm().to_python(request.params)
        except Invalid as i:
            return self.edit(id, i.unpack_errors())

        badge = self._get_badge_or_redirect(id)
        title, color, visible, description, impact, instance =\
            self._get_common_fields(self.form_result)
        group = self.form_result.get('group')
        display_group = self.form_result.get('display_group')

        badge.group = group
        badge.title = title
        badge.color = color
        badge.visible = visible
        badge.description = description
        if badge.impact != impact:
            badge.impact = impact
            for user in badge.users:
                update_entity(user, UPDATE)
        badge.instance = instance
        badge.display_group = display_group
        if behavior_enabled():
            badge.behavior_proposal_sort_order = self.form_result.get(
                'behavior_proposal_sort_order')
        meta.Session.commit()
        h.flash(_("Badge changed successfully"), 'success')
        redirect(self.base_url)

    @guard.instance.any_admin()
    @RequireInternalRequest()
    def update_delegateable_badge(self, id):
        try:
            self.form_result = BadgeForm().to_python(request.params)
        except Invalid as i:
            return self.edit(id, i.unpack_errors())
        badge = self._get_badge_or_redirect(id)
        title, color, visible, description, impact, instance =\
            self._get_common_fields(self.form_result)

        badge.title = title
        badge.color = color
        badge.visible = visible
        badge.description = description
        if badge.impact != impact:
            badge.impact = impact
            for delegateable in badge.delegateables:
                update_entity(delegateable, UPDATE)
        badge.instance = instance
        meta.Session.commit()
        h.flash(_("Badge changed successfully"), 'success')
        redirect(self.base_url)

    @guard.instance.any_admin()
    @RequireInternalRequest()
    def update_instance_badge(self, id):
        try:
            self.form_result = BadgeForm().to_python(request.params)
        except Invalid as i:
            return self.edit(id, i.unpack_errors())
        badge = self._get_badge_or_redirect(id)
        title, color, visible, description, impact, instance =\
            self._get_common_fields(self.form_result)

        badge.title = title
        badge.color = color
        badge.visible = visible
        badge.description = description
        if badge.impact != impact:
            badge.impact = impact
            for instance in badge.instances:
                update_entity(instance, UPDATE)
        badge.instance = instance
        meta.Session.commit()
        h.flash(_("Badge changed successfully"), 'success')
        redirect(self.base_url)

    @guard.instance.any_admin()
    @RequireInternalRequest()
    def update_category_badge(self, id):
        try:
            params = request.params.copy()
            params['id'] = id
            self.form_result = CategoryBadgeUpdateForm().to_python(params)
        except Invalid as i:
            return self.edit(id, i.unpack_errors())
        badge = self._get_badge_or_redirect(id)
        title, color, visible, description, impact, instance =\
            self._get_common_fields(self.form_result)
        child_descr = self.form_result.get("select_child_description")
        child_descr = child_descr.replace("$badge_title", title)
        #TODO global badges must have only global badges children, joka
        parent = self.form_result.get("parent")
        if parent and parent.id == id:
            parent = None
        badge.title = title
        badge.color = color
        badge.visible = visible
        badge.description = description
        if badge.impact != impact:
            badge.impact = impact
            for delegateable in badge.delegateables:
                update_entity(delegateable, UPDATE)
        badge.instance = instance
        badge.select_child_description = child_descr
        badge.parent = parent
        meta.Session.commit()
        h.flash(_("Badge changed successfully"), 'success')
        redirect(self.base_url)

    @guard.instance.any_admin()
    @RequireInternalRequest()
    def update_thumbnail_badge(self, id):
        try:
            self.form_result = ThumbnailBadgeForm().to_python(request.params)
        except Invalid as i:
            return self.edit(id, i.unpack_errors())
        badge = self._get_badge_or_redirect(id)
        title, color, visible, description, impact, instance =\
            self._get_common_fields(self.form_result)
        thumbnail = self.form_result.get("thumbnail")
        if isinstance(thumbnail, FieldStorage):
            badge.thumbnail = thumbnail.file.read()
        if 'delete_thumbnail' in self.form_result:
            badge.thumbnail = None
        badge.title = title
        badge.color = color
        badge.visible = visible
        badge.description = description
        if badge.impact != impact:
            badge.impact = impact
            for delegateable in badge.delegateables:
                update_entity(delegateable, UPDATE)
        badge.instance = instance
        meta.Session.commit()
        h.flash(_("Badge changed successfully"), 'success')
        redirect(self.base_url)

    @guard.instance.any_admin()
    def ask_delete(self, id, format=u'html'):
        badge = self._get_badge_or_redirect(id)

        data = {
            'badge': badge,
            'badge_type': self._get_badge_type(badge),
            'badged_entities': badge.badged_entities(),
            'return_url': self.base_url,
        }

        return render('/badge/ask_delete.html', data,
                      overlay=format == u'overlay')

    @guard.instance.any_admin()
    @RequireInternalRequest()
    def delete(self, id):
        badge = self._get_badge_or_redirect(id)
        for badge_instance in badge.badges():
            meta.Session.delete(badge_instance)
            update_entity(badge_instance.badged_entity(), UPDATE)
        meta.Session.delete(badge)
        meta.Session.commit()
        h.flash(_(u"Badge deleted successfully"), 'success')
        redirect(self.base_url)
