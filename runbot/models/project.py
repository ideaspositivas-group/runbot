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
        if self.version_name == 'master':
            self.version_number = '~'
        else:
            # max version number with this format: 99.99
            self.version_number = '.'.join([elem.zfill(2) for elem in re.sub('[^0-9\.]', '', self.version_name).split('.')])

class ProjectCategory(models.Model):
    _name = 'runbot.project.category'
    _description = 'Category'

    name = fields.Char('Category name', required=True, unique=True, help="Name of the base branch")
    trigger_ids = fields.One2many('runbot.trigger', 'category_id', string='Triggers', required=True, unique=True, help="Name of the base branch")

class Project(models.Model):
    _name = "runbot.project"
    _description = "Project"

    name = fields.Char('Project name', required=True, unique=True, help="Name of the base branch")
    category_id = fields.Many2one('runbot.project.category')
    sticky = fields.Boolean(stored=True)
    is_base = fields.Boolean(compute='compute_is_base', stored=True)
    version_id = fields.Many2one('runbot.version', 'Version')
    no_build = fields.Boolean('No build')
    # version can change in case of retarget or manual operation from user


    #base_id = fields.Many2one('runbot.project', 'Base project', compute='_compute_closest_base' 
    #    help='A corresponding project that is a base, ususally a target, (master, or other version)')
    #forced_base_id = fields.Many2one('runbot.project', 'Forced base project')

    @api.model_create_single
    def create(self, values):
        ...

    def write(self, values):
        super().write(values)
        if 'is_base' in values:
            for project in self:
                self.env['runbot.project'].search([('name', '=like', '%s%%' % project.name)])._compute_closest_base()

    def _get(self, name, category_id):
        project = self.search([('name', '=', name), ('category_id', '=', category_id)])
        if not project:
            self.create({
                'name': name,
                'category_id': category_id,
            })
        return project

    @api.depends('is_base', 'forced_base', 'base_id.is_base')
    def _compute_closest_base(self):
        bases_by_category = {}
        for project in self:
            if self.is_base:
                return self
            category_id = project.category_id
            if category_id in bases_by_category:  # small perf imp for udge bartched
                base_projects = bases_by_category[category_id]
            else:
                base_projects = self.search([('is_base', '=', True), ('category_id', '=', category_id)])
                bases_by_category[category_id] = base_projects
            for candidate in base_projects:
                if project.name.startswith(candidate.name):
                    project.base_id = candidate
                    break
                elif project.name == 'master':
                    project.base_id = candidate


    def _get_preparing_instance(self):
        # find last project instance or create one
        preparing = self.env['runbot.instance'].search([('state', '=', 'preparing'), ('project_id', '=', self.id)])
        if not preparing:
            preparing = self.env['runbot.instance'].create({
                'last_update': fields.Datetime.Now(),
                'project_id': self,
                'state': 'creating',
            })
        return preparing

    def _target_changed(self):
        self.add_warning()

    def _last_succes(self):
        # search last project where all linked builds are success
        return None


class ProjectInstance(models.Model):
    _name = "runbot.instance"
    _description = "Project instance"
    _inherit = "mail.thread"

    last_update = fields.Datetime('Last ref update')
    project_id = fields.Many2one('runbot.project', required=True)
    project_commit_ids = fields.One2many('runbot.instance.commit', 'project_instance_id')
    builds = fields.Many2many('runbot.build')
    state = fields.Selection([('preparing', 'Preparing'), ('ready', 'Ready')])

    def _add_commit(self, commit):
        # if not the same hash for repo_group:
        self.last_update = fields.Datetime.now()

    def _start(self):
        # For all commit on real branches:
        for project_commit in self.project_commit_ids:
            triggers = self.env['runbot.trigger'].search([
                ('category_id', '=', self.project_id.category_id),
                ('repos_group_ids', 'in', project_commit.repo_group_id.id)])
            print('trigger', triggers)
            # todo execute triggers


class ProjectInstanceCommit(models.Model):
    _name = "runbot.instance.commit"
    _description = "Project instance commit"

    commit_id = fields.Many2one('runbot.commit')
    project_instance_id = fields.Many2one('runbot.instance')
    match_type = fields.Selection([('head', 'Head of branch'), ('default', 'Found on base branch')])  # HEAD, DEFAULT
    has_main = fields.Boolean('Commit already exists in another base project')  # a ref is pushed on another branch, don't build?


class ProjectInstanceBuild(models.Model):
    _name = 'runbot.instance.build'
    _description = 'Link between a project instance and a build'


    project_instance_id = fields.Many2one('runbot.instance')
    build_id = fields.Many2one('runbot.build')
    link_type = fields.Selection([('created', 'Build created'),('matched', 'Existing build matched')]) # rebuild type? 
    active = fields.Boolean('Attached')
    # rebuild, what to do: since build ccan be in multiple instance:
    # - replace for all instance?
    # - only available on instance and replace for instance only? 
    # - create a new project instance will new linked build?
