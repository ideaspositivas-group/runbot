# -*- coding: utf-8 -*-
import operator
import werkzeug
from collections import OrderedDict

import werkzeug.utils
import werkzeug.urls

from odoo.addons.http_routing.models.ir_http import slug
from odoo.addons.website.controllers.main import QueryURL

from odoo.http import Controller, request, route
from ..common import uniq_list, flatten, fqdn
from odoo.osv import expression

from odoo.exceptions import UserError

class Runbot(Controller):

    def _pending(self):
        ICP = request.env['ir.config_parameter'].sudo().get_param
        warn = int(ICP('runbot.pending.warning', 5))
        crit = int(ICP('runbot.pending.critical', 12))
        pending_count = request.env['runbot.build'].search_count([('local_state', '=', 'pending'), ('build_type', '!=', 'scheduled')])
        scheduled_count = request.env['runbot.build'].search_count([('local_state', '=', 'pending'), ('build_type', '=', 'scheduled')])
        level = ['info', 'warning', 'danger'][int(pending_count > warn) + int(pending_count > crit)]
        return pending_count, level, scheduled_count

    @route(['/runbot', '/runbot/projects/<model("runbot.project.category"):category>'], website=True, auth='public', type='http')
    def projects(self, category=None, search='', refresh='', **kwargs):
        search = search if len(search) < 60 else search[:60]
        env = request.env
        categories = env['runbot.project.category'].search([])
        if not category and categories:
            category = categories[0]

        pending_count, level, scheduled_count = self._pending()
        context = {
            'categories': categories,
            'category': category,
            'search': search,
            'refresh': refresh,
            'message': request.env['ir.config_parameter'].sudo().get_param('runbot.runbot_message'),
            'pending_total': pending_count,
            'pending_level': level,
            'scheduled_count': scheduled_count,
            'hosts_data': request.env['runbot.host'].search([]),
        }

        if category:
            # basic search to start, only project name. TODO add instance.commits and project pr numbers (all branches names)

            domain = [('category_id', '=', category.id)]
            if search:
                for search_elem in search.split("|"):
                    domain = expression.OR(domain, [('name', 'like', search_elem)])

            env.cr.execute("""
                SELECT id FROM runbot_project
                WHERE last_instance is not null
                ORDER BY
                    sticky desc,
                    case when sticky then version_number end collate "C" desc,
                    case when not sticky then last_instance end desc
                LIMIT 100""")
            # TODO check if where clausse is usefull on complete database
            projects = env['runbot.project'].browse([r[0] for r in env.cr.fetchall()])

            context.update({
                'projects': projects,
                'qu': QueryURL('/runbot/projects/' + slug(category), search=search, refresh=refresh),
            })

        context.update({'message': request.env['ir.config_parameter'].sudo().get_param('runbot.runbot_message')})
        return request.render('runbot.projects', context)


    @route(['/runbot/project/<model("runbot.project"):project>'], website=True, auth='public', type='http')
    def project(self, project=None, page=1, limit=50, more=False, refresh='', **kwargs):
        print('here')
        env = request.env
        domain =[('project_id','=',project.id), ('hidden', '=', False)]
        instance_count = request.env['runbot.instance'].search_count(domain)
        pager = request.website.pager(
            url='/runbot/project/%s' % project.id,
            total=instance_count,
            page=page,
            step=50,
        )
        instances = request.env['runbot.instance'].search(domain, limit=limit, offset=pager.get('offset',0))

        context = {'project': project, 'instances': instances, 'pager': pager, 'more': more}

        return request.render('runbot.project', context)


    @route(['/runbot/instance/<model("runbot.project.instance"):instance>'], website=True, auth='public', type='http')
    def project(self, project=None, page=1, limit=50, more=False, refresh='', **kwargs):
        context = {'instance': instance, 'pager': pager, 'more': more}
        return request.render('runbot.instance', context)


    @route(['/runbot/repo/<model("runbot.repo"):repo>'], website=True, auth='public', type='http')
    def repo(self, repo=None, search='', refresh='', **kwargs):
        search = search if len(search) < 60 else search[:60]

        triggers = request.env['runbot.trigger'].search([])
        if not trigger and triggers:
            repo = repos[0].id

        pending = self._pending()
        context = {
            'repos': repos.ids,
            'repo': repo,
            'host_stats': [],
            'pending_total': pending[0],
            'pending_level': pending[1],
            'scheduled_count': pending[2],
            'search': search,
            'refresh': refresh,
            'hosts_data': request.env['runbot.host'].search([]),
        }

        build_ids = []
        if repo:
            domain = [('repo_id', '=', repo.id)]
            if search:
                search_domain = []
                for to_search in search.split("|"):
                    search_domain = ['|', '|', '|'] + search_domain
                    search_domain += [('dest', 'ilike', to_search), ('subject', 'ilike', to_search), ('branch_id.branch_name', 'ilike', to_search)]
                domain += search_domain[1:]
            domain = expression.AND([domain, [('hidden', '=', False)]]) # don't display children builds on repo view
            build_ids = build_obj.search(domain, limit=100)
            branch_ids, build_by_branch_ids = [], {}

            if build_ids:
                branch_query = """
                SELECT br.id FROM runbot_branch br INNER JOIN runbot_build bu ON br.id=bu.branch_id WHERE bu.id in %s
                ORDER BY bu.sequence DESC
                """
                sticky_dom = [('repo_id', '=', repo.id), ('sticky', '=', True)]
                sticky_branch_ids = [] if search else branch_obj.search(sticky_dom).sorted(key=lambda b: (b.branch_name == 'master', b.id), reverse=True).ids
                request._cr.execute(branch_query, (tuple(build_ids.ids),))
                branch_ids = uniq_list(sticky_branch_ids + [br[0] for br in request._cr.fetchall()])

                build_query = """
                    SELECT 
                        branch_id, 
                        max(case when br_bu.row = 1 then br_bu.build_id end),
                        max(case when br_bu.row = 2 then br_bu.build_id end),
                        max(case when br_bu.row = 3 then br_bu.build_id end),
                        max(case when br_bu.row = 4 then br_bu.build_id end)
                    FROM (
                        SELECT 
                            br.id AS branch_id, 
                            bu.id AS build_id,
                            row_number() OVER (PARTITION BY branch_id) AS row
                        FROM 
                            runbot_branch br INNER JOIN runbot_build bu ON br.id=bu.branch_id 
                        WHERE 
                            br.id in %s AND (bu.hidden = 'f' OR bu.hidden IS NULL)
                        GROUP BY br.id, bu.id
                        ORDER BY br.id, bu.id DESC
                    ) AS br_bu
                    WHERE
                        row <= 4
                    GROUP BY br_bu.branch_id;
                """
                request._cr.execute(build_query, (tuple(branch_ids),))
                build_by_branch_ids = {
                    rec[0]: [r for r in rec[1:] if r is not None] for rec in request._cr.fetchall()
                }

            branches = branch_obj.browse(branch_ids)
            build_ids = flatten(build_by_branch_ids.values())
            build_dict = {build.id: build for build in build_obj.browse(build_ids)}

            def branch_info(branch):
                return {
                    'branch': branch,
                    'builds': [build_dict[build_id] for build_id in build_by_branch_ids.get(branch.id) or []]
                }

            context.update({
                'branches': [branch_info(b) for b in branches],
                'qu': QueryURL('/runbot/repo/' + slug(repo), search=search, refresh=refresh),
                'fqdn': fqdn(),
            })

        # consider host gone if no build in last 100
        build_threshold = max(build_ids or [0]) - 100

        context.update({'message': request.env['ir.config_parameter'].sudo().get_param('runbot.runbot_message')})
        return request.render('runbot.repo', context)

    @route([
        '/runbot/build/<int:build_id>/<operation>',
        '/runbot/build/<int:build_id>/<operation>/<int:exact>',
    ], type='http', auth="public", methods=['POST'], csrf=False)
    def build_force(self, build_id, operation, exact=0, search=None, **post):
        build = request.env['runbot.build'].sudo().browse(build_id)
        if operation == 'force':
            build = build._force(exact=bool(exact))
        elif operation == 'kill':
            build._ask_kill()
        elif operation == 'wakeup':
            build._wake_up()

        qs = ''
        if search:
            qs = '?' + werkzeug.urls.url_encode({'search': search})
        return werkzeug.utils.redirect(build.build_url + qs)

    @route(['/runbot/build/<int:build_id>'], type='http', auth="public", website=True)
    def build(self, build_id, search=None, **post):
        """Events/Logs"""

        Build = request.env['runbot.build']
        Logging = request.env['ir.logging']

        build = Build.browse([build_id])[0]
        if not build.exists():
            return request.not_found()

        #show_rebuild_button = Build.search([('branch_id', '=', build.branch_id.id), ('parent_id', '=', False)], limit=1) == build

        context = {
            'build': build,
            'fqdn': fqdn(),
        }
        return request.render("runbot.build", context)

    @route(['/runbot/quick_connect/<model("runbot.branch"):branch>'], type='http', auth="public", website=True)
    def fast_launch(self, branch, **post):
        """Connect to the running Odoo instance"""
        Build = request.env['runbot.build']
        domain = [('branch_id', '=', branch.id), ('config_id', '=', branch.config_id.id)]

        # Take the 10 lasts builds to find at least 1 running... Else no luck
        builds = Build.search(domain, order='sequence desc', limit=10)

        if builds:
            last_build = False
            for build in builds:
                if build.local_state == 'running':
                    last_build = build
                    break

            if not last_build:
                # Find the last build regardless the state to propose a rebuild
                last_build = builds[0]

            if last_build.local_state != 'running':
                url = "/runbot/build/%s?ask_rebuild=1" % last_build.id
            else:
                url = "http://%s/web/login?db=%s-all&login=admin&redirect=/web?debug=1" % (last_build.domain, last_build.dest)
        else:
            return request.not_found()
        return werkzeug.utils.redirect(url)

    def _glances_ctx(self):
        repos = request.env['runbot.repo'].search([])   # respect record rules
        default_config_id = request.env.ref('runbot.runbot_build_config_default').id
        query = """
            SELECT split_part(r.name, ':', 2),
                   br.branch_name,
                   (array_agg(bu.global_result order by bu.id desc))[1]
              FROM runbot_build bu
              JOIN runbot_branch br on (br.id = bu.branch_id)
              JOIN runbot_repo r on (r.id = br.repo_id)
             WHERE br.sticky
               AND br.repo_id in %s
               AND (bu.hidden = 'f' OR bu.hidden IS NULL)
               AND (
                    bu.global_state in ('running', 'done')
               )
               AND bu.global_result not in ('skipped', 'manually_killed')
               AND (bu.config_id = r.repo_config_id
                    OR bu.config_id =  br.branch_config_id
                    OR bu.config_id =  %s)
          GROUP BY 1,2,r.sequence,br.id
          ORDER BY r.sequence, (br.branch_name='master'), br.id
        """
        cr = request.env.cr
        cr.execute(query, (tuple(repos.ids), default_config_id))
        ctx = OrderedDict()
        for row in cr.fetchall():
            ctx.setdefault(row[0], []).append(row[1:])
        return ctx

    @route('/runbot/glances', type='http', auth='public', website=True)
    def glances(self, refresh=None):
        glances_ctx = self._glances_ctx()
        pending = self._pending()
        qctx = {
            'refresh': refresh,
            'pending_total': pending[0],
            'pending_level': pending[1],
            'glances_data': glances_ctx,
        }
        return request.render("runbot.glances", qctx)

    @route(['/runbot/monitoring',
            '/runbot/monitoring/<int:config_id>',
            '/runbot/monitoring/<int:config_id>/<int:view_id>'], type='http', auth='user', website=True)
    def monitoring(self, config_id=None, view_id=None, refresh=None, **kwargs):
        glances_ctx = self._glances_ctx()
        pending = self._pending()
        hosts_data = request.env['runbot.host'].search([])

        last_monitored = None

        monitored_config_id = config_id or int(request.env['ir.config_parameter'].sudo().get_param('runbot.monitored_config_id', 1))
        request.env.cr.execute("""SELECT DISTINCT ON (branch_id) branch_id, id FROM runbot_build
                                WHERE config_id = %s
                                AND global_state in ('running', 'done')
                                AND branch_id in (SELECT id FROM runbot_branch where sticky='t')
                                AND local_state != 'duplicate'
                                AND hidden = false
                                ORDER BY branch_id ASC, id DESC""", [int(monitored_config_id)])
        last_monitored = request.env['runbot.build'].browse([r[1] for r in request.env.cr.fetchall()])

        config = request.env['runbot.build.config'].browse(monitored_config_id)
        qctx = {
            'config': config,
            'refresh': refresh,
            'pending_total': pending[0],
            'pending_level': pending[1],
            'scheduled_count': pending[2],
            'glances_data': glances_ctx,
            'hosts_data': hosts_data,
            'last_monitored': last_monitored,  # nightly
            'auto_tags': request.env['runbot.build.error'].disabling_tags(),
            'build_errors': request.env['runbot.build.error'].search([('random', '=', True)]),
            'kwargs': kwargs
        }
        return request.render(view_id if view_id else config.monitoring_view_id.id or "runbot.monitoring", qctx)

    @route(['/runbot/config/<int:config_id>',
            '/runbot/config/<config_name>'], type='http', auth="public", website=True)
    def config(self, config_id=None, config_name=None, refresh=None, **kwargs):

        if config_id:
            monitored_config_id = config_id
        else:
            config = request.env['runbot.build.config'].search([('name', '=', config_name)], limit=1)
            if config:
                monitored_config_id = config.id
            else:
                raise UserError('Config name not found')

        readable_repos = request.env['runbot.repo'].search([])
        request.env.cr.execute("""SELECT DISTINCT ON (branch_id) branch_id, id FROM runbot_build
                                WHERE config_id = %s
                                AND global_state in ('running', 'done')
                                AND branch_id in (SELECT id FROM runbot_branch where sticky='t' and repo_id in %s)
                                AND local_state != 'duplicate'
                                AND hidden = false
                                ORDER BY branch_id ASC, id DESC""", [int(monitored_config_id), tuple(readable_repos.ids)])
        last_monitored = request.env['runbot.build'].browse([r[1] for r in request.env.cr.fetchall()])

        config = request.env['runbot.build.config'].browse(monitored_config_id)
        qctx = {
            'config': config,
            'refresh': refresh,
            'last_monitored': last_monitored,  # nightly
            'kwargs': kwargs
        }
        return request.render(config.monitoring_view_id.id or "runbot.config_monitoring", qctx)
