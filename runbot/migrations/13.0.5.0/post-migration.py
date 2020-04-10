# -*- coding: utf-8 -*-

from odoo.api import Environment
from odoo import SUPERUSER_ID
import logging

_logger = logging.getLogger(__name__)

def migrate(cr, version):
    env = Environment(cr, SUPERUSER_ID, {})
    # some checks:
    for keyword in ('real_build', 'duplicate_id', '_get_all_commit'):
        matches = env['runbot.build.config.step'].search([('python_code', 'like', keyword)])
        if matches:
            _logger.warning('Some python steps found with %s ref: %s', keyword, matches)

    _logger.info('Checking nginx')
    cr.execute('SELECT id FROM runbot_repo WHERE nginx = true')
    if cr.fetchone():
        cr.execute("""INSERT INTO ir_config_parameter (KEY, value) VALUES ('runbot_nginx', 'True')""")

    ########################
    # Repo groups, triggers and categories
    ########################

    repo_to_group = {}
    group_to_category = {}


    RD_category = env['runbot.project.category'].create({
        'name': 'R&D'
    })
    category_matching = {
        'odoo': RD_category,
        'enterprise': RD_category,
        'upgrade': RD_category,
        'design-theme': RD_category,
    }
    cr.execute("""
        SELECT 
        id, name, duplicate_id, modules, modules_auto, server_files, manifest_files, addons_paths
        FROM runbot_repo order by id
    """)
    for id, name, duplicate_id, modules, modules_auto, server_files, manifest_files, addons_paths in cr.fetchall():
        cr.execute(""" SELECT res_groups_id FROM res_groups_runbot_repo_rel WHERE runbot_repo_id = %s""", (id,))
        group_ids = [r[0] for r in cr.fetchall()]
        repo_name = name.split('/')[-1].replace('.git', '')

        repo = env['runbot.repo'].browse(id)
        if duplicate_id in repo_to_group:
            repo.repo_group_id = repo_to_group[duplicate_id]
            repo_to_group[id] = repo_to_group[duplicate_id]
            # todo make some checks ?
        else:
            # if not, we need to give information on how to group repos: odoo+enterprise+upgarde+design-theme/se/runbot
            # this mean that we will need to group build too. Could be nice but maybe a little difficult.
            if repo_name in category_matching:
                category = category_matching[repo_name]
            else:
                category = env['runbot.project.category'].create({
                    'name': 'R&D'
                })
            group = env['runbot.repo.group'].create({
                'name': repo_name,
                'default_category_id': category.id,
                #'main': id, # older repo should be the main, not sur it is usefull
                'modules': modules,
                'modules_auto': modules_auto,
                'group_ids': [(4, group_id) for group_id in group_ids],
                'server_files': server_files,
                'manifest_files': manifest_files,
                'addons_paths': addons_paths,
            })
            repo.repo_group_id = group
            repo_to_group[id] = group

    _logger.info('Creating triggers')
    processed = set()
    cr.execute("""
        SELECT 
        id, name, repo_config_id
        FROM runbot_repo order by id
    """)
    for id, name, repo_config_id in cr.fetchall():
        repo_name = name.split('/')[-1].replace('.git', '')
        cr.execute(""" SELECT dependency_id FROM runbot_repo_dep_rel WHERE dependant_id = %s""", (id,))
        dependency_ids = [r[0] for r in cr.fetchall()]
        group = repo_to_group[id]
        if group.id not in processed:
            processed.add(group.id)
            env['runbot.trigger'].create({
                'name': repo_name,
                'category_id': group.default_category_id.id,
                'repos_group_ids': [(4, group.id)],
                'dependency_ids': [(4, repo_to_group[dependency_id].id) for dependency_id in dependency_ids],
                'config_id': repo_config_id if repo_config_id else env.ref('runbot.runbot_build_config_default').id,
            })
        # TODO create trigger using dependency_ids

    # no build, config, ...




    ########################
    # Projects
    ########################
    _logger.info('Creating projects')

    branches = env['runbot.branch'].search([], order='id')

    branches._compute_reference_name()

    projects = {}
    for branch in branches:
        category_id = branch.repo_id.repo_group_id.default_category_id
        name = branch.reference_name
        key = (name, category_id)
        if key not in projects:
            projects[key] = env['runbot.project'].create({
                'name': name,
                'category_id': category_id,
            })
        branch.project_id = projects[key]

    ########################
    # build and commits
    ########################
    _logger.info('Creating commits')
    cr.execute("""
        SELECT 
        id, name, branch_id, repo_id, 
        author, author_email, committer, committer_email, subject, date 
        FROM runbot_build order by id asc
        """)
    nb_build = cr.rowcount
    sha_commits = {}
    progress = int(nb_build/100)

    for counter, (id, name, branch_id, repo_id, author, author_email, committer, committer_email, subject, date) in enumerate(cr.fetchall()): 
        if counter % progress == 0:
            _logger.info('%s%%', int(counter/progress))
        key = (name, repo_id)
        if key in sha_commits:
            commit = sha_commits[key]
        else:
            commit = env['runbot.commit'].create({
                'name': name,
                'repo_id': repo_id,
                'author': author,
                'author_email': author_email,
                'committer': committer,
                'committer_email': committer_email,
                'subject': subject,
                'date': date
            })
            sha_commits[key] = commit
            # setting head if it is a new commit, should be ok since in chronological order. if not, check type and parent_id
            # TODO: check that it is corresct or scheduler will explode
            env['runbot.branch'].browse(branch_id).head = commit

    _logger.info('Updating dependencies')
    cr.execute("""
        SELECT 
        id, dependency_hash, dependecy_repo_id
        FROM runbot_build_commit order by id asc
        """)

    _logger.info('Creating params')
    cr.execute("""
        SELECT 
        id, name, branch_id, repo_id, 
        author, author_email, committer, committer_email, subject, date 
        FROM runbot_build order by id asc
        """)
    for id in cr.fetchall():
        env['runbot.build.params'].create({

        })
        # QUESTION: is param unique? can a child have another param? or modifiable
        #-> for migration, need to change params (commits, extra_params, ....)
        #-> yes params can change in subbuild?


    _logger.info('Creating instances')
    #roject_id from branch
    # temporary hack
    ###################
    # Project instance
    ####################
    cr.execute("""
        SELECT 
        id, local_state, project_id
        author, author_email, committer, committer_email, subject, date 
        FROM runbot_build WHERE parent_id IS NOT SET order by id asc
        """)
    for counter, (id, name, branch_id, repo_id, author, author_email, committer, committer_email, subject, date) in enumerate(cr.fetchall()): 
        if counter % progress == 0:
            _logger.info('%s%%', int(counter/progress))
        # how to link build and project instance? 
        #depending on triggers:
        # first naive solution: one instance per build
        # then, merge close instance in same project
        instance = env['runbot.instance'].create({

        })
        env['runbot.instance.build'].create({
            'project_instance_id': instance.id,
            'build_id' = duplicate_id or id,
            'link_type' = 'matched' if duplicate_id else 'created',
            'active' = True,
        })
        for commit in build.params_id.commits:
            env['runbot.instance.commit'].create({
                'commit_id': commit.id,
                'project_instance_id' = duplicate_id or id,
                'match_type' = 'head', # TODO fixme
                #'has_main' = True, ?
            })

        commit_id = fields.Many2one('runbot.commit')
        project_instance_id = fields.Many2one('runbot.instance')
        match_type = fields.Selection([('head', 'Head of branch'), ('default', 'Found on base branch')])  # HEAD, DEFAULT


    cr.execute("delete from runbot_build where local_state='duplicate')



    # manage duplicate= thet should be a link in project
    # dependency to commit
    # split params? why again? usefull for rebuild, and matching same build on



    #split result and build
    #Build of type rebuild may point to same params as rebbuild?


    ###################
    # Cleaning (performances)
    ###################
    # 1. avoid UPDATE "runbot_build" SET "commit_path_mode"=NULL WHERE "commit_path_mode"='soft'
    cr.execute("ALTER TABLE runbot_build DROP COLUMN commit_path_mode")
    field = env['ir.model.fields'].search([('name', '=', 'commit_path_mode')])
    field_selection = env['ir.model.fields.selection'].search([('field_id', '=', field.id)])

    super(field_selection).unlink
    # delete corresponding fields