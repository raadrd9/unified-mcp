import os
import base64
import requests
from typing import Optional
from fastmcp import FastMCP

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

GH_INSTRUCTIONS = """
You are connected to GitHub_Pro, the user's full-access GitHub engineering workspace (raadrd9).
AUTOMATIC ROUTING RULES:
- Whenever the user asks about their GitHub repositories, projects, files, code search, commits, issues, actions, or asks to create/edit/save/delete any file or repo (even without typing @GitHub_Pro or tool names), AUTOMATICALLY invoke these tools.
- Format explanations using clear bullet points (`-`).
"""

mcp = FastMCP("GitHub_Pro", instructions=GH_INSTRUCTIONS)

def _gh_headers(accept: str = "application/vnd.github+json") -> dict:
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "GitHub-Pro-FastMCP-V4"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers

@mcp.tool(annotations={"readOnlyHint": True})
def github_list_my_repos(visibility: str = "all", sort: str = "updated", max_results: int = 30) -> dict:
    """AUTOMATICALLY use this tool when the user asks to see their GitHub projects/repositories (both private and public)."""
    r = requests.get(
        "https://api.github.com/user/repos",
        headers=_gh_headers(),
        params={"visibility": visibility, "sort": sort, "direction": "desc", "per_page": min(max_results, 100), "affiliation": "owner,collaborator,organization_member"},
        timeout=20
    )
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    return {
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
    }

