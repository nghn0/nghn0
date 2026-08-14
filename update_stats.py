import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request

USER = "nghn0"
TOKEN = os.getenv("GH_TOKEN", "")

API = "https://api.github.com"
GRAPHQL = "https://api.github.com/graphql"

try:
    with urllib.request.urlopen(API, timeout=5):
        pass
    CTX = None
except Exception:
    CTX = ssl._create_unverified_context()


def _open(req):
    kwargs = {"context": CTX} if CTX else {}
    for attempt in range(5):
        try:
            return urllib.request.urlopen(req, **kwargs)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and attempt < 4:
                wait = int(e.headers.get("Retry-After") or 0)
                if not wait:
                    wait = 30 * (attempt + 1)
                print(f"Rate limited ({e.code}), waiting {wait}s...")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("Gave up after repeated rate limits")


def api(path, token=TOKEN):
    req = urllib.request.Request(API + path)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", USER)
    with _open(req) as resp:
        return json.loads(resp.read().decode())


def gql(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(GRAPHQL, data=body, method="POST")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", USER)
    with _open(req) as resp:
        return json.loads(resp.read().decode())


def fetch_all(path):
    results, page = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        data = api(f"{path}{sep}per_page=100&page={page}")
        if not data:
            break
        results.extend(data)
        if len(data) < 100:
            break
        page += 1
    return results


COMMITS_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      target {
        ... on Commit {
          history { totalCount }
        }
      }
    }
  }
}
"""


def commits_graphql(repos):
    total = 0
    for i, repo in enumerate(repos):
        data = gql(COMMITS_QUERY, {"owner": USER, "name": repo["name"]})
        errors = data.get("errors")
        if errors:
            print(f"  {repo['name']}: skipped ({errors[0]['message']})")
            continue
        target = (data.get("data", {}).get("repository", {}).get("defaultBranchRef", {}) or {}).get("target") or {}
        total += target.get("history", {}).get("totalCount", 0)
        if i and i % 10 == 0:
            time.sleep(1)
    return total


def commits_events_fallback():
    total = 0
    for page in range(1, 6):
        events = api(f"/users/{USER}/events/public?per_page=100&page={page}")
        if not events:
            break
        total += sum(len(e.get("payload", {}).get("commits", [])) for e in events if e.get("type") == "PushEvent")
    return total


def get_stats():
    user = api("/users/" + USER)
    repos = fetch_all("/users/" + USER + "/repos")
    prs = api("/search/issues?q=author:" + USER + "+type:pr")

    stars = sum(r.get("stargazers_count") or 0 for r in repos)

    if TOKEN:
        print("Fetching lifetime commit counts via GraphQL...")
        commits = commits_graphql(repos)
    else:
        print("No GH_TOKEN — using REST events feed (last ~90 days) as fallback.")
        commits = commits_events_fallback()

    return {
        "stars": stars,
        "commits": commits,
        "prs": prs.get("total_count", 0),
        "repos": user.get("public_repos", 0),
    }


def fmt(n):
    return f"{n:,}"


def update_svg(stats):
    with open("assets/stats-card.svg", encoding="utf-8") as f:
        svg = f.read()

    label_number_map = [
        ("Total Stars Earned", "stars"),
        ("Total Commits", "commits"),
        ("Total Pull Requests", "prs"),
        ("Total Repositories", "repos"),
    ]

    lines = svg.split("\n")
    for i, line in enumerate(lines):
        for label, key in label_number_map:
            if label in line and i + 1 < len(lines):
                lines[i + 1] = re.sub(r">[^<]*</text>", f">{fmt(stats[key])}</text>", lines[i + 1], count=1)
                break

    with open("assets/stats-card.svg", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print("Updated:", {k: fmt(v) for k, v in stats.items()})


if __name__ == "__main__":
    update_svg(get_stats())
