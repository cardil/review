import importlib.util
import io
import json
import subprocess
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch

from github import GitHubForge
from gitlab import GitLabForge


def _load_review_module():
    path = Path(__file__).with_name("review")
    loader = SourceFileLoader("review_cli", str(path))
    spec = importlib.util.spec_from_loader("review_cli", loader)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


review_cli = _load_review_module()


class CoordinateTests(unittest.TestCase):
    def test_github_coordinates_select_full_repository(self):
        forge = GitHubForge()

        number = forge.get_mr_number(["owner/repo#94"])

        self.assertEqual(number, 94)
        self.assertEqual(forge._repo_override, ("owner", "repo"))

    def test_github_short_coordinates_use_current_repository_name(self):
        forge = GitHubForge()
        with patch.object(forge, "_get_repo_info", return_value=("current", "repo")):
            number = forge.get_mr_number(["owner#94"])

        self.assertEqual(number, 94)
        self.assertEqual(forge._repo_override, ("owner", "repo"))

    def test_gitlab_coordinates_select_project_path(self):
        forge = GitLabForge()

        number = forge.get_mr_number(["owner/repo!94"])

        self.assertEqual(number, 94)
        self.assertEqual(forge._project_path_override, "owner/repo")

    def test_gitlab_coordinates_accept_nested_project_path(self):
        forge = GitLabForge()

        number = forge.get_mr_number(["group/subgroup/project!94"])

        self.assertEqual(number, 94)
        self.assertEqual(forge._project_path_override, "group/subgroup/project")

    def test_gitlab_coordinates_reject_empty_project_component(self):
        forge = GitLabForge()

        with self.assertRaisesRegex(RuntimeError, "Invalid MR number"):
            forge.get_mr_number(["owner//repo!94"])


