"""GitHub forge implementation using gh CLI."""

import json
import re
import subprocess
import sys

from forge import Forge, get_current_branch


class GitHubForge(Forge):

    def __init__(self) -> None:
        self._repo_override: tuple[str, str] | None = None

    def get_mr_number(self, args: list[str]) -> int:
        if args:
            try:
                return int(args[0])
            except ValueError:
                print(f"Error: Invalid PR number: {args[0]}", file=sys.stderr)
                sys.exit(1)

        branch_name = get_current_branch()

        result = subprocess.run(
            [
                "gh", "pr", "list",
                "--head", branch_name,
                "--state", "open",
                "--json", "number",
                "--jq", ".[].number",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"GitHub CLI error: {result.stderr.strip()}")
        if result.stdout.strip():
            numbers = result.stdout.strip().splitlines()
            if len(numbers) == 1:
                return int(numbers[0])
            if len(numbers) > 1:
                ids = ", ".join(f"#{n}" for n in numbers)
                raise RuntimeError(f"Multiple open PRs for this branch: {ids}. Specify one explicitly.")

        origin_owner = self._get_origin_owner()
        if origin_owner:
            for repo_nwo in self._get_upstream_repos():
                result = subprocess.run(
                    [
                        "gh", "pr", "list",
                        "--repo", repo_nwo,
                        "--head", branch_name,
                        "--state", "open",
                        "--json", "number,headRepositoryOwner",
                    ],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    continue
                if not result.stdout.strip():
                    continue
                try:
                    prs = json.loads(result.stdout)
                except json.JSONDecodeError:
                    continue
                mine = [pr["number"] for pr in prs
                        if (pr.get("headRepositoryOwner") or {}).get("login") == origin_owner]
                if len(mine) == 1:
                    parts = repo_nwo.split("/", 1)
                    self._repo_override = (parts[0], parts[1])
                    return mine[0]
                if len(mine) > 1:
                    ids = ", ".join(f"#{n}" for n in mine)
                    raise RuntimeError(
                        f"Multiple open PRs for this branch in {repo_nwo}: {ids}. Specify one explicitly."
                    )

        raise RuntimeError("No open PR found for current branch")

    @staticmethod
    def _get_origin_owner() -> str | None:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        url = result.stdout.strip()
        m = re.search(r"github\.com[:/]([^/]+)/", url)
        return m.group(1) if m else None

    @staticmethod
    def _get_upstream_repos() -> list[str]:
        result = subprocess.run(
            ["git", "config", "--get-regexp", r"remote\..*\.url"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        repos = []
        for line in result.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            key, url = parts
            if "origin" in key:
                continue
            m = re.search(r"github\.com[:/]([^/]+/[^/\s]+)", url)
            if m:
                nwo = m.group(1).removesuffix(".git")
                if nwo not in repos:
                    repos.append(nwo)
        return repos

    def _get_repo_info(self) -> tuple[str, str]:
        if self._repo_override:
            return self._repo_override

        result = subprocess.run(
            [
                "gh",
                "repo",
                "view",
                "--json",
                "owner,name",
                "--jq",
                "[.owner.login, .name] | @tsv",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError("Could not determine GitHub repository")
        parts = result.stdout.strip().split("\t")
        return parts[0], parts[1]

    def fetch_unresolved_threads(self, mr_number: int) -> tuple[list[dict], dict]:
        owner, repo = self._get_repo_info()
        print(f"Fetching unresolved comments for {owner}/{repo}#{mr_number}...")

        query = """
        query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
            repository(owner: $owner, name: $repo) {
                pullRequest(number: $pr) {
                    mergeable
                    mergeStateStatus
                    reviewDecision
                    latestReviews(first: 100) {
                        nodes {
                            author {
                                login
                            }
                            state
                            submittedAt
                        }
                    }
                    commits(last: 1) {
                        nodes {
                            commit {
                                statusCheckRollup {
                                    state
                                    contexts(first: 100) {
                                        nodes {
                                            __typename
                                            ... on CheckRun {
                                                name
                                                conclusion
                                                status
                                                detailsUrl
                                            }
                                            ... on StatusContext {
                                                context
                                                state
                                                targetUrl
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    reviewThreads(first: 100, after: $cursor) {
                        pageInfo {
                            hasNextPage
                            endCursor
                        }
                        nodes {
                            id
                            isResolved
                            isOutdated
                            path
                            line
                            comments(first: 20) {
                                nodes {
                                    author {
                                        login
                                    }
                                    body
                                    createdAt
                                    url
                                    id
                                }
                            }
                        }
                    }
                }
            }
        }
        """

        variables: dict = {"owner": owner, "repo": repo, "pr": mr_number, "cursor": None}
        all_threads_raw = []
        has_next_page = True
        pr_status_raw = None

        while has_next_page:
            result = subprocess.run(
                [
                    "gh",
                    "api",
                    "graphql",
                    "-f",
                    f"query={query}",
                    "-f",
                    f"owner={owner}",
                    "-f",
                    f"repo={repo}",
                    "-F",
                    f"pr={mr_number}",
                    "-f",
                    f"cursor={variables['cursor'] or ''}",
                ],
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                raise RuntimeError(f"GitHub API error: {result.stderr}")

            data = json.loads(result.stdout)
            if "errors" in data:
                error_msg = data["errors"][0].get("message", "Unknown error")
                raise RuntimeError(f"GitHub API error: {error_msg}")

            pr_data = data["data"]["repository"]["pullRequest"]
            if not pr_data:
                raise RuntimeError(f"Could not find GitHub PR #{mr_number}")

            threads_data = pr_data["reviewThreads"]

            if pr_status_raw is None:
                pr_status_raw = {
                    "mergeable": pr_data.get("mergeable"),
                    "mergeStateStatus": pr_data.get("mergeStateStatus"),
                    "reviewDecision": pr_data.get("reviewDecision"),
                    "latestReviews": pr_data.get("latestReviews", {}).get("nodes", []),
                    "statusCheckRollup": None,
                }
                commits = pr_data.get("commits", {}).get("nodes", [])
                if commits and commits[0].get("commit", {}).get("statusCheckRollup"):
                    pr_status_raw["statusCheckRollup"] = commits[0]["commit"]["statusCheckRollup"]

            for thread in threads_data["nodes"]:
                if not thread["isResolved"]:
                    all_threads_raw.append(thread)

            has_next_page = threads_data["pageInfo"]["hasNextPage"]
            variables["cursor"] = threads_data["pageInfo"]["endCursor"]

        normalized_threads = []
        for thread in all_threads_raw:
            comments = []
            for comment in thread["comments"]["nodes"]:
                author = comment["author"]["login"] if comment["author"] else "unknown"
                url = comment["url"]
                comment_id = url.split("discussion_r")[-1] if "discussion_r" in url else "?"
                comments.append(
                    {
                        "author": author,
                        "body": comment["body"],
                        "url": url,
                        "comment_id": comment_id,
                    }
                )

            first_url = (
                thread["comments"]["nodes"][0].get("url", "")
                if thread["comments"]["nodes"]
                else ""
            )
            thread_id = (
                first_url.split("discussion_r")[-1] if "discussion_r" in first_url else "?"
            )

            normalized_threads.append(
                {
                    "thread_id": thread_id,
                    "path": thread["path"],
                    "line": thread.get("line", "?"),
                    "is_outdated": thread["isOutdated"],
                    "comments": comments,
                }
            )

        normalized_status: dict = {
            "review_summary": pr_status_raw.get("reviewDecision"),
            "reviewers": [
                {
                    "login": r.get("author", {}).get("login", "unknown"),
                    "state": r.get("state"),
                }
                for r in pr_status_raw.get("latestReviews", [])
                if r.get("author")
            ],
            "mergeable": pr_status_raw.get("mergeable"),
            "merge_state": pr_status_raw.get("mergeStateStatus"),
            "checks": pr_status_raw.get("statusCheckRollup"),
        }

        return normalized_threads, normalized_status

    def _get_pr_for_comment(self, owner: str, repo: str, comment_id: str) -> int:
        url = f"/repos/{owner}/{repo}/pulls/comments/{comment_id}"
        result = subprocess.run(
            ["gh", "api", url],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Error finding comment: {result.stderr}")

        data = json.loads(result.stdout)
        pr_url = data.get("pull_request_url", "")
        if pr_url:
            return int(pr_url.rstrip("/").split("/")[-1])

        raise RuntimeError(f"Could not find PR for comment {comment_id}")

    def reply_to_thread(self, thread_id: str, body: str) -> None:
        try:
            pr_number = self.get_mr_number([])
        except (RuntimeError, SystemExit):
            pr_number = None

        owner, repo = self._get_repo_info()

        if pr_number is None:
            pr_number = self._get_pr_for_comment(owner, repo, thread_id)

        url = f"/repos/{owner}/{repo}/pulls/{pr_number}/comments/{thread_id}/replies"
        result = subprocess.run(
            ["gh", "api", "-X", "POST", url, "-f", f"body={body}"],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Error replying to thread: {result.stderr}")

        print(f"✅ Successfully replied to thread {thread_id}")
