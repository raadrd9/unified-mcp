import os
import json
import base64
import requests
from typing import Optional
from fastmcp import FastMCP

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

READ_ONLY_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False
}

WRITE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": False
}

GH_INSTRUCTIONS = (
    "Provides full access to the authenticated user's GitHub repositories, source code, "
    "files, issues, pull requests, commits, and GitHub Actions workflows. "
    "Use this service to inspect projects, read files, search code, and commit file updates."
)

mcp = FastMCP("GitHub Pro", instructions=GH_INSTRUCTIONS)

def _gh_headers(accept: str = "application/vnd.github+json") -> dict:
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "GitHub-Pro-FastMCP-V5"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def github_list_my_repos(visibility: str = "all", sort: str = "updated", max_results: int = 30) -> str:
    """Lists repositories belonging to the authenticated GitHub account (both private and public)."""
    r = requests.get(
        "https://api.github.com/user/repos",
        headers=_gh_headers(),
        params={"visibility": visibility, "sort": sort, "direction": "desc", "per_page": min(max_results, 100), "affiliation": "owner,collaborator,organization_member"},
        timeout=20
    )
    if r.status_code != 200:
        return json.dumps({"error": r.status_code, "message": r.text})
    return json.dumps({
        "repositories": [
            {
                "full_name": repo.get("full_name"),
                "private": repo.get("private"),
                "html_url": repo.get("html_url"),
                "description": repo.get("description"),
                "language": repo.get("language"),
                "default_branch": repo.get("default_branch"),
                "updated_at": repo.get("updated_at")
            }
            for repo in r.json()[:max_results]
        ]
    }, ensure_ascii=False, indent=2)

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def github_search_repositories(query: str, sort: str = "stars", max_results: int = 10) -> str:
    """Searches GitHub repositories by keyword or topic."""
    r = requests.get("https://api.github.com/search/repositories", headers=_gh_headers(), params={"q": query, "sort": sort, "order": "desc", "per_page": min(max_results, 30)}, timeout=20)
    if r.status_code != 200:
        return json.dumps({"error": r.status_code, "message": r.text})
    data = r.json()
    return json.dumps({
        "total_count": data.get("total_count", 0),
        "repositories": [
            {"full_name": repo.get("full_name"), "html_url": repo.get("html_url"), "description": repo.get("description"), "language": repo.get("language"), "updated_at": repo.get("updated_at")}
            for repo in data.get("items", [])[:max_results]
        ]
    }, ensure_ascii=False, indent=2)

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def github_search_code(query: str, max_results: int = 10) -> str:
    """Searches inside source code across GitHub or inside specific repositories."""
    r = requests.get("https://api.github.com/search/code", headers=_gh_headers("application/vnd.github.text-match+json"), params={"q": query, "per_page": min(max_results, 30)}, timeout=20)
    if r.status_code != 200:
        return json.dumps({"error": r.status_code, "message": r.text})
    data = r.json()
    return json.dumps({
        "total_count": data.get("total_count", 0),
        "results": [
            {"repository": i.get("repository", {}).get("full_name"), "path": i.get("path"), "html_url": i.get("html_url"), "text_matches": [m.get("fragment", "") for m in i.get("text_matches", [])]}
            for i in data.get("items", [])[:max_results]
        ]
    }, ensure_ascii=False, indent=2)

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def github_get_file_or_dir(owner: str, repo: str, path: str = "", ref: Optional[str] = None) -> str:
    """Reads complete file content or lists directory entries inside a GitHub repository."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path.lstrip('/')}"
    r = requests.get(url, headers=_gh_headers(), params={"ref": ref} if ref else {}, timeout=20)
    if r.status_code != 200:
        return json.dumps({"error": r.status_code, "message": r.text})
    data = r.json()
    if isinstance(data, list):
        return json.dumps({"type": "directory", "path": path, "entries": [{"name": e.get("name"), "path": e.get("path"), "type": e.get("type"), "size": e.get("size")} for e in data]}, ensure_ascii=False, indent=2)
    content = ""
    if data.get("encoding") == "base64" and data.get("content"):
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    elif data.get("download_url"):
        content = requests.get(data["download_url"], headers=_gh_headers(), timeout=20).text
    return json.dumps({"type": "file", "path": data.get("path"), "sha": data.get("sha"), "content": content[:100000]}, ensure_ascii=False, indent=2)

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def github_list_issues_and_prs(owner: str, repo: str, state: str = "open", max_results: int = 15) -> str:
    """Lists Issues and Pull Requests in a repository."""
    r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/issues", headers=_gh_headers(), params={"state": state, "per_page": min(max_results, 50)}, timeout=20)
    if r.status_code != 200:
        return json.dumps({"error": r.status_code, "message": r.text})
    return json.dumps({"items": [{"number": i.get("number"), "type": "pull_request" if "pull_request" in i else "issue", "title": i.get("title"), "state": i.get("state"), "user": i.get("user", {}).get("login"), "created_at": i.get("created_at"), "body_preview": (i.get("body") or "")[:500]} for i in r.json()[:max_results]]}, ensure_ascii=False, indent=2)

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def github_get_issue_or_pr_full_thread(owner: str, repo: str, number: int) -> str:
    """Retrieves full details of an Issue or Pull Request including all discussion comments."""
    issue_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    r = requests.get(issue_url, headers=_gh_headers(), timeout=20)
    if r.status_code != 200:
        return json.dumps({"error": r.status_code, "message": r.text})
    item = r.json()
    comments_r = requests.get(f"{issue_url}/comments", headers=_gh_headers(), params={"per_page": 100}, timeout=20)
    return json.dumps({
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "body": item.get("body"),
        "discussion_comments": [{"user": c.get("user", {}).get("login"), "created_at": c.get("created_at"), "body": c.get("body")} for c in (comments_r.json() if comments_r.status_code == 200 else [])]
    }, ensure_ascii=False, indent=2)

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def github_get_commits_and_comments(owner: str, repo: str, sha_or_branch: Optional[str] = None, commit_sha: Optional[str] = None, max_results: int = 15) -> str:
    """Lists recent commits on a branch or inspects a specific commit SHA with diff patches."""
    if commit_sha:
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/commits/{commit_sha}", headers=_gh_headers(), timeout=20)
        if r.status_code != 200:
            return json.dumps({"error": r.status_code, "message": r.text})
        c_data = r.json()
        return json.dumps({"sha": c_data.get("sha"), "message": c_data.get("commit", {}).get("message"), "files": [{"filename": f.get("filename"), "status": f.get("status"), "patch": (f.get("patch") or "")[:4000]} for f in c_data.get("files", [])[:30]]}, ensure_ascii=False, indent=2)
    params = {"per_page": min(max_results, 50)}
    if sha_or_branch:
        params["sha"] = sha_or_branch
    r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/commits", headers=_gh_headers(), params=params, timeout=20)
    if r.status_code != 200:
        return json.dumps({"error": r.status_code, "message": r.text})
    return json.dumps({"commits": [{"sha": c.get("sha"), "date": c.get("commit", {}).get("author", {}).get("date"), "message": c.get("commit", {}).get("message")} for c in r.json()[:max_results]]}, ensure_ascii=False, indent=2)

@mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
def github_get_actions_runs_and_jobs(owner: str, repo: str, run_id: Optional[int] = None, max_results: int = 10) -> str:
    """Lists GitHub Actions workflow runs or inspects jobs for a specific run ID."""
    if run_id:
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs", headers=_gh_headers(), timeout=20)
        return json.dumps(r.json() if r.status_code == 200 else {"error": r.status_code, "message": r.text}, ensure_ascii=False, indent=2)
    r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/actions/runs", headers=_gh_headers(), params={"per_page": min(max_results, 30)}, timeout=20)
    if r.status_code != 200:
        return json.dumps({"error": r.status_code, "message": r.text})
    return json.dumps({"workflow_runs": [{"id": run.get("id"), "name": run.get("name"), "status": run.get("status"), "conclusion": run.get("conclusion"), "created_at": run.get("created_at")} for run in r.json().get("workflow_runs", [])[:max_results]]}, ensure_ascii=False, indent=2)

@mcp.tool(annotations=WRITE_ANNOTATIONS)
def github_create_or_update_file(owner: str, repo: str, path: str, content: str, commit_message: str, branch: str = "main") -> str:
    """Creates a new file or updates and commits an existing file in a GitHub repository."""
    clean_path = path.lstrip("/")
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{clean_path}"
    existing_sha = None
    get_r = requests.get(url, headers=_gh_headers(), params={"ref": branch}, timeout=20)
    if get_r.status_code == 200 and isinstance(get_r.json(), dict):
        existing_sha = get_r.json().get("sha")
    payload = {"message": commit_message, "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"), "branch": branch}
    if existing_sha:
        payload["sha"] = existing_sha
    put_r = requests.put(url, headers=_gh_headers(), json=payload, timeout=25)
    if put_r.status_code not in (200, 201):
        return json.dumps({"error": put_r.status_code, "message": put_r.text})
    resp_data = put_r.json()
    return json.dumps({"status": "updated" if existing_sha else "created", "path": resp_data.get("content", {}).get("path"), "commit_sha": resp_data.get("commit", {}).get("sha")}, ensure_ascii=False, indent=2)

@mcp.tool(annotations=WRITE_ANNOTATIONS)
def github_delete_file(owner: str, repo: str, path: str, commit_message: str, branch: str = "main") -> str:
    """Deletes a file from a GitHub repository and commits the change."""
    clean_path = path.lstrip("/")
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{clean_path}"
    get_r = requests.get(url, headers=_gh_headers(), params={"ref": branch}, timeout=20)
    if get_r.status_code != 200 or not isinstance(get_r.json(), dict):
        return json.dumps({"error": get_r.status_code, "message": "File not found"})
    del_r = requests.delete(url, headers=_gh_headers(), json={"message": commit_message, "sha": get_r.json().get("sha"), "branch": branch}, timeout=20)
    return json.dumps({"status": "deleted", "path": clean_path} if del_r.status_code == 200 else {"error": del_r.status_code, "message": del_r.text}, ensure_ascii=False, indent=2)

@mcp.tool(annotations=WRITE_ANNOTATIONS)
def github_create_repository(name: str, description: str = "", private: bool = False, auto_init: bool = True) -> str:
    """Creates a new GitHub repository in the authenticated user's account."""
    r = requests.post("https://api.github.com/user/repos", headers=_gh_headers(), json={"name": name, "description": description, "private": private, "auto_init": auto_init}, timeout=20)
    return json.dumps(r.json() if r.status_code in (200, 201) else {"error": r.status_code, "message": r.text}, ensure_ascii=False, indent=2)

@mcp.tool(annotations=WRITE_ANNOTATIONS)
def github_create_issue_or_comment(owner: str, repo: str, body: str, title: Optional[str] = None, issue_number: Optional[int] = None) -> str:
    """Creates a new Issue or posts a comment on an existing Issue or Pull Request."""
    if issue_number is not None:
        r = requests.post(f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments", headers=_gh_headers(), json={"body": body}, timeout=20)
        return json.dumps({"status": "comment_created", "html_url": r.json().get("html_url")} if r.status_code in (200, 201) else {"error": r.status_code, "message": r.text}, ensure_ascii=False, indent=2)
    r = requests.post(f"https://api.github.com/repos/{owner}/{repo}/issues", headers=_gh_headers(), json={"title": title or "New Issue", "body": body}, timeout=20)
    return json.dumps({"status": "issue_created", "number": r.json().get("number"), "html_url": r.json().get("html_url")} if r.status_code in (200, 201) else {"error": r.status_code, "message": r.text}, ensure_ascii=False, indent=2)
