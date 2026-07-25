"""Forge abstraction for review tool -- shared base class and display logic."""

import abc
import subprocess
import sys


def get_current_branch() -> str:
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("Could not determine current branch")
    return result.stdout.strip()


class Forge(abc.ABC):
    """Abstract base for forge (GitHub/GitLab) implementations."""

    @abc.abstractmethod
    def get_mr_number(self, args: list[str]) -> int:
        """Return MR/PR number from CLI arg or current branch."""

    @abc.abstractmethod
    def fetch_unresolved_threads(self, mr_number: int) -> tuple[list[dict], dict]:
        """Fetch unresolved threads; return (threads, status) in normalized shape.

        Normalized thread shape:
            {
                "thread_id": str,      # GitHub: discussion_r ID; GitLab: first 8 chars of hex ID
                "path": str | None,    # file path (None for non-diff comments)
                "line": int | str,     # line number or "?"
                "is_outdated": bool,
                "comments": [
                    {
                        "author": str,
                        "body": str,
                        "url": str,
                        "comment_id": str,  # for response delimiter
                    }
                ]
            }

        Normalized status shape:
            {
                "review_summary": str | None,    # "APPROVED", "CHANGES_REQUESTED", etc.
                "reviewers": [{"login": str, "state": str}],
                "mergeable": str,                # "MERGEABLE", "CONFLICTING", etc.
                "merge_state": str,              # "CLEAN", "BLOCKED", etc.
                "checks": dict | None,           # status check rollup
            }
        """

    def print_threads(self, threads: list[dict], status: dict) -> None:
        """Print unresolved threads in a readable format (shared implementation)."""
        if not threads:
            print("✅ No unresolved review comments!")
        else:
            print(f"📋 Found {len(threads)} unresolved review thread(s):\n")

            total_threads = len(threads)
            outdated_count = 0
            responded_count = 0
            addressed_count = 0

            for thread in threads:
                path = thread["path"]
                line = thread["line"]
                is_outdated = thread["is_outdated"]
                outdated = " (outdated)" if is_outdated else ""
                thread_id = thread["thread_id"]

                has_response = len(thread["comments"]) > 1

                if is_outdated:
                    outdated_count += 1
                if has_response:
                    responded_count += 1
                if has_response or is_outdated:
                    addressed_count += 1

                first_author = thread["comments"][0]["author"] if thread["comments"] else "unknown"
                if path is not None:
                    print(f"── Thread {thread_id} by @{first_author}: {path}:{line}{outdated} ──")
                else:
                    print(f"── Thread {thread_id} by @{first_author}{outdated} ──")

                for idx, comment in enumerate(thread["comments"]):
                    author = comment["author"]
                    body = comment["body"]
                    url = comment["url"]
                    comment_id = comment["comment_id"]

                    if idx > 0:
                        print(f"─── Response {comment_id} by @{author}:")
                    print()
                    for line_text in body.split("\n"):
                        print(f"{line_text}")
                    print()
                    print(f"🔗 Link: {url}")
                    print()

                if path is not None:
                    print(f"== Thread end {thread_id}: {path}:{line}{outdated} ==")
                else:
                    print(f"== Thread end {thread_id}{outdated} ==")

            print("\n" + "=" * 60)
            print("📊 Review Threads Summary:")
            print(f" * Addressed threads: {addressed_count} / {total_threads}")
            print(f" * Responded: {responded_count}")
            print(f" * Outdated: {outdated_count}")

        _print_status(status)

    @abc.abstractmethod
    def reply_to_thread(self, thread_id: str, body: str) -> None:
        """Reply to a review thread."""


