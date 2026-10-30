"""PyPI + GitHub maintenance probe. Writes JSON to stdout for the report."""
import json, urllib.request

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "sim-survey", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")

out = {}
for repo in ["LucasAlegre/sumo-rl", "Pi-Star-Lab/RESCO", "cityflow-project/CityFlow",
             "metadriverse/metadrive", "eclipse-mosaic/mosaic", "a-b-street/abstreet",
             "Farama-Foundation/PettingZoo", "eclipse-sumo/sumo", "flow-project/flow"]:
    d = json.loads(get("https://api.github.com/repos/" + repo))
    out[repo] = {
        "stars": d["stargazers_count"], "pushed_at": d["pushed_at"], "archived": d["archived"],
        "license": (d.get("license") or {}).get("spdx_id"), "open_issues_and_prs": d["open_issues_count"],
        "default_branch": d["default_branch"],
    }
    c = json.loads(get(f"https://api.github.com/repos/{repo}/commits?per_page=1"))
    out[repo]["last_commit"] = c[0]["commit"]["committer"]["date"] if c else None
    out[repo]["last_commit_sha"] = c[0]["sha"][:8] if c else None
    iss = get(f"https://api.github.com/search/issues?q=repo:{repo}+is:issue")
    out[repo]["issues_total"] = json.loads(iss)["total_count"]
    iss = get(f"https://api.github.com/search/issues?q=repo:{repo}+is:issue+is:open")
    out[repo]["issues_open"] = json.loads(iss)["total_count"]
    iss = get(f"https://api.github.com/search/issues?q=repo:{repo}+is:pr+is:open")
    out[repo]["prs_open"] = json.loads(iss)["total_count"]

for pkg in ["cityflow", "metadrive-simulator", "pettingzoo", "sumo-rl", "eclipse-sumo", "traci",
            "libsumo", "abstreet", "eclipse-mosaic"]:
    try:
        d = json.loads(get(f"https://pypi.org/pypi/{pkg}/json"))
    except Exception as e:
        out["pypi:" + pkg] = {"exists": False, "err": str(e)[:60]}
        continue
    files = d["releases"].get(d["info"]["version"], [])
    out["pypi:" + pkg] = {"exists": True, "latest": d["info"]["version"],
                          "requires_python": d["info"].get("requires_python"),
                          "uploaded": files[0]["upload_time"] if files else None,
                          "wheels": [f["filename"] for f in files if f["filename"].endswith(".whl")][:4],
                          "sdist_only": bool(files) and not any(f["filename"].endswith(".whl") for f in files)}
print(json.dumps(out, indent=1))