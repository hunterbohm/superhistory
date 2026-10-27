"""A stand-in for the gh CLI, for tests. It reads made-up threads from state.json beside it and appends every call
to calls.jsonl. `search issues` filters the threads by the --updated range and caps them at --limit; `pr view` and
`issue view` accept only the --json fields the real command accepts (from gh 2.x --help), so a field the real gh
would refuse fails here too."""
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SEARCH_FIELDS = {"assignees", "author", "authorAssociation", "body", "closedAt", "commentsCount", "createdAt", "id",
                 "isLocked", "isPullRequest", "labels", "number", "repository", "state", "title", "updatedAt", "url"}
ISSUE_FIELDS = {"assignees", "author", "blockedBy", "blocking", "body", "closed", "closedAt",
                "closedByPullRequestsReferences", "comments", "createdAt", "id", "isPinned", "issueType", "labels",
                "milestone", "number", "parent", "projectCards", "projectItems", "reactionGroups", "state",
                "stateReason", "subIssues", "subIssuesSummary", "title", "updatedAt", "url"}
PR_FIELDS = {"additions", "assignees", "author", "autoMergeRequest", "baseRefName", "baseRefOid", "body",
             "changedFiles", "closed", "closedAt", "closingIssuesReferences", "comments", "commits", "createdAt",
             "deletions", "files", "fullDatabaseId", "headRefName", "headRefOid", "headRepository",
             "headRepositoryOwner", "id", "isCrossRepository", "isDraft", "labels", "latestReviews",
             "maintainerCanModify", "mergeCommit", "mergeStateStatus", "mergeable", "mergedAt", "mergedBy", "milestone",
             "number", "potentialMergeCommit", "projectCards", "projectItems", "reactionGroups", "reviewDecision",
             "reviewRequests", "reviews", "state", "statusCheckRollup", "title", "updatedAt", "url"}


def epoch(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def fail(message):
    sys.stderr.write(message + "\n")
    sys.exit(1)


def main(args):
    with open(os.path.join(HERE, "calls.jsonl"), "a") as fh:
        fh.write(json.dumps(args) + "\n")
    with open(os.path.join(HERE, "state.json")) as fh:
        threads = json.load(fh)["threads"]
    if "--json" not in args:
        fail("this fake needs --json")
    fields = args[args.index("--json") + 1].split(",")
    if args[:2] == ["search", "issues"]:
        flags = dict(a.split("=", 1) for a in args if a.startswith("--") and "=" in a)
        if "--include-prs" not in args or "--involves" not in flags:
            fail("missing --include-prs or --involves")
        if not set(fields) <= SEARCH_FIELDS:
            fail("Unknown JSON field: %s" % sorted(set(fields) - SEARCH_FIELDS))
        lo, hi = (epoch(t) for t in flags["--updated"].split(".."))
        rows = [{"repository": {"name": t["repo"].split("/")[1], "nameWithOwner": t["repo"]}, "number": t["number"],
                 "isPullRequest": t["pr"], "updatedAt": t["view"]["updatedAt"]}
                for t in threads if lo <= epoch(t["view"]["updatedAt"]) <= hi]
        rows.sort(key=lambda r: (r["repository"]["nameWithOwner"], r["number"]))
        json.dump(rows[:int(args[args.index("--limit") + 1])], sys.stdout)
        return
    kind, verb, number = args[:3]
    if verb != "view":
        fail("unsupported command")
    unknown = set(fields) - (PR_FIELDS if kind == "pr" else ISSUE_FIELDS)
    if unknown:
        fail("Unknown JSON field: %s" % sorted(unknown))
    repo = args[args.index("-R") + 1]
    for t in threads:
        if t["repo"] == repo and t["number"] == int(number) and t["pr"] == (kind == "pr"):
            json.dump({f: t["view"][f] for f in fields}, sys.stdout)
            return
    fail("GraphQL: Could not resolve to an issue or pull request with the number of %s." % number)


main(sys.argv[1:])
