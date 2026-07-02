# review

A command-line tool for fetching and managing review comments from GitHub PRs and GitLab MRs.

## Overview

`review` simplifies the PR/MR review workflow by providing a streamlined interface to view unresolved review comments and reply to review threads directly from the command line. It auto-detects the forge (GitHub or GitLab) from git remotes and routes to the appropriate API. Same CLI interface regardless of forge.

## Features

- **Multi-forge support**: Works with both GitHub and GitLab out of the box
- **Auto-detection**: Detects forge type from git remote hostname, caches in `~/.cache/review/forges.ini`
- **Self-healing**: If the assumed forge API fails, automatically tries the other forge and updates cache
- **Fetch unresolved comments**: View all unresolved review threads for a PR/MR with detailed statistics
- **Review approvals**: See review decision status and who approved, requested changes, or commented
- **PR/MR status summary**: Check mergeability, merge state, and CI/CD check status at a glance
- **Detailed check breakdown**: See which checks passed, failed, or are still pending
- **Smart PR/MR detection**: Automatically detects PR/MR number from current branch or accepts explicit number
- **Reply to threads**: Respond to review comments directly from the command line
- **Pagination support**: Handles large PRs with many review threads (GitHub)
- **Status tracking**: Shows which threads are outdated, responded to, or still need attention
- **Direct links**: Provides clickable URLs to each comment and check for easy navigation

## Requirements

- Python 3.10 or higher
- For GitHub: [GitHub CLI (`gh`)](https://cli.github.com/) installed and authenticated
- For GitLab: [GitLab CLI (`glab`)](https://gitlab.com/gitlab-org/cli) installed and authenticated
- Git repository with a configured remote

## Installation

1. Clone this repository or download the `review` script
2. Make the script executable:
   ```bash
   chmod +x review
   ```
3. (Optional) Add it to your PATH or create a symlink:
   ```bash
   ln -s /path/to/review ~/.local/bin/review
   ```

## Usage

### Fetch unresolved comments

Get unresolved review comments for the current branch's PR/MR:
```bash
review get
```

Get unresolved comments for a specific PR/MR number:
```bash
review get 123
```

### Reply to a review thread

Reply to a thread using content from a file:
```bash
review reply 1234567890 response.md
```

Reply to a thread using stdin:
```bash
echo "Thanks for the review! Fixed in the latest commit." | review reply 1234567890 -
```

### Get help

```bash
review help
```

## Output Format

The `get` command displays:

### Review Threads
- Thread ID and file location (path:line)
- Whether the thread is outdated
- All comments in the thread with author information
- Direct links to each comment
- Summary statistics showing:
  - Total addressed threads (responded + outdated)
  - Threads with responses
  - Outdated threads

### PR/MR Status
- **Reviews**: Overall review decision (APPROVED, CHANGES_REQUESTED, etc.)
  - Individual reviewer statuses with usernames
  - Shows who approved, requested changes, or commented
- **Mergeability**: Whether the PR/MR can be merged (conflicts, etc.)
- **Merge State**: Current state (CLEAN, BLOCKED, BEHIND, etc.)
- **CI/CD Checks**: Overall status and detailed breakdown
  - Failed checks with links (shown first for quick attention)
  - Pending/in-progress checks
  - Passed checks (summarized count)
- **Blocking discussions** (GitLab): Whether blocking discussions are resolved

## Example Output

### GitHub

```
$ review get 42
Fetching unresolved comments for owner/repo#42...
📋 Found 2 unresolved review thread(s):

── Thread 3423986075 by @reviewer1: src/main.py:15 ──

Consider using a context manager here.

🔗 Link: https://github.com/owner/repo/pull/42#discussion_r3423986075

─── Response 3426283699 by @author:

Good point, fixed in latest push.

🔗 Link: https://github.com/owner/repo/pull/42#discussion_r3426283699

== Thread end 3423986075: src/main.py:15 ==
── Thread 3436744571 by @reviewer2: README.md:8 (outdated) ──

Typo in the description.

🔗 Link: https://github.com/owner/repo/pull/42#discussion_r3436744571

== Thread end 3436744571: README.md:8 (outdated) ==

============================================================
📊 Review Threads Summary:
 * Addressed threads: 2 / 2
 * Responded: 1
 * Outdated: 1

────────────────────────────────────────────────────────────
🔍 PR Status:
 ❌ Reviews: CHANGES REQUESTED
   💬 Commented by: reviewer1
 ✅ Mergeable: YES
 ⚠️ State: UNSTABLE
 ❌ Checks: FAILURE

📝 Checks breakdown (3 total):
 ❌ Failed (1):
      • build (FAILURE)
        https://github.com/owner/repo/actions/runs/123/job/456
 ✅ Passed (2)
```

### GitLab

```
$ review get 1
Fetching unresolved comments for group/project!1...
📋 Found 1 unresolved review thread(s):

── Thread 564776ce by @reviewer1: docs/design.md:74 ──

Should we add a sequence diagram here?

🔗 Link: https://gitlab.example.com/group/project/-/merge_requests/1#note_22158853

─── Response 22160030 by @author:

Good idea, added in the latest commit.

🔗 Link: https://gitlab.example.com/group/project/-/merge_requests/1#note_22160030

== Thread end 564776ce: docs/design.md:74 ==

============================================================
📊 Review Threads Summary:
 * Addressed threads: 1 / 1
 * Responded: 1
 * Outdated: 0

────────────────────────────────────────────────────────────
🔍 PR Status:
 ✅ Reviews: APPROVED
   ✅ Approved by: reviewer1
 ✅ Mergeable: YES
 ✅ State: CLEAN
 ✅ Blocking discussions: resolved
 ✅ Checks: SUCCESS

📝 Checks breakdown (1 total):
 ✅ Passed (1)
```

## Commands

```bash
# Check current PR/MR for unresolved comments
review get

# Check specific PR/MR
review get 456

# Reply to a comment
echo "LGTM, thanks!" | review reply 1234567890 -

# Reply with a longer response from a file
review reply 1234567890 my-response.txt
```

## How It Works

`review` supports two forges:

**GitHub** (via `gh` CLI):
- **GraphQL API** to fetch review threads with pagination support
- **REST API** to post replies to review threads
- Thread IDs are `discussion_r` numeric IDs from comment URLs

**GitLab** (via `glab` CLI):
- **REST API** to fetch MR discussions, status, and approvals
- **REST API** to post replies to discussion threads
- Thread IDs are hex discussion ID prefixes (first 8 characters)

Forge is auto-detected from git remote hostname and cached in `~/.cache/review/forges.ini`. The `github.com` hostname defaults to GitHub. All other hostnames default to GitLab. If the initial assumption is wrong (e.g., a GitHub Enterprise instance), the tool self-heals by trying the other forge and updating the cache.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.

## Credits

Based on the approach described in [this Stack Overflow answer](https://stackoverflow.com/a/66072198/844449).

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.
