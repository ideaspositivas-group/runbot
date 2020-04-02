import glob
import re
from odoo import models, fields, api

# Todo test: create will invalid branch name, pull request
# Todo test: test version number

# A project regroups different repo group.


class Project(models.Model):
    _name = 'runbot.commit'
    _description = 'Commit'
    _inherit = 'mail.thread'
