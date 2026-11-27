"""Archived experiments are isolated from the production test suite.

AC-EXP-001 asserts that no `supportops_agent` module is imported in its process
(test_no_production_supportops_import), so it must not share a pytest process with
the main suite. A root-level `pytest` run skips it; run it on its own:

    cd experiments/agent_commons && python3 -m pytest tests -q
"""
import os

# Skip during a repository-wide run; allow when invoked from inside this directory.
if os.path.abspath(os.getcwd()).startswith(os.path.dirname(os.path.abspath(__file__))):
    collect_ignore_glob = []
else:
    collect_ignore_glob = ["agent_commons/*"]
