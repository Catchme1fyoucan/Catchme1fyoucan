#!/usr/bin/env python3
"""Refresh data/contributions.json from the GitHub GraphQL API.

Runs inside the scheduled Action (GH_TOKEN is provided by the workflow). It needs
no third-party packages. Output is deterministic so an unchanged calendar produces
an unchanged file.

    GH_TOKEN=... python scripts/fetch_data.py
"""
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = Path(os.environ.get("CONFIG", ROOT / "config.json"))
DATA = Path(os.environ.get("DATA", ROOT / "data" / "contributions.json"))
API = "https://api.github.com/graphql"

CAL_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}"""

REPO_FIELDS = (
    "nameWithOwner description stargazerCount forkCount "
    "primaryLanguage { name color }"
)


def post(token, query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-cards",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as err:
        sys.exit(f"GitHub API returned HTTP {err.code}: {err.read().decode()[:300]}")


def parse_calendar(resp):
    """GraphQL response -> (total, [[iso_date, count], ...]) sorted by date."""
    user = (resp.get("data") or {}).get("user")
    if not user:
        raise ValueError(f"no user in response: {json.dumps(resp)[:300]}")
    cal = user["contributionsCollection"]["contributionCalendar"]
    days = {
        d["date"]: d["contributionCount"]
        for week in cal["weeks"]
        for d in week["contributionDays"]
    }
    return cal["totalContributions"], [[k, days[k]] for k in sorted(days)]


def parse_repos(resp, wanted):
    """Keep only repos GitHub actually returned (missing ones come back null)."""
    out = {}
    for i, full in enumerate(wanted):
        node = (resp.get("data") or {}).get(f"r{i}")
        if not node:
            continue
        lang = node.get("primaryLanguage") or {}
        out[full] = {
            "description": node.get("description") or "",
            "stars": node["stargazerCount"],
            "forks": node["forkCount"],
            "language": lang.get("name", ""),
            "color": lang.get("color") or "#8B8B8B",
        }
    return out


def main():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GH_TOKEN is not set")
    cfg = json.loads(CONFIG.read_text())
    login = cfg["login"]

    today = dt.datetime.now(dt.timezone.utc).date()
    start = today - dt.timedelta(days=364)
    cal = post(
        token,
        CAL_QUERY,
        {
            "login": login,
            "from": f"{start.isoformat()}T00:00:00Z",
            "to": f"{today.isoformat()}T23:59:59Z",
        },
    )
    total, days = parse_calendar(cal)

    wanted = [r["repo"] for r in cfg.get("repos", [])]
    repos = {}
    if wanted:
        aliases = []
        for i, full in enumerate(wanted):
            owner, name = full.split("/", 1)
            aliases.append(
                f'r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) '
                f"{{ {REPO_FIELDS} }}"
            )
        resp = post(token, "query { " + " ".join(aliases) + " }")
        repos = parse_repos(resp, wanted)

    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(
        json.dumps(
            {"login": login, "total": total, "days": days, "repos": repos},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    print(f"{len(days)} days, {total} contributions, {len(repos)}/{len(wanted)} repos found")


if __name__ == "__main__":
    main()
