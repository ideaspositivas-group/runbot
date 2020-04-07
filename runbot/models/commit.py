from odoo import models, fields

class Commit(models.Model):
    _name = "runbot.commit"
    _description = "Commit"

    sha = fields.Char()
    repo_id = fields.Many2one('runbot.repo', string='Repo') # discovered in repo
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