def _print_status(status: dict) -> None:
    """Print MR/PR status summary."""
    print("\n" + "─" * 60)
    print("🔍 PR Status:")

    review_summary = status.get("review_summary")
    if review_summary == "APPROVED":
        print(" ✅ Reviews: APPROVED")
    elif review_summary == "CHANGES_REQUESTED":
        print(" ❌ Reviews: CHANGES REQUESTED")
    elif review_summary == "REVIEW_REQUIRED":
        print(" ⏳ Reviews: REVIEW REQUIRED")
    elif review_summary is None:
        print(" ⏳ Reviews: No reviews yet")
    else:
        print(f" ❓ Reviews: {review_summary}")

    reviewers = status.get("reviewers", [])
    if reviewers:
        approved = []
        changes_requested = []
        commented = []

        for reviewer in reviewers:
            login = reviewer.get("login", "unknown")
            state = reviewer.get("state")

            if state == "APPROVED":
                approved.append(login)
            elif state == "CHANGES_REQUESTED":
                changes_requested.append(login)
            elif state == "COMMENTED":
                commented.append(login)

        if approved:
            print(f"   ✅ Approved by: {', '.join(approved)}")
        if changes_requested:
            print(f"   ❌ Changes requested by: {', '.join(changes_requested)}")
        if commented:
            print(f"   💬 Commented by: {', '.join(commented)}")

    mergeable = status.get("mergeable")
    merge_state = status.get("merge_state")

    if mergeable == "MERGEABLE":
        print(" ✅ Mergeable: YES")
    elif mergeable == "CONFLICTING":
        print(" ❌ Mergeable: NO (conflicts)")
    elif mergeable == "UNKNOWN":
        print(" ⏳ Mergeable: UNKNOWN (calculating...)")
    else:
        print(f" ❓ Mergeable: {mergeable}")

    merge_state_icon = {
        "CLEAN": "✅",
        "UNSTABLE": "⚠️",
        "DIRTY": "❌",
        "BLOCKED": "🚫",
        "BEHIND": "⬅️",
        "DRAFT": "📝",
        "UNKNOWN": "❓",
    }.get(merge_state, "❓")
    print(f" {merge_state_icon} State: {merge_state or 'UNKNOWN'}")

    # GitLab-specific: blocking discussions
    blocking_resolved = status.get("blocking_discussions_resolved")
    if blocking_resolved is not None:
        if blocking_resolved:
            print(" ✅ Blocking discussions: resolved")
        else:
            print(" 🚫 Blocking discussions: NOT resolved")

    _print_checks(status)


def _print_checks(status: dict) -> None:
    """Print CI/CD check status."""
    rollup = status.get("checks")
    if rollup:
        overall_state = rollup.get("state", "UNKNOWN")
        state_icon = {
            "SUCCESS": "✅",
            "PENDING": "⏳",
            "FAILURE": "❌",
            "ERROR": "❌",
            "EXPECTED": "⏳",
        }.get(overall_state, "❓")
        print(f" {state_icon} Checks: {overall_state}")

        contexts = rollup.get("contexts", {}).get("nodes", [])
        if contexts:
            print(f"\n📝 Checks breakdown ({len(contexts)} total):")

            success_checks = []
            pending_checks = []
            failed_checks = []

            for ctx in contexts:
                if ctx.get("__typename") == "CheckRun":
                    name = ctx.get("name", "Unknown")
                    conclusion = ctx.get("conclusion")
                    status_val = ctx.get("status")
                    url = ctx.get("detailsUrl", "")

                    if conclusion == "SUCCESS":
                        success_checks.append((name, url))
                    elif conclusion in ("FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED"):
                        failed_checks.append((name, conclusion, url))
                    elif status_val in ("IN_PROGRESS", "QUEUED", "PENDING", "WAITING"):
                        pending_checks.append((name, status_val, url))
                    else:
                        # Treat neutral/skipped as success
                        success_checks.append((name, url))

                elif ctx.get("__typename") == "StatusContext":
                    name = ctx.get("context", "Unknown")
                    state = ctx.get("state")
                    url = ctx.get("targetUrl", "")

                    if state == "SUCCESS":
                        success_checks.append((name, url))
                    elif state in ("FAILURE", "ERROR"):
                        failed_checks.append((name, state, url))
                    elif state == "PENDING":
                        pending_checks.append((name, state, url))

            if failed_checks:
                print(f" ❌ Failed ({len(failed_checks)}):")
                for name, status_val, url in failed_checks:
                    print(f"      • {name} ({status_val})")
                    if url:
                        print(f"        {url}")

            if pending_checks:
                print(f" ⏳ Pending ({len(pending_checks)}):")
                for name, status_val, url in pending_checks:
                    print(f"      • {name} ({status_val})")

            if success_checks:
                print(f" ✅ Passed ({len(success_checks)})")
    else:
        print("❓ Checks: No status information available")
