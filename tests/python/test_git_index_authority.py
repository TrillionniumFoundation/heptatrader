from __future__ import annotations

import unittest
import exact_git_index_authority_cases as cases


class ExactGitIndexAuthorityTests(cases.ExactGitIndexAuthorityTests):
    """Execute real Git index/filesystem hostile-input tests.

    Workflow semantics and executable command blocks are tested in their owning
    suite; duplicate step-name and source-string assertions do not belong here.
    """


if __name__ == "__main__":
    unittest.main()
