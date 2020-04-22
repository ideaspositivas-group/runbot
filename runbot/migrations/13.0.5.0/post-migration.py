# -*- coding: utf-8 -*-

from odoo.api import Environment
from odoo import SUPERUSER_ID
import logging
import progressbar
from collections import defaultdict

def _bar(total):
    b = progressbar.ProgressBar(maxval=total, \
        widgets=[progressbar.Bar('=', '[', ']'), ' ', progressbar.Percentage()])
    b.start()
    return b

_logger = logging.getLogger(__name__)

def migrate(cr, version):
    env = Environment(cr, SUPERUSER_ID, {})
    # some checks:
    for keyword in ('real_build', 'duplicate_id', '_get_all_commit'):
        matches = env['runbot.build.config.step'].search([('python_code', 'like', keyword)])
        if matches:
            _logger.warning('Some python steps found with %s ref: %s', keyword, matches)

    cr.execute('SELECT id FROM runbot_repo WHERE nginx = true')
    if cr.fetchone():
        cr.execute("""INSERT INTO ir_config_parameter (KEY, value) VALUES ('runbot_nginx', 'True')""")

    ########################
    # Repo groups, triggers and categories
    ########################

    repo_to_group = {}
    owner_group_to_repo = {}

    RD_category = env['runbot.project.category'].create({
        'name': 'R&D'
    })
    security_category = env['runbot.project.category'].create({
        'name': 'Security'
    })
    category_matching = { # some hardcoded info 
        'odoo': RD_category,
        'enterprise': RD_category,
        'upgrade': RD_category,
        'design-themes': RD_category,
        'odoo-security': security_category,
        'enterprise-security': security_category,
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
        owner = name.split(':')[-1].split('/')[0]
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
                    'name': repo_name,
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
        owner_group_to_repo[(owner, repo_to_group[id].id)] = id

    _logger.info('Creating triggers')
    processed = set()
    cr.execute("""
        SELECT 
        id, name, repo_config_id
        FROM runbot_repo order by id
    """)
    triggers = {}
    for id, name, repo_config_id in cr.fetchall():
        repo_name = name.split('/')[-1].replace('.git', '')
        cr.execute(""" SELECT dependency_id FROM runbot_repo_dep_rel WHERE dependant_id = %s""", (id,))
        dependency_ids = [r[0] for r in cr.fetchall()]
        group = repo_to_group[id]
        if group.id not in processed:
            processed.add(group.id)
            triggers[group.id] = env['runbot.trigger'].create({
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
    versions = {}
    branch_to_project = {}
    branch_to_version = {}
    progress = _bar(len(branches))
    for i, branch in enumerate(branches):
        progress.update(i)
        if branch.sticky and branch.branch_name not in versions:
            versions[branch.branch_name] = env['runbot.version'].create({
                'name': branch.branch_name,
            })
        group = branch.repo_id.repo_group_id
        if branch.target_branch_name and branch.pull_head_name:
            # 1. update source_repo: do not call github and use a naive approach:
            # pull_head_name contains odoo-dev and a repo in group starts with odoo-dev -> this is a known repo.
            owner = branch.pull_head_name.split(':')[0]
            pull_head_repo_id = owner_group_to_repo.get((owner, group.id))
            if pull_head_repo_id:
                branch.pull_head_repo_id = pull_head_repo_id
        category_id = group.default_category_id
        name = branch.reference_name

        key = (name, category_id)
        if key not in projects:
            project = env['runbot.project'].create({
                'name': name,
                'category_id': category_id.id,
                'sticky': branch.sticky,
                'is_base': branch.sticky,
                'version_id': next((version.id for k, version in versions.items() if (
                    k == branch.target_branch_name or \
                    branch.branch_name.startswith(k)
                )), next(version.id for k, version in versions.items() if k=='master'))
            })
            projects[key] = project
        project = projects[key]
        branch.project_id = project
        branch_to_project[branch.id] = project
        branch_to_version[branch.id] = project.version_id.id

    branches.flush()
    env['runbot.project'].flush()
    progress.finish()

    batch_size = 100000

    sha_commits = {}
    sha_repo_commits = {}
    branch_heads = {}
    build_commit_ids = defaultdict(dict)
    cr.execute("SELECT count(*) FROM runbot_build WHERE duplicate_id IS NULL")
    nb_real_build = cr.fetchone()[0]

    # create params from build
    _logger.info('Creating params and commits')
    counter = 0
    progress = _bar(nb_real_build)
    for offset in range(0, nb_real_build, batch_size):
        cr.execute("""
            SELECT
            id, branch_id, repo_id, extra_params, config_id, config_data, commit_path_mode,
            name, author, author_email, committer, committer_email, subject, date
            FROM runbot_build WHERE duplicate_id IS NULL ORDER BY id asc LIMIT %s OFFSET %s""", (batch_size, offset))

        for id, branch_id, repo_id, extra_params, config_id, config_data, commit_path_mode,\
            name, author, author_email, committer, committer_email, subject, date in cr.fetchall():
            progress.update(counter)

            key = (name, repo_id)
            if key in sha_repo_commits:
                commit = sha_repo_commits[key]
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
                sha_repo_commits[key] = commit
                sha_commits[name] = commit

                # setting head if it is a new commit, should be ok since in chronological order. if not, check type and parent_id
                # TODO: check that it is corresct or scheduler will explode
            branch_heads[branch_id] = commit.id
            counter += 1
            params = env['runbot.build.params'].create({
                'version_id':  branch_to_version[branch_id],
                'extra_params': extra_params,
                'config_id': config_id,
                'config_data': config_data,
                'commit_path_mode':commit_path_mode,
            })
            env['runbot.build.commit'].create({
                'commit_id': commit.id,
                'params_id': params.id,
            })
            build_commit_ids[id][commit.repo_group_id.id] = (name, commit.id)
            cr.execute('UPDATE runbot_build_commit SET params_id=%s WHERE build_id=%s', (params.id, id))
            # todo set params on duplicate?
            cr.execute('UPDATE runbot_build SET params_id=%s WHERE id=%s', (params.id, id))
            # TODO deps from logs?
        env.cache.invalidate()
    progress.finish()


    for branch, head in branch_heads.items():
        cr.execute('UPDATE runbot_branch SET head=%s WHERE id=%s', (head, branch))
    del branch_heads
    # adapt build commits


    _logger.info('Updating build commits')
    cr.execute("SELECT count(*) FROM runbot_build_commit")
    nb_build_commit = cr.fetchone()[0]
    counter = 0

    progress = _bar(nb_build_commit)
    for offset in range(0, nb_build_commit, batch_size):
        cr.execute('SELECT id, dependency_hash, dependecy_repo_id, build_id from runbot_build_commit WHERE build_id is not NULL LIMIT %s OFFSET %s', (batch_size, offset))
        # TODO unique by hash repo and update
        for id, dependency_hash, dependency_repo_id, build_id in cr.fetchall():
            progress.update(counter)
            counter += 1
            key = (dependency_hash, dependency_repo_id)
            commit = sha_repo_commits.get(key) or sha_commits.get(dependency_hash) # TODO check this (changing repo)
            if not commit:
                # -> most of the time, commit in exists but with wrong repo. Info can be found on other commit.
                _logger.warning('Missing commit %s created', dependency_hash)
                commit = env['runbot.commit'].create({
                    'name': dependency_hash,
                    'repo_id': dependency_repo_id,
                })

                build_commit_ids[build_id][commit.repo_group_id.id] = (dependency_hash, commit.id)
                sha_repo_commits[key] = commit
                sha_commits[dependency_hash] = commit

            cr.execute('UPDATE runbot_build_commit SET commit_id=%s WHERE id=%s', (commit.id, id))
    progress.finish()

    _logger.info('Creating instances')
    ###################
    # Project instance
    ####################
    cr.execute("SELECT count(*) FROM runbot_build WHERE parent_id IS NOT NULL")
    nb_root_build = cr.fetchone()[0]
    counter = 0
    progress = _bar(nb_root_build)
    for offset in range(0, nb_root_build, batch_size):
        cr.execute("""
            SELECT
            id, duplicate_id, repo_id, branch_id, create_date, build_type
            FROM runbot_build WHERE parent_id IS NULL order by id asc
            LIMIT %s OFFSET %s""", (batch_size, offset))
        for id, duplicate_id, repo_id, branch_id, create_date, build_type in cr.fetchall():
            progress.update(counter)
            counter += 1
            if repo_id is None:
                _logger.warning('Skipping %s: no repo', id)
                continue
            project = branch_to_project[branch_id]
            # try to merge build in same instance
            # not temporal notion in this case, only hash consistency
            instance = False
            build_id = duplicate_id or id
            build_commits = build_commit_ids[build_id]
            instance_group_repos_ids = []
            if project.last_instance:
                if duplicate_id and build_id in project.last_instance.slot_ids.mapped('build_id').ids:
                    continue

                # to fix: nightly will be in the same instance of the previous normal one. If config_id is diffrent, create instance?

                instance = project.last_instance
                instance_repos_groups = []
                instance_commits = instance.project_commit_ids.mapped('commit_id')
                instance_group_repos_ids = instance_commits.mapped('repo_group_id').ids
                for commit in instance_commits:
                    repo_group_id = commit.repo_group_id.id
                    if repo_group_id in build_commits:
                        instance_commit_name, _ = build_commits[repo_group_id]
                        if instance_commit_name != commit.name:
                            instance = False
                            instance_group_repos_ids = []
                            break

            missing_commits = [commit_id for repo_group_id, (_, commit_id) in build_commits.items() if repo_group_id not in instance_group_repos_ids]
            if not instance:
                instance = env['runbot.instance'].create({
                    'last_update': create_date,
                    'state': 'ready',
                    'project_id': project.id
                })
                project.last_instance = instance
            instance_slot = env['runbot.instance.slot'].create({
                'trigger_id': triggers[repo_to_group[repo_id].id].id,
                'instance_id': instance.id,
                'build_id': build_id,
                'link_type': 'rebuild' if build_type == 'rebuild' else 'matched' if duplicate_id else 'created',
                'active': True,
            })
            for missing_commit in missing_commits: # todo improve this, need time to prefetch params + commits
                env['runbot.instance.commit'].create({
                    'commit_id': missing_commit,
                    'instance_id': instance.id,
                    'match_type': 'head', # TODO fixme
                    #'has_main' = True, ?
                })

        env.cache.invalidate()
    progress.finish()

    #Build of type rebuild may point to same params as rebbuild?

    ###################
    # Cleaning (performances)
    ###################
    # 1. avoid UPDATE "runbot_build" SET "commit_path_mode"=NULL WHERE "commit_path_mode"='soft'

    _logger.info('Pre-cleaning')
    cr.execute('alter table runbot_build alter column commit_path_mode drop not null')
    cr.execute('ANALYZE')
    cr.execute("delete from runbot_build where local_state='duplicate'") # what about duplicate childrens?
    _logger.info('End')

    # todo rename folders from dest to id.
