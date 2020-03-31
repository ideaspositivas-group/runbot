# -*- encoding: utf-8 -*-

from pathlib import Path
import subprocess

from odoo import models, fields


class Step(models.Model):
    _inherit = "runbot.build.config.step"

    job_type = fields.Selection(selection_add=[("pre-commit", "pre-commit check")])

    def _run_step(self, build, log_path):
        if self.job_type == "pre-commit":
            return self._runbot_pre_commit_check(build, log_path)
        return super(Step, self)._run_step(build, log_path)

    def _runbot_pre_commit_check(self, build, log_path):
        pi = build.branch_id._get_pull_info()
        if pi.get("state") != "open":
            return -2

        build._checkout()
        repo = build.repo_id

        src_path = Path(repo._source_path(build.name))
        cfg = src_path / ".pre-commit-config.yaml"
        if cfg.is_file():
            # get common ancestor
            base = repo._git(
                ["merge-base", f"refs/heads/{pi['base']['ref']}", build.branch_id.name]
            )
            p = subprocess.run(
                [
                    "pre-commit",
                    "run",
                    "--from-ref",
                    base,
                    "--to-ref",
                    "HEAD",
                    "--show-diff-on-failure",
                ],
                cwd=str(src_path),
                env={"GIT_DIR": repo.path},
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            if p.returncode == 0:
                state = "success"
            else:
                state = "failure"
                build._log("pre-commit", p.stdout)

            subprocess.run(
                ["git", "reset", "--hard", build.name],
                cwd=str(src_path),
                env={"GIT_DIR": repo.path},
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            runbot_domain = self.env["runbot.repo"]._domain()
            status = {
                "state": state,
                "target_url": f"http://{runbot_domain}/runbot/build/{build.id}",
                "description": "pre-commit check",
                "context": "qa/pre-commit",
            }
            build._github_status_notify_all(status)

        # 0 is myself, -1 is everybody else, -2 nothing
        return -2
