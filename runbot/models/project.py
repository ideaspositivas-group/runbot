import glob
import re
from odoo import models, fields, api

#Todo test: create will invalid branch name, pull request


class Version(models.Model):
    _name = "runbot.version"
    _description = "Version"

    name = fields.Char('Version name')
    number = fields.Char('Comparable version number', compute='_compute_version_number', stored=True)

    @api.depends('version_name')
    def compute_version_number(self):
        if version_name == 'master':
            self.version_number = '~'
        else:
            # max version number with this format: 99.99
            self.version_number = '.'.join([elem.zfill(2) for elem in re.sub('[^0-9\.]', '', self.version_name).split('.')])

class ProjectCategory(models.Model):
    _name = 'runbot.project.category'
    name = fields.Char('Project group name', required=True, unique=True, help="Name of the base branch")
    trigger_ids = fields.One2Many('Project group name', required=True, unique=True, help="Name of the base branch")

class Project(models.Model):
    _name = "runbot.project"
    _description = "Project"

    name = fields.Char('Project name', required=True, unique=True, help="Name of the base branch")
    project_category_id = fields.Many2one('runbot.project.category')
    sticky = fields.Boolean(stored=True)
    is_base = fields.Boolean(compute='compute_is_base', stored=True)
    base_id = fields.Many2one('runbot.project', 'Base project',
        help='A corresponding project that is a base, ususally a target, (master, or other version)')
    version_id = fields.Many2one('runbot.version', 'Version')
    # version can change in case of retarget or manual operation from user

    @api.model_create_single
    def create(self, values):
        ...

    def _name_from_branch(self, branch):
        repo_id = branch.repo_id
        if branch.target_branch_name and branch.pull_head_name:  # odoo:master-remove-duplicate-idx, davidtranhp:xxx, 
            version_str = branch.target_branch_name
            owner, name = branch.pull_head_name.split(':')  # TODO fix where pullheadname doesnt have : -> old branch, redo get_pull_info
            repo_group = repo_id.repo_group
            source_repo = branch.pull_head_repo_id
            if source_repo:
                assert source_repo in repo_group.repos # should be in repo list
            else:
                name = branch.pull_head_name  # repo is not known, not in repo list must be an external pr, so use complete label
        else:
            name = branch.branch_name
        return name
        # cases to test:
        # organisation:patch-x (no pull_head_name, should be changed)
        # odoo-dev:master-my-dev
        # odoo-dev:dummy-my-dev -> warning
        # odoo:master-my-dev
        # odoo:master-my-dev
        # odoo:master-my-dev + odoo-dev:master-my-dev
        # -> convention in odoo, this is an error. A branch_name should be unique
        # pr targetting odoo-dev
        #
        # a pr pull head name should be in a repo or one of its forks, we need to check that

    def _from_branch(self, branch):
        name = self._name_from_branch(branch)
        project_category_id = branch.repo_id.repo_group.default_project_category_id
        project = self.search([('name', '=', name), ('project_category_id', '=', project_category_id)])
        if not project:
            self.create({
                'name': name,
                'project_category': project_category_id,
                'sticky': branch.sticky # NOT A GOOD IDEA, TODO REMOVE STICKY ON BRANCH, False by default
                'base_id': self._get_closest_base()
            })
        return project

    def _get_closest_base(self):

    def _get_preparing_instance(self, co):
        # find last project instance or create one
        preparing = self.env['runbot.project.instance'].search([('state', '=', 'preparing'), ('project_id', '=', self.id)])
        if not preparing:
            preparing = self.env['runbot.project.instance'].create({
                'last_update': fields.Datetime.Now(),
                'project_id': self,
                'state': 'creating'
            })
        return preparing

    def _target_changed(self)
        self.add_warning

    def _last_succes(self):
        # search last project where all linked builds are success
        return None


class ProjectBuild(models.Model):
    _name = "runbot.project.instance"
    _description = "Project instance"
    _inherit = "mail.thread"

    last_update = fields.Datetime('Last ref update')
    project_id = fields.Many2One('runbot.project', required=True)
    project_commit_ids = fields.One2Many('runbot.project.commit', 'project_instance_id')
    builds = fields.Many2Many('runbot.build')
    state = fields.Selection([('preparing', 'Preparing'), ('ready', 'Ready')])

    def _add_commit(self, commit):
        # if not the same hash for repo_group:
        self.last_update = fields.Datetime.now()

    def _start(self):
        # For all commit on real branches:
        for project_commit in self.project_commit_ids:
            group = project_commit.repo_group
            triggers = self.env['repo.trigger'].search([('project_category_id', '=', self.project_category_id), ('trigger_repos', 'in', commit.repo_group_id)])

class ProjectBuildCommit(models.Model):
    _name = "runbot.project.instance.commit"
    _description = "Project instance commit"

    commit_id = 
    project_instance_id = 
    repo_group_id = 


class Commit(models.Model):
    _name = "runbot.commit"
    _description = "Commit"

    sha = fields.Char()
    repo_id = fields.Many2One('')
    date = fields.Datetime('Commit date')
    author = fields.Char('Author')
    author_email = fields.Char('Author Email')
    committer = fields.Char('Committer')
    committer_email = fields.Char('Committer Email')
    subject = fields.Text('Subject')

    def _source_path(self, *path):
        return self.repo._source_path(self.sha, *path)

    def export(self):
        return self.repo._git_export(self.sha)

    def read_source(self, file, mode='r'):
        file_path = self._source_path(file)
        try:
            with open(file_path, mode) as f:
                return f.read()
        except:
            return False

    def __str__(self):
        return '%s:%s' % (self.repo.short_name, self.sha)