class GithubDiscoveryTests(unittest.TestCase):
    def test_configured_github_remotes_include_origin(self):
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "remote.origin.url git@github.com:fork/repo.git\n"
                "remote.upstream.url https://github.com/owner/repo.git\n"
                "remote.gitlab.url git@gitlab.com:owner/repo.git\n"
            ),
            stderr="",
        )
        with patch("github.subprocess.run", return_value=result):
            remotes = GitHubForge._get_github_repos()

        self.assertEqual(remotes, ["fork/repo", "owner/repo"])

    def test_configured_github_remotes_deduplicate_case_variants(self):
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "remote.origin.url git@github.com:Owner/Repo.git\n"
                "remote.upstream.url https://github.com/owner/repo.git\n"
                "remote.mirror.url https://github.com/OWNER/REPO.git\n"
                "remote.other.url https://github.com/other/repo.git\n"
            ),
            stderr="",
        )
        with patch("github.subprocess.run", return_value=result):
            remotes = GitHubForge._get_github_repos()

        self.assertEqual(remotes, ["Owner/Repo", "other/repo"])

    def test_discovery_matches_head_repository_not_branch_only(self):
        forge = GitHubForge()
        branch_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="feature-94\n", stderr=""
        )
        unrelated = json.dumps([
            {
                "number": 12,
                "headRepositoryOwner": {"login": "unrelated"},
                "headRepository": {"name": "repo"},
            }
        ])
        matching = json.dumps([
            {
                "number": 94,
                "headRepositoryOwner": {"login": "owner"},
                "headRepository": {"name": "repo"},
            }
        ])

        def run(command, **kwargs):
            if command[:3] == ["git", "branch", "--show-current"]:
                return branch_result
            if "--repo" not in command:
                raise AssertionError(f"unexpected unscoped command: {command}")
            repo = command[command.index("--repo") + 1]
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout=unrelated if repo == "fork/repo" else matching,
                stderr="",
            )

        with patch.object(forge, "_get_github_repos", return_value=["fork/repo", "owner/repo"]), \
                patch("github.subprocess.run", side_effect=run):
            number = forge.get_mr_number([])

        self.assertEqual(number, 94)
        self.assertEqual(forge._repo_override, ("owner", "repo"))

    def test_discovery_continues_after_failed_remote_query(self):
        forge = GitHubForge()
        matching = json.dumps([
            {
                "number": 94,
                "headRepositoryOwner": {"login": "owner"},
                "headRepository": {"name": "repo"},
            }
        ])

        def run(command, **kwargs):
            repo = command[command.index("--repo") + 1]
            if repo == "broken/repo":
                return subprocess.CompletedProcess(
                    args=command, returncode=1, stdout="", stderr="auth failed"
                )
            return subprocess.CompletedProcess(
                args=command, returncode=0, stdout=matching, stderr=""
            )

        with patch.object(forge, "_get_github_repos", return_value=["broken/repo", "owner/repo"]), \
                patch("github.get_current_branch", return_value="feature-94"), \
                patch("github.subprocess.run", side_effect=run):
            number = forge.get_mr_number([])

        self.assertEqual(number, 94)
        self.assertEqual(forge._repo_override, ("owner", "repo"))

    def test_discovery_reports_cli_error_when_all_remote_queries_fail(self):
        forge = GitHubForge()

        def run(command, **kwargs):
            return subprocess.CompletedProcess(
                args=command, returncode=1, stdout="", stderr="auth failed"
            )

        with patch.object(forge, "_get_github_repos", return_value=["owner/repo"]), \
                patch("github.get_current_branch", return_value="feature-94"), \
                patch("github.subprocess.run", side_effect=run), \
                self.assertRaisesRegex(RuntimeError, "GitHub CLI error: auth failed"):
            forge.get_mr_number([])

    def test_discovery_keeps_empty_successful_results_as_no_match(self):
        forge = GitHubForge()

        def run(command, **kwargs):
            return subprocess.CompletedProcess(
                args=command, returncode=0, stdout="", stderr=""
            )

        with patch.object(forge, "_get_github_repos", return_value=["owner/repo"]), \
                patch("github.get_current_branch", return_value="feature-94"), \
                patch("github.subprocess.run", side_effect=run), \
                self.assertRaisesRegex(RuntimeError, "No open PR found"):
            forge.get_mr_number([])

    def test_discovery_accepts_fork_when_no_github_remotes_are_configured(self):
        forge = GitHubForge()
        matching = json.dumps([
            {
                "number": 94,
                "headRepositoryOwner": {"login": "fork"},
                "headRepository": {"name": "repo"},
            }
        ])

        def run(command, **kwargs):
            return subprocess.CompletedProcess(
                args=command, returncode=0, stdout=matching, stderr=""
            )

        with patch.object(forge, "_get_github_repos", return_value=[]), \
                patch.object(forge, "_get_repo_info", return_value=("owner", "repo")), \
                patch("github.get_current_branch", return_value="feature-94"), \
                patch("github.subprocess.run", side_effect=run):
            number = forge.get_mr_number([])

        self.assertEqual(number, 94)
        self.assertEqual(forge._repo_override, ("owner", "repo"))


class GetCommandTests(unittest.TestCase):
    def test_explicit_gitlab_target_routes_before_parsing(self):
        with patch.object(review_cli, "detect_forge", return_value=GitHubForge()), \
                patch.object(review_cli, "_get_remote_hostname", return_value=None), \
                patch.object(GitLabForge, "get_mr_number", return_value=94) as get_number, \
                patch.object(GitLabForge, "fetch_unresolved_threads", return_value=([], {})) as fetch, \
                patch.object(GitLabForge, "print_threads"):
            review_cli.cmd_get(["owner/repo!94"])

        get_number.assert_called_once_with(["owner/repo!94"])
        fetch.assert_called_once_with(94)

    def test_explicit_target_does_not_update_forge_cache(self):
        with patch.object(review_cli, "detect_forge", return_value=GitHubForge()), \
                patch.object(review_cli, "_get_remote_hostname", return_value="github.com"), \
                patch.object(GitLabForge, "get_mr_number", return_value=94), \
                patch.object(GitLabForge, "fetch_unresolved_threads", return_value=([], {})), \
                patch.object(GitLabForge, "print_threads"), \
                patch.object(review_cli, "_update_cache") as update_cache:
            review_cli.cmd_get(["owner/repo!94"])

        update_cache.assert_not_called()


