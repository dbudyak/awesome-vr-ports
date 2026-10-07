#!/usr/bin/env python3
"""Mirror a GitHub star list into README.md.

Reads a user's star list (github.com/stars/<user>/lists/<slug>) through the
GraphQL API and rewrites the section of README.md between the
<!-- LIST:START --> and <!-- LIST:END --> markers. Everything outside the
markers is left untouched, so the intro and footer can be edited by hand.

Environment:
  GITHUB_TOKEN  token used for the API (required). To mirror a *private*
                list, this must be a personal token of the list owner.
  LIST_OWNER    GitHub login that owns the list (required).
  LIST_NAME     list name or slug; matching ignores case. If unset, the
                first list whose name contains "vr" is used.
  README_PATH   file to update (default: README.md).
"""

import json
import os
import re
import sys
import urllib.request

API = "https://api.github.com/graphql"
START, END = "<!-- LIST:START -->", "<!-- LIST:END -->"

LISTS_QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    lists(first: 50, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { id name slug description }
    }
  }
}
"""

ITEMS_QUERY = """
query($id: ID!, $after: String) {
  node(id: $id) {
    ... on UserList {
      items(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          ... on Repository {
            nameWithOwner
            url
            description
            stargazerCount
            isArchived
            pushedAt
          }
        }
      }
    }
  }
}
"""


def graphql(token, query, variables):
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        body = json.load(resp)
    if body.get("errors"):
        sys.exit(f"GraphQL error: {json.dumps(body['errors'], indent=2)}")
    return body["data"]


def paginate(token, query, variables, extract):
    after = None
    while True:
        conn = extract(graphql(token, query, {**variables, "after": after}))
        yield from conn["nodes"]
        if not conn["pageInfo"]["hasNextPage"]:
            return
        after = conn["pageInfo"]["endCursor"]


def find_list(token, owner, wanted):
    def extract(data):
        if not data["user"]:
            sys.exit(f"User {owner!r} not found")
        return data["user"]["lists"]

    lists = list(paginate(token, LISTS_QUERY, {"login": owner}, extract))
    for lst in lists:
        if wanted:
            if wanted.lower() in (lst["name"].lower(), lst["slug"].lower()):
                return lst
        elif re.search(r"\bvr\b", lst["name"], re.IGNORECASE):
            return lst
    names = ", ".join(repr(l["name"]) for l in lists) or "none visible"
    sys.exit(f"List {wanted or '(matching VR)'} not found for {owner}. Lists: {names}")


def fetch_repos(token, list_id):
    extract = lambda data: data["node"]["items"]
    # Non-repository items come back as empty objects; drop them.
    return [r for r in paginate(token, ITEMS_QUERY, {"id": list_id}, extract) if r]


def fmt_stars(n):
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def render(lst, repos):
    lines = [f"_{len(repos)} projects, mirrored from the "
             f"[{lst['name']}](https://github.com/stars/{os.environ['LIST_OWNER']}/lists/{lst['slug']}) star list._", ""]
    for r in sorted(repos, key=lambda r: -r["stargazerCount"]):
        desc = (r["description"] or "").strip().replace("\n", " ")
        badges = f" ⭐ {fmt_stars(r['stargazerCount'])}"
        if r["isArchived"]:
            badges += " · 🗄️ archived"
        entry = f"- [{r['nameWithOwner']}]({r['url']})"
        lines.append(f"{entry} — {desc}{badges}" if desc else f"{entry}{badges}")
    return "\n".join(lines) + "\n"


def main():
    token = os.environ.get("GITHUB_TOKEN") or sys.exit("GITHUB_TOKEN is not set")
    owner = os.environ.get("LIST_OWNER") or sys.exit("LIST_OWNER is not set")
    path = os.environ.get("README_PATH", "README.md")

    lst = find_list(token, owner, os.environ.get("LIST_NAME", "").strip())
    repos = fetch_repos(token, lst["id"])

    with open(path, encoding="utf-8") as f:
        readme = f.read()
    if START not in readme or END not in readme:
        sys.exit(f"{path} is missing the {START} / {END} markers")

    head, rest = readme.split(START, 1)
    _, tail = rest.split(END, 1)
    updated = f"{head}{START}\n{render(lst, repos)}{END}{tail}"

    if updated != readme:
        with open(path, "w", encoding="utf-8") as f:
            f.write(updated)
    print(f"Synced {len(repos)} repos from list {lst['name']!r}"
          f"{'' if updated != readme else ' (no changes)'}")


if __name__ == "__main__":
    main()
