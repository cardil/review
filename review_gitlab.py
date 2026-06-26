"""GitLab forge implementation using glab CLI."""

import json
import subprocess
import sys
import urllib.parse

from review_forge import Forge


_MERGE_STATE_MAP = {
    "mergeable": "CLEAN",
    "conflicts": "DIRTY",
    "draft_status": "DRAFT",
    "discussions_not_resolved": "BLOCKED",
    "not_approved": "BLOCKED",
    "ci_must_pass": "UNSTABLE",
    "ci_still_running": "UNSTABLE",
    "checking": "UNKNOWN",
    "approvals_syncing": "UNKNOWN",
    "not_open": "BLOCKED",
    "broken_status": "DIRTY",
}

_PIPELINE_STATE_MAP = {
    "success": "SUCCESS",
    "failed": "FAILURE",
    "canceled": "SUCCESS",
    "skipped": "SUCCESS",
    "pending": "PENDING",
    "running": "PENDING",
    "created": "PENDING",
    "waiting_for_resource": "PENDING",
    "preparing": "PENDING",
    "manual": "PENDING",
    "scheduled": "PENDING",
}


class GitLabForge(Forge):

    def get_mr_number(self, args: list[str]) -> int:
        if args:
            try:
                return int(args[0])
            except ValueError:
                print(f"Error: Invalid MR number: {args[0]}", file=sys.stderr)
                sys.exit(1)

        result = subprocess.run(
            ["glab", "mr", "view", "--output", "json"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data["iid"]

        raise RuntimeError("Could not determine MR number")

    def _get_project_path(self) -> str:
        result = subprocess.run(
            ["glab", "repo", "view", "--output", "json"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError("Could not determine GitLab project")
        data = json.loads(result.stdout)
        return data["path_with_namespace"]

    def fetch_unresolved_threads(self, mr_number: int) -> tuple[list[dict], dict]:
        project_path = self._get_project_path()
        encoded_path = urllib.parse.quote(project_path, safe="")
        print(f"Fetching unresolved comments for {project_path}!{mr_number}...")

        discussions = self._api_get(f"projects/{encoded_path}/merge_requests/{mr_number}/discussions")
        mr_data = self._api_get(f"projects/{encoded_path}/merge_requests/{mr_number}")
        approvals_data = self._api_get(f"projects/{encoded_path}/merge_requests/{mr_number}/approvals")

        web_url = mr_data.get("web_url", "")

        normalized_threads = []
        for discussion in discussions:
            if discussion.get("individual_note", True):
                continue

            notes = discussion.get("notes", [])
            if not notes:
                continue

            first_note = notes[0]

            if not first_note.get("resolvable", False):
                continue

            if first_note.get("resolved", True):
                continue

            discussion_id = discussion.get("id", "")
            thread_id = discussion_id[:8] if len(discussion_id) >= 8 else discussion_id

            position = first_note.get("position")
            if position:
                path = position.get("new_path")
                line = position.get("new_line") or position.get("old_line") or "?"
            else:
                path = None
                line = "?"

            comments = []
            for note in notes:
                note_author = note.get("author", {}).get("username", "unknown")
                note_id = note.get("id", "")
                note_url = f"{web_url}#note_{note_id}"
                comments.append(
                    {
                        "author": note_author,
                        "body": note.get("body", ""),
                        "url": note_url,
                        "comment_id": str(note_id),
                    }
                )

            normalized_threads.append(
                {
                    "thread_id": thread_id,
                    "path": path,
                    "line": line,
                    "is_outdated": False,
                    "comments": comments,
                }
            )

        is_approved = approvals_data.get("approved", False)
        approved_by = approvals_data.get("approved_by", [])
        reviewers = [
            {"login": entry["user"]["username"], "state": "APPROVED"}
            for entry in approved_by
            if entry.get("user")
        ]

        has_conflicts = mr_data.get("has_conflicts", False)
        detailed_merge_status = mr_data.get("detailed_merge_status", "")
        merge_state = _MERGE_STATE_MAP.get(
            detailed_merge_status,
            detailed_merge_status.upper() if detailed_merge_status else "UNKNOWN",
        )

        pipeline = mr_data.get("pipeline")
        checks = None
        if pipeline:
            pipeline_status = pipeline.get("status", "unknown")
            normalized_pipeline_state = _PIPELINE_STATE_MAP.get(pipeline_status, "UNKNOWN")
            checks = {
                "state": normalized_pipeline_state,
                "contexts": {
                    "nodes": [
                        {
                            "__typename": "StatusContext",
                            "context": "GitLab Pipeline",
                            "state": normalized_pipeline_state,
                            "targetUrl": pipeline.get("web_url", ""),
                        }
                    ]
                },
            }

        normalized_status: dict = {
            "review_summary": "APPROVED" if is_approved else "REVIEW_REQUIRED",
            "reviewers": reviewers,
            "mergeable": "CONFLICTING" if has_conflicts else "MERGEABLE",
            "merge_state": merge_state,
            "checks": checks,
            "blocking_discussions_resolved": mr_data.get("blocking_discussions_resolved"),
        }

        return normalized_threads, normalized_status

    def reply_to_thread(self, thread_id: str, body: str) -> None:
        project_path = self._get_project_path()
        encoded_path = urllib.parse.quote(project_path, safe="")
        mr_number = self.get_mr_number([])

        discussions = self._api_get(
            f"projects/{encoded_path}/merge_requests/{mr_number}/discussions"
        )

        matches = [
            disc.get("id", "")
            for disc in discussions
            if disc.get("id", "").startswith(thread_id)
        ]

        if len(matches) == 0:
            print(f"Error: No discussion found matching prefix '{thread_id}'", file=sys.stderr)
            sys.exit(1)
        elif len(matches) > 1:
            print(
                f"Error: Ambiguous thread ID '{thread_id}', matches:", file=sys.stderr
            )
            for m in matches:
                print(f"  {m}", file=sys.stderr)
            sys.exit(1)

        full_discussion_id = matches[0]

        result = subprocess.run(
            [
                "glab",
                "api",
                "-X",
                "POST",
                f"projects/{encoded_path}/merge_requests/{mr_number}/discussions/{full_discussion_id}/notes",
                "-f",
                f"body={body}",
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Error replying to thread: {result.stderr}")

        print(f"✅ Successfully replied to thread {thread_id}")

    @staticmethod
    def _api_get(endpoint: str) -> dict | list:
        result = subprocess.run(
            ["glab", "api", endpoint],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"GitLab API error: {result.stderr}")
        return json.loads(result.stdout)