class ReplyTests(unittest.TestCase):
    def test_explicit_reply_target_is_preserved(self):
        forge = GitHubForge()
        with patch.object(review_cli, "detect_forge", return_value=forge), \
                patch.object(review_cli, "_get_remote_hostname", return_value="github.com"), \
                patch.object(review_cli, "_prepend_author", return_value="body"), \
                patch.object(forge, "get_mr_number") as get_number, \
                patch.object(forge, "reply_to_thread") as reply, \
                patch("sys.stdin", io.StringIO("body")):
            review_cli.cmd_reply(["owner/repo#94", "thread-1", "-"])

        get_number.assert_called_once_with(["owner/repo#94"])
        reply.assert_called_once_with("thread-1", "body")

    def test_explicit_target_does_not_update_forge_cache(self):
        forge = GitHubForge()
        with patch.object(review_cli, "detect_forge", return_value=forge), \
                patch.object(review_cli, "_get_remote_hostname", return_value="github.com"), \
                patch.object(review_cli, "_prepend_author", return_value="body"), \
                patch.object(forge, "get_mr_number"), \
                patch.object(forge, "reply_to_thread"), \
                patch.object(review_cli, "_update_cache") as update_cache, \
                patch("sys.stdin", io.StringIO("body")):
            review_cli.cmd_reply(["owner/repo#94", "thread-1", "-"])

        update_cache.assert_not_called()

    def test_explicit_reply_gitlab_target_routes_before_parsing(self):
        with patch.object(review_cli, "detect_forge", return_value=GitHubForge()), \
                patch.object(review_cli, "_get_remote_hostname", return_value=None), \
                patch.object(review_cli, "_prepend_author", return_value="body"), \
                patch.object(GitLabForge, "get_mr_number", return_value=94) as get_number, \
                patch.object(GitLabForge, "reply_to_thread") as reply, \
                patch("sys.stdin", io.StringIO("body")):
            review_cli.cmd_reply(["owner/repo!94", "thread-1", "-"])

        get_number.assert_called_once_with(["owner/repo!94"])
        reply.assert_called_once_with("thread-1", "body")

    def test_explicit_reply_github_target_routes_before_parsing(self):
        with patch.object(review_cli, "detect_forge", return_value=GitLabForge()), \
                patch.object(review_cli, "_get_remote_hostname", return_value=None), \
                patch.object(review_cli, "_prepend_author", return_value="body"), \
                patch.object(GitHubForge, "get_mr_number", return_value=94) as get_number, \
                patch.object(GitHubForge, "reply_to_thread") as reply, \
                patch("sys.stdin", io.StringIO("body")):
            review_cli.cmd_reply(["owner/repo#94", "thread-1", "-"])

        get_number.assert_called_once_with(["owner/repo#94"])
        reply.assert_called_once_with("thread-1", "body")

    def test_two_argument_reply_rejects_coordinate_looking_thread_id(self):
        for target in ("owner/repo#94", "owner/repo!94"):
            with self.subTest(target=target), \
                    patch("sys.stderr", new_callable=io.StringIO) as stderr, \
                    self.assertRaises(SystemExit):
                review_cli.cmd_reply([target, "-"])

            self.assertIn("Usage: review reply", stderr.getvalue())

    def test_two_argument_numeric_reply_remains_supported(self):
        forge = GitHubForge()
        with patch.object(review_cli, "detect_forge", return_value=forge), \
                patch.object(review_cli, "_get_remote_hostname", return_value=None), \
                patch.object(review_cli, "_prepend_author", return_value="body"), \
                patch.object(forge, "reply_to_thread") as reply, \
                patch("sys.stdin", io.StringIO("body")):
            review_cli.cmd_reply(["123", "-"])

        reply.assert_called_once_with("123", "body")


if __name__ == "__main__":
    unittest.main()
