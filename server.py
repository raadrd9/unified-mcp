import os
import base64
import requests
from typing import Optional
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

SECRET_PATH = os.environ.get("MCP_SECRET_PATH", "default-secret-uuid").strip("/")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

mcp = FastMCP("GitHubUnifiedMCP")

def _gh_headers(accept: str = "application/vnd.github+json") -> dict:
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Unified-FastMCP-Agent"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers

@mcp.custom_route("/health", methods=["GET", "HEAD"])
async def health_check(request: Request) -> JSONResponse:
    """Lightweight health endpoint for Render and GitHub Actions Cron keep-alive."""
    return JSONResponse({"status": "awake", "service": "GitHubUnifiedMCP"})

@mcp.tool(annotations={"readOnlyHint": True})
def github_search_repositories(query: str, sort: str = "stars", max_results: int = 10) -> dict:
    """Search GitHub repositories by keyword, topic, or qualifier (e.g., 'fastmcp language:python')."""
    url = "https://api.github.com/search/repositories"
    params = {"q": query, "sort": sort, "order": "desc", "per_page": min(max_results, 30)}
    r = requests.get(url, headers=_gh_headers(), params=params, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    data = r.json()
    items = []
    for repo in data.get("items", [])[:max_results]:
        items.append({
            "full_name": repo.get("full_name"),
            "html_url": repo.get("html_url"),
            "description": repo.get("description"),
            "stars": repo.get("stargazers_count"),
            "forks": repo.get("forks_count"),
            "open_issues": repo.get("open_issues_count"),
            "language": repo.get("language"),
            "default_branch": repo.get("default_branch"),
            "updated_at": repo.get("updated_at"),
            "topics": repo.get("topics", [])
        })
    return {"total_count": data.get("total_count", 0), "repositories": items}

@mcp.tool(annotations={"readOnlyHint": True})
def github_search_code(query: str, max_results: int = 10) -> dict:
    """Search inside source code across GitHub (e.g., 'StreamableHTTP repo:jlowin/fastmcp')."""
    url = "https://api.github.com/search/code"
    params = {"q": query, "per_page": min(max_results, 30)}
    r = requests.get(url, headers=_gh_headers("application/vnd.github.text-match+json"), params=params, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    data = r.json()
    results = []
    for item in data.get("items", [])[:max_results]:
        matches = [m.get("fragment", "") for m in item.get("text_matches", [])]
        results.append({
            "repository": item.get("repository", {}).get("full_name"),
            "path": item.get("path"),
            "name": item.get("name"),
            "html_url": item.get("html_url"),
            "text_matches": matches
        })
    return {"total_count": data.get("total_count", 0), "results": results}

@mcp.tool(annotations={"readOnlyHint": True})
def github_get_file_or_dir(owner: str, repo: str, path: str = "", ref: Optional[str] = None) -> dict:
    """Read complete file content or list directory contents inside any GitHub repository."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path.lstrip('/')}"
    params = {"ref": ref} if ref else {}
    r = requests.get(url, headers=_gh_headers(), params=params, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    data = r.json()
    if isinstance(data, list):
        return {
            "type": "directory",
            "path": path,
            "entries": [
                {"name": e.get("name"), "path": e.get("path"), "type": e.get("type"), "size": e.get("size")}
                for e in data
            ]
        }
    content = ""
    if data.get("encoding") == "base64" and data.get("content"):
        try:
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        except Exception as exc:
            content = f"Decode error: {exc}"
    elif data.get("download_url"):
        raw_r = requests.get(data["download_url"], headers=_gh_headers(), timeout=20)
        content = raw_r.text[:100000]
    return {
        "type": "file",
        "path": data.get("path"),
        "size": data.get("size"),
        "sha": data.get("sha"),
        "html_url": data.get("html_url"),
        "content": content[:100000]
    }

@mcp.tool(annotations={"readOnlyHint": True})
def github_list_issues_and_prs(owner: str, repo: str, state: str = "open", max_results: int = 15) -> dict:
    """List Issues and Pull Requests in a repository (state: 'open', 'closed', or 'all')."""
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    params = {"state": state, "per_page": min(max_results, 50), "sort": "updated", "direction": "desc"}
    r = requests.get(url, headers=_gh_headers(), params=params, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    items = []
    for item in r.json()[:max_results]:
        items.append({
            "number": item.get("number"),
            "type": "pull_request" if "pull_request" in item else "issue",
            "title": item.get("title"),
            "state": item.get("state"),
            "user": item.get("user", {}).get("login"),
            "comments_count": item.get("comments"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "labels": [lbl.get("name") for lbl in item.get("labels", [])],
            "html_url": item.get("html_url"),
            "body_preview": (item.get("body") or "")[:500]
        })
    return {"owner": owner, "repo": repo, "items": items}

@mcp.tool(annotations={"readOnlyHint": True})
def github_get_issue_or_pr_full_thread(owner: str, repo: str, number: int) -> dict:
    """Get full details of an Issue or Pull Request including ALL discussion comments, review comments, and changed files."""
    issue_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    r = requests.get(issue_url, headers=_gh_headers(), timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    item = r.json()
    is_pr = "pull_request" in item

    comments_r = requests.get(f"{issue_url}/comments", headers=_gh_headers(), params={"per_page": 100}, timeout=20)
    issue_comments = []
    if comments_r.status_code == 200:
        for c in comments_r.json():
            issue_comments.append({
                "id": c.get("id"),
                "user": c.get("user", {}).get("login"),
                "created_at": c.get("created_at"),
                "body": c.get("body")
            })

    result = {
        "number": item.get("number"),
        "type": "pull_request" if is_pr else "issue",
        "title": item.get("title"),
        "state": item.get("state"),
        "author": item.get("user", {}).get("login"),
        "created_at": item.get("created_at"),
        "body": item.get("body"),
        "html_url": item.get("html_url"),
        "discussion_comments": issue_comments
    }

    if is_pr:
        pr_review_r = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}/comments",
            headers=_gh_headers(),
            params={"per_page": 100},
            timeout=20
        )
        if pr_review_r.status_code == 200:
            result["code_review_comments"] = [
                {
                    "user": rc.get("user", {}).get("login"),
                    "path": rc.get("path"),
                    "created_at": rc.get("created_at"),
                    "body": rc.get("body"),
                    "diff_hunk": rc.get("diff_hunk")
                }
                for rc in pr_review_r.json()
            ]
        files_r = requests.get(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}/files",
            headers=_gh_headers(),
            params={"per_page": 50},
            timeout=20
        )
        if files_r.status_code == 200:
            result["changed_files"] = [
                {
                    "filename": f.get("filename"),
                    "status": f.get("status"),
                    "additions": f.get("additions"),
                    "deletions": f.get("deletions"),
                    "patch": (f.get("patch") or "")[:4000]
                }
                for f in files_r.json()
            ]
    return result

@mcp.tool(annotations={"readOnlyHint": True})
def github_get_commits_and_comments(owner: str, repo: str, sha_or_branch: Optional[str] = None, commit_sha: Optional[str] = None, max_results: int = 15) -> dict:
    """List recent commits on a branch OR inspect a specific commit_sha with its diff patches and commit comments."""
    if commit_sha:
        c_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{commit_sha}"
        r = requests.get(c_url, headers=_gh_headers(), timeout=20)
        if r.status_code != 200:
            return {"error": r.status_code, "message": r.text}
        c_data = r.json()
        comm_r = requests.get(f"{c_url}/comments", headers=_gh_headers(), timeout=20)
        comments = comm_r.json() if comm_r.status_code == 200 else []
        return {
            "sha": c_data.get("sha"),
            "author": c_data.get("commit", {}).get("author"),
            "message": c_data.get("commit", {}).get("message"),
            "stats": c_data.get("stats"),
            "files": [
                {
                    "filename": f.get("filename"),
                    "status": f.get("status"),
                    "patch": (f.get("patch") or "")[:4000]
                }
                for f in c_data.get("files", [])[:30]
            ],
            "commit_comments": [
                {
                    "user": cm.get("user", {}).get("login"),
                    "path": cm.get("path"),
                    "position": cm.get("position"),
                    "created_at": cm.get("created_at"),
                    "body": cm.get("body")
                }
                for cm in comments
            ]
        }

    url = f"https://api.github.com/repos/{owner}/{repo}/commits"
    params = {"per_page": min(max_results, 50)}
    if sha_or_branch:
        params["sha"] = sha_or_branch
    r = requests.get(url, headers=_gh_headers(), params=params, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    commits = []
    for c in r.json()[:max_results]:
        commits.append({
            "sha": c.get("sha"),
            "author": c.get("commit", {}).get("author", {}).get("name"),
            "date": c.get("commit", {}).get("author", {}).get("date"),
            "message": c.get("commit", {}).get("message"),
            "comment_count": c.get("commit", {}).get("comment_count", 0),
            "html_url": c.get("html_url")
        })
    return {"owner": owner, "repo": repo, "commits": commits}

@mcp.tool(annotations={"readOnlyHint": True})
def github_get_actions_runs_and_jobs(owner: str, repo: str, run_id: Optional[int] = None, max_results: int = 10) -> dict:
    """List GitHub Actions workflow runs for a repository OR inspect jobs and failed steps for a specific run_id."""
    if run_id:
        jobs_url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        r = requests.get(jobs_url, headers=_gh_headers(), timeout=20)
        if r.status_code != 200:
            return {"error": r.status_code, "message": r.text}
        jobs = []
        for j in r.json().get("jobs", []):
            jobs.append({
                "id": j.get("id"),
                "name": j.get("name"),
                "status": j.get("status"),
                "conclusion": j.get("conclusion"),
                "started_at": j.get("started_at"),
                "completed_at": j.get("completed_at"),
                "steps": [
                    {
                        "name": s.get("name"),
                        "status": s.get("status"),
                        "conclusion": s.get("conclusion"),
                        "number": s.get("number")
                    }
                    for s in j.get("steps", [])
                ]
            })
        return {"run_id": run_id, "jobs": jobs}

    runs_url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs"
    r = requests.get(runs_url, headers=_gh_headers(), params={"per_page": min(max_results, 30)}, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    runs = []
    for run in r.json().get("workflow_runs", [])[:max_results]:
        runs.append({
            "id": run.get("id"),
            "name": run.get("name"),
            "event": run.get("event"),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            "head_branch": run.get("head_branch"),
            "created_at": run.get("created_at"),
            "html_url": run.get("html_url")
        })
    return {"owner": owner, "repo": repo, "workflow_runs": runs}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=port,
        path=f"/{SECRET_PATH}/mcp"
    )
