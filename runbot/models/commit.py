from odoo import models, fields

class Commit(models.Model):
    _name = "runbot.commit"
    _description = "Commit"

    name = fields.Char('SHA')
    repo_id = fields.Many2one('runbot.repo', string='Repo') # discovered in repo
    repo_group_id = fields.Many2one(related='repo_id.repo_group_id', stored=True) # discovered in repo
    date = fields.Datetime('Commit date')
    author = fields.Char('Author')
    author_email = fields.Char('Author Email')
    committer = fields.Char('Committer')
    committer_email = fields.Char('Committer Email')
    subject = fields.Text('Subject')

    def _source_path(self, *path):
        return self.repo._source_path(self.name, *path)

    def export(self):
        return self.repo._git_export(self.name)

    def read_source(self, file, mode='r'):
        file_path = self._source_path(file)
        try:
            with open(file_path, mode) as f:
                return f.read()
        except:
            return False

class RunbotBuildCommit(models.Model):
    _name = "runbot.build.commit"
    _description = "Build commit"

    params_id = fields.Many2one('runbot.build.params', 'Build', required=True, ondelete='cascade', index=True)
    commit_id = fields.Many2one('runbot.commit', 'Dependency commit', required=True)
    closest_branch_id = fields.Many2one('runbot.branch', 'Branch', ondelete='cascade')
    match_type = fields.Char('Match Type')

    def _get_repo(self):
        return self.closest_branch_id.repo_id or self.dependecy_repo_id