@mcp.tool(annotations={"readOnlyHint": True})
def github_search_repositories(query: str, sort: str = "stars", max_results: int = 10) -> dict:
    """Search public or private GitHub repositories by keyword or topic."""
    r = requests.get("https://api.github.com/search/repositories", headers=_gh_headers(), params={"q": query, "sort": sort, "order": "desc", "per_page": min(max_results, 30)}, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    data = r.json()
    return {
        "total_count": data.get("total_count", 0),
        "repositories": [
            {"full_name": repo.get("full_name"), "html_url": repo.get("html_url"), "description": repo.get("description"), "language": repo.get("language"), "updated_at": repo.get("updated_at")}
            for repo in data.get("items", [])[:max_results]
        ]
    }

@mcp.tool(annotations={"readOnlyHint": True})
def github_search_code(query: str, max_results: int = 10) -> dict:
    """Search inside source code across GitHub or inside specific repositories."""
    r = requests.get("https://api.github.com/search/code", headers=_gh_headers("application/vnd.github.text-match+json"), params={"q": query, "per_page": min(max_results, 30)}, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    data = r.json()
    return {
        "total_count": data.get("total_count", 0),
        "results": [
            {"repository": i.get("repository", {}).get("full_name"), "path": i.get("path"), "html_url": i.get("html_url"), "text_matches": [m.get("fragment", "") for m in i.get("text_matches", [])]}
            for i in data.get("items", [])[:max_results]
        ]
    }

@mcp.tool(annotations={"readOnlyHint": True})
def github_get_file_or_dir(owner: str, repo: str, path: str = "", ref: Optional[str] = None) -> dict:
    """Read complete file content or list directory contents inside any public or private GitHub repository."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path.lstrip('/')}"
    r = requests.get(url, headers=_gh_headers(), params={"ref": ref} if ref else {}, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    data = r.json()
    if isinstance(data, list):
        return {"type": "directory", "path": path, "entries": [{"name": e.get("name"), "path": e.get("path"), "type": e.get("type"), "size": e.get("size")} for e in data]}
    content = ""
    if data.get("encoding") == "base64" and data.get("content"):
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    elif data.get("download_url"):
        content = requests.get(data["download_url"], headers=_gh_headers(), timeout=20).text
    return {"type": "file", "path": data.get("path"), "sha": data.get("sha"), "content": content[:100000]}

@mcp.tool(annotations={"readOnlyHint": True})
def github_list_issues_and_prs(owner: str, repo: str, state: str = "open", max_results: int = 15) -> dict:
    """List Issues and Pull Requests in a repository."""
    r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/issues", headers=_gh_headers(), params={"state": state, "per_page": min(max_results, 50)}, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    return {"items": [{"number": i.get("number"), "type": "pull_request" if "pull_request" in i else "issue", "title": i.get("title"), "state": i.get("state"), "user": i.get("user", {}).get("login"), "created_at": i.get("created_at"), "body_preview": (i.get("body") or "")[:500]} for i in r.json()[:max_results]]}

@mcp.tool(annotations={"readOnlyHint": True})
def github_get_issue_or_pr_full_thread(owner: str, repo: str, number: int) -> dict:
    """Get full details of an Issue or Pull Request including ALL discussion comments."""
    issue_url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    r = requests.get(issue_url, headers=_gh_headers(), timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    item = r.json()
    comments_r = requests.get(f"{issue_url}/comments", headers=_gh_headers(), params={"per_page": 100}, timeout=20)
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "body": item.get("body"),
        "discussion_comments": [{"user": c.get("user", {}).get("login"), "created_at": c.get("created_at"), "body": c.get("body")} for c in (comments_r.json() if comments_r.status_code == 200 else [])]
    }

@mcp.tool(annotations={"readOnlyHint": True})
def github_get_commits_and_comments(owner: str, repo: str, sha_or_branch: Optional[str] = None, commit_sha: Optional[str] = None, max_results: int = 15) -> dict:
    """List recent commits OR inspect a specific commit_sha with diff patches."""
    if commit_sha:
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/commits/{commit_sha}", headers=_gh_headers(), timeout=20)
        if r.status_code != 200:
            return {"error": r.status_code, "message": r.text}
        c_data = r.json()
        return {"sha": c_data.get("sha"), "message": c_data.get("commit", {}).get("message"), "files": [{"filename": f.get("filename"), "status": f.get("status"), "patch": (f.get("patch") or "")[:4000]} for f in c_data.get("files", [])[:30]]}
    params = {"per_page": min(max_results, 50)}
    if sha_or_branch:
        params["sha"] = sha_or_branch
    r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/commits", headers=_gh_headers(), params=params, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    return {"commits": [{"sha": c.get("sha"), "date": c.get("commit", {}).get("author", {}).get("date"), "message": c.get("commit", {}).get("message")} for c in r.json()[:max_results]]}

@mcp.tool(annotations={"readOnlyHint": True})
def github_get_actions_runs_and_jobs(owner: str, repo: str, run_id: Optional[int] = None, max_results: int = 10) -> dict:
    """List GitHub Actions workflow runs OR inspect jobs for a specific run_id."""
    if run_id:
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs", headers=_gh_headers(), timeout=20)
        return r.json() if r.status_code == 200 else {"error": r.status_code, "message": r.text}
    r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/actions/runs", headers=_gh_headers(), params={"per_page": min(max_results, 30)}, timeout=20)
    if r.status_code != 200:
        return {"error": r.status_code, "message": r.text}
    return {"workflow_runs": [{"id": run.get("id"), "name": run.get("name"), "status": run.get("status"), "conclusion": run.get("conclusion"), "created_at": run.get("created_at")} for run in r.json().get("workflow_runs", [])[:max_results]]}

@mcp.tool()
def github_create_or_update_file(owner: str, repo: str, path: str, content: str, commit_message: str, branch: str = "main") -> dict:
    """Create a new file OR edit and save (commit & push) an existing file in any of your GitHub repositories."""
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
        return {"error": put_r.status_code, "message": put_r.text}
    resp_data = put_r.json()
    return {"status": "updated" if existing_sha else "created", "path": resp_data.get("content", {}).get("path"), "commit_sha": resp_data.get("commit", {}).get("sha")}

@mcp.tool()
def github_delete_file(owner: str, repo: str, path: str, commit_message: str, branch: str = "main") -> dict:
    """Delete a file from a GitHub repository and commit the change."""
    clean_path = path.lstrip("/")
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{clean_path}"
    get_r = requests.get(url, headers=_gh_headers(), params={"ref": branch}, timeout=20)
    if get_r.status_code != 200 or not isinstance(get_r.json(), dict):
        return {"error": get_r.status_code, "message": "File not found"}
    del_r = requests.delete(url, headers=_gh_headers(), json={"message": commit_message, "sha": get_r.json().get("sha"), "branch": branch}, timeout=20)
    return {"status": "deleted", "path": clean_path} if del_r.status_code == 200 else {"error": del_r.status_code, "message": del_r.text}

@mcp.tool()
def github_create_repository(name: str, description: str = "", private: bool = False, auto_init: bool = True) -> dict:
    """Create a brand new GitHub repository (project) in your account (public or private)."""
    r = requests.post("https://api.github.com/user/repos", headers=_gh_headers(), json={"name": name, "description": description, "private": private, "auto_init": auto_init}, timeout=20)
    return r.json() if r.status_code in (200, 201) else {"error": r.status_code, "message": r.text}

@mcp.tool()
def github_create_issue_or_comment(owner: str, repo: str, body: str, title: Optional[str] = None, issue_number: Optional[int] = None) -> dict:
    """Create a new Issue in a repository OR post a comment on an existing Issue / Pull Request."""
    if issue_number is not None:
        r = requests.post(f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments", headers=_gh_headers(), json={"body": body}, timeout=20)
        return {"status": "comment_created", "html_url": r.json().get("html_url")} if r.status_code in (200, 201) else {"error": r.status_code, "message": r.text}
    r = requests.post(f"https://api.github.com/repos/{owner}/{repo}/issues", headers=_gh_headers(), json={"title": title or "New Issue", "body": body}, timeout=20)
    return {"status": "issue_created", "number": r.json().get("number"), "html_url": r.json().get("html_url")} if r.status_code in (200, 201) else {"error": r.status_code, "message": r.text}
