# -*- coding: utf-8 -*-
import logging
_logger = logging.getLogger(__name__)

def migrate(cr, version):
    # dependency is not correct since it will be all commits. This also free the name for a build dependant on another build params
    cr.execute("ALTER TABLE runbot_build_dependency RENAME TO runbot_build_commit;")
    cr.execute("ALTER SEQUENCE runbot_build_dependency_id_seq RENAME TO runbot_build_commit_id_seq")

