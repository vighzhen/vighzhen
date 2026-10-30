#!/usr/bin/env python3
"""Build public aggregate statistics, including the owner's private projects."""

import argparse
import collections
import concurrent.futures
import datetime as dt
import html
import json
import pathlib
import subprocess


QUERY = """
query($login:String!,$after:String,$from:DateTime!,$to:DateTime!) {
  user(login:$login) {
    repositories(first:100,after:$after,
      ownerAffiliations:OWNER,orderBy:{field:NAME,direction:ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name isPrivate isFork stargazerCount
        languages(first:100,orderBy:{field:SIZE,direction:DESC}) {
          totalCount totalSize
          edges { size node { name color } }
        }
      }
    }
    contributionsCollection(from:$from,to:$to) {
      totalCommitContributions totalPullRequestContributions
      totalIssueContributions totalPullRequestReviewContributions
      contributionCalendar { totalContributions }
    }
  }
}
"""

COMMIT_FIELDS = """
  pageInfo { hasNextPage endCursor }
  nodes { oid committedDate additions deletions parents(first:1) { totalCount } }
"""

BRANCH_QUERY = """
query($owner:String!,$name:String!,$after:String,$author:ID!,
      $since:GitTimestamp!,$until:GitTimestamp!) {
  repository(owner:$owner,name:$name) {
    refs(refPrefix:"refs/heads/",first:30,after:$after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        target {
          ... on Commit {
            oid
            history(first:100,author:{id:$author},since:$since,until:$until) {
""" + COMMIT_FIELDS + """
            }
          }
        }
      }
    }
  }
}
"""

HISTORY_QUERY = """
query($owner:String!,$name:String!,$oid:GitObjectID!,$after:String,
      $author:ID!,$since:GitTimestamp!,$until:GitTimestamp!) {
  repository(owner:$owner,name:$name) {
    object(oid:$oid) {
      ... on Commit {
        history(first:100,after:$after,author:{id:$author},since:$since,until:$until) {
""" + COMMIT_FIELDS + """
        }
      }
    }
  }
}
"""

CHINA = dt.timezone(dt.timedelta(hours=8))

THEMES = {
    "light": {
        "bg": "#ffffff", "panel": "#f6f8fa", "border": "#d8dee4",
        "text": "#1f2328", "muted": "#59636e", "accent": "#0969da",
        "track": "#eaeef2", "green": "#1a7f37", "red": "#cf222e",
    },
    "dark": {
        "bg": "#0d1117", "panel": "#161b22", "border": "#30363d",
        "text": "#f0f6fc", "muted": "#9da7b3", "accent": "#58a6ff",
        "track": "#21262d", "green": "#3fb950", "red": "#ff7b72",
    },
}


def api(endpoint, payload=None):
    command = ["gh", "api", endpoint]
    if payload is not None:
        command += ["--input", "-"]
    result = subprocess.run(
        command, input=json.dumps(payload) if payload else None,
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    if isinstance(data, dict) and data.get("errors"):
        raise RuntimeError(json.dumps(data["errors"], ensure_ascii=False))
    return data


def repository_commits(login, repo, author_id, since, until):
    variables = {
        "owner": login, "name": repo["name"], "author": author_id,
        "since": since, "until": until, "after": None,
    }
    commits = []
    while True:
        repository = api(
            "graphql", {"query": BRANCH_QUERY, "variables": variables}
        )["data"]["repository"]
        if repository is None:
            raise RuntimeError("An owned repository became inaccessible")
        branches = repository["refs"]
        for branch in branches["nodes"]:
            target = branch["target"]
            if "history" not in target:
                continue
            history = target["history"]
            commits.extend(history["nodes"])
            while history["pageInfo"]["hasNextPage"]:
                history_variables = {
                    **variables, "oid": target["oid"],
                    "after": history["pageInfo"]["endCursor"],
                }
                obj = api(
                    "graphql", {"query": HISTORY_QUERY, "variables": history_variables}
                )["data"]["repository"]["object"]
                if obj is None:
                    raise RuntimeError("A branch changed during statistics collection")
                history = obj["history"]
                commits.extend(history["nodes"])
        if not branches["pageInfo"]["hasNextPage"]:
            return commits
        variables["after"] = branches["pageInfo"]["endCursor"]


def aggregate_daily(commits, now):
    """Deduplicate branch histories and bucket commit time into UTC+8 days."""
    today = now.astimezone(CHINA).date()
    first_day = today - dt.timedelta(days=29)
    rows = {
        (first_day + dt.timedelta(days=index)).isoformat(): {
            "date": (first_day + dt.timedelta(days=index)).isoformat(),
            "commits": 0, "additions": 0, "deletions": 0,
        }
        for index in range(30)
    }
    seen = set()
    for commit in commits:
        if commit["oid"] in seen:
            continue
        seen.add(commit["oid"])
        timestamp = dt.datetime.fromisoformat(commit["committedDate"].replace("Z", "+00:00"))
        date = timestamp.astimezone(CHINA).date().isoformat()
        if date not in rows or timestamp > now:
            continue
        row = rows[date]
        row["commits"] += 1
        # Merge diffs repeat changes already attributed to source commits.
        if commit["parents"]["totalCount"] <= 1:
            row["additions"] += commit["additions"]
            row["deletions"] += commit["deletions"]
    activity = list(rows.values())
    totals = {
        key: sum(row[key] for row in activity)
        for key in ("commits", "additions", "deletions")
    }
    return {
        "activity_timezone": "Asia/Shanghai",
        "daily_activity": activity,
        "today": dict(activity[-1]),
        "last_30_days": {**totals, "active_days": sum(row["commits"] > 0 for row in activity)},
    }


def collect(login):
    now = dt.datetime.now(dt.timezone.utc)
    profile = api("user")
    if profile["login"].lower() != login.lower():
        raise RuntimeError("Statistics must use the repository owner's personal credential")
    variables = {
        "login": login, "after": None,
        "from": f"{now.year}-01-01T00:00:00Z",
        "to": now.isoformat().replace("+00:00", "Z"),
    }
    repositories = []
    contributions = None
    while True:
        user = api("graphql", {"query": QUERY, "variables": variables})["data"]["user"]
        if user is None:
            raise RuntimeError(f"GitHub user not found: {login}")
        contributions = contributions or user["contributionsCollection"]
        connection = user["repositories"]
        repositories.extend(connection["nodes"])
        if not connection["pageInfo"]["hasNextPage"]:
            break
        variables["after"] = connection["pageInfo"]["endCursor"]

    owned = [repo for repo in repositories if not repo["isFork"]]
    private_count = sum(repo["isPrivate"] for repo in repositories)
    expected_private = profile.get("owned_private_repos")
    if expected_private is not None and private_count < expected_private:
        raise RuntimeError("The credential cannot access every owned private repository")
    languages = collections.Counter()
    colors = {}
    for repo in owned:
        # The profile's own automation must not change the language breakdown.
        if repo["name"].lower() == login.lower():
            continue
        connection = repo["languages"]
        if connection["totalCount"] > len(connection["edges"]):
            raise RuntimeError("Incomplete repository language data; nothing will be published")
        if sum(edge["size"] for edge in connection["edges"]) != connection["totalSize"]:
            raise RuntimeError("Inconsistent repository language data; nothing will be published")
        for edge in connection["edges"]:
            name = edge["node"]["name"]
            languages[name] += edge["size"]
            colors[name] = edge["node"]["color"] or "#8b949e"

    total_bytes = sum(languages.values())
    entries = [
        {"name": name, "bytes": size, "percent": size / total_bytes * 100,
         "color": colors[name]}
        for name, size in sorted(languages.items(), key=lambda item: (-item[1], item[0]))
    ]
    first_day = now.astimezone(CHINA).date() - dt.timedelta(days=29)
    since = dt.datetime.combine(first_day, dt.time.min, CHINA).isoformat()
    until = now.isoformat()
    # Read every owned repository and every branch, including private and Fork
    # projects. The profile repository's publishing commits are excluded.
    activity_repos = [repo for repo in repositories if repo["name"].lower() != login.lower()]
    commits = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(repository_commits, login, repo, profile["node_id"], since, until)
            for repo in activity_repos
        ]
        for future in futures:
            try:
                commits.extend(future.result())
            except Exception:
                raise RuntimeError("Could not read complete branch activity; nothing published") from None
    daily = aggregate_daily(commits, now)
    return {
        "login": profile["login"], "year": now.year,
        "updated_at": now.isoformat().replace("+00:00", "Z"),
        "updated_at_china": now.astimezone(CHINA).strftime("%Y-%m-%d %H:%M"),
        "public_repositories": profile["public_repos"],
        "private_repositories": private_count,
        "total_repositories": profile["public_repos"] + private_count,
        "non_fork_repositories": len(owned),
        "stars": sum(repo["stargazerCount"] for repo in owned),
        "followers": profile["followers"],
        "contributions": contributions["contributionCalendar"]["totalContributions"],
        "contribution_visibility": "public-and-private",
        "commits": contributions["totalCommitContributions"],
        "pull_requests": contributions["totalPullRequestContributions"],
        "issues": contributions["totalIssueContributions"],
        "reviews": contributions["totalPullRequestReviewContributions"],
        "language_bytes": total_bytes, "languages": entries,
        **daily,
        "sources": ["https://api.github.com/user",
                    "https://api.github.com/graphql"],
    }


def text(x, y, content, fill, size=12, weight=400, anchor="start"):
    return (
        f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" '
        f'font-weight="{weight}" text-anchor="{anchor}">'
        f'{html.escape(str(content))}</text>'
    )


def rect(x, y, width, height, fill, radius=0, stroke=None):
    border = f' stroke="{stroke}"' if stroke else ""
    return (
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
        f'rx="{radius}" fill="{fill}"{border}/>'
    )


def frame(title, description, body, theme):
    c = THEMES[theme]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="320" '
        'viewBox="0 0 420 320" role="img" aria-labelledby="title description">'
        f"<title id=\"title\">{html.escape(title)}</title>"
        f"<desc id=\"description\">{html.escape(description)}</desc>"
        '<g font-family="-apple-system,BlinkMacSystemFont,Segoe UI,'
        'Noto Sans CJK SC,Microsoft YaHei,Arial,sans-serif">'
        + rect(0.5, 0.5, 419, 319, c["bg"], 16, c["border"])
        + body + "</g></svg>\n"
    )


def footer(data, c):
    return (
        rect(20, 286, 380, 1, c["border"])
        + text(20, 305, data["updated_at_china"], c["muted"], 10)
    )


def overview(data, theme):
    c = THEMES[theme]
    contribution_label = "今年贡献"
    body = text(20, 33, "GitHub 数据概览", c["text"], 18, 700)
    body += text(400, 33, str(data["year"]), c["muted"], 12, anchor="end")
    metrics = [
        ("全部仓库", data["total_repositories"],
         f'公开 {data["public_repositories"]} · 私有 {data["private_repositories"]}'),
        ("仓库星标", data["stars"], ""),
        ("关注者", data["followers"], ""),
        (contribution_label, data["contributions"], ""),
    ]
    for index, (label, value, detail) in enumerate(metrics):
        x, y = 20 + (index % 2) * 196, 56 + (index // 2) * 96
        body += rect(x, y, 184, 84, c["panel"], 10)
        body += text(x + 14, y + 22, label, c["muted"], 12, 500)
        body += text(x + 14, y + 56, f"{value:,}", c["accent"], 30, 700)
        if detail:
            body += text(x + 14, y + 73, detail, c["muted"], 10)
    for x, label, key in [(20, "年度提交", "commits"), (159, "PR", "pull_requests"),
                          (245, "议题", "issues"), (325, "审查", "reviews")]:
        body += text(x, 265, f'{label} {data[key]:,}', c["muted"], 11)
    body += footer(data, c)
    description = (
        f'{data["total_repositories"]} 个仓库，其中 {data["private_repositories"]} 个私有仓库，'
        f'{data["stars"]} 个非 Fork 仓库星标，'
        f'{data["followers"]} 位关注者，{data["contributions"]} 次{contribution_label}。'
    )
    return frame("Vighzhen 的 GitHub 数据概览", description, body, theme)


def language_card(data, theme):
    c = THEMES[theme]
    body = text(20, 33, "代码语言分布", c["text"], 18, 700)
    visible = [dict(item) for item in data["languages"][:5]]
    remainder = data["languages"][5:]
    if remainder:
        visible.append({
            "name": "其他", "color": "#8b949e",
            "percent": sum(item["percent"] for item in remainder),
        })
    if not visible:
        body += text(20, 115, "暂无可统计的代码语言", c["muted"], 14)
    for index, item in enumerate(visible):
        y = 69 + index * 35
        body += rect(20, y - 9, 8, 8, item["color"], 4)
        body += text(36, y, item["name"], c["text"], 12, 500)
        body += text(400, y, f'{item["percent"]:.1f}%', c["muted"], 12, 500, "end")
        body += rect(20, y + 8, 380, 5, c["track"], 2.5)
        if item["percent"] > 0:
            body += rect(20, y + 8, 380 * item["percent"] / 100, 5, item["color"], 2.5)
    body += footer(data, c)
    return frame("Vighzhen 的代码语言分布", "公开与私有的非 Fork 项目代码字节占比。", body, theme)


def compact(value):
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if value >= 10_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:,}"


def chart_dates(rows, c):
    body = ""
    for index in (0, 14, 29):
        x = 24 + index * 12.8 + 5
        body += text(x, 271, rows[index]["date"][5:].replace("-", "/"),
                     c["muted"], 10, anchor="middle")
    return body


def commit_card(data, theme):
    c = THEMES[theme]
    today, total, rows = data["today"], data["last_30_days"], data["daily_activity"]
    body = text(20, 33, "每日提交", c["text"], 18, 700)
    body += text(20, 63, "今日", c["muted"], 12)
    body += text(20, 102, f'{today["commits"]:,}', c["accent"], 36, 700)
    body += text(216, 63, "近 30 天", c["muted"], 12)
    body += text(216, 102, f'{total["commits"]:,}', c["text"], 30, 700)
    body += text(20, 133, f'活跃 {total["active_days"]} 天', c["muted"], 11)
    peak = max(1, max(row["commits"] for row in rows))
    for index, row in enumerate(rows):
        x, height = 24 + index * 12.8, row["commits"] / peak * 98
        body += rect(x, 154, 9, 98, c["panel"], 2)
        if row["commits"]:
            body += (
                f'<g><title>{row["date"]}：{row["commits"]} 次提交</title>'
                + rect(x, 252 - height, 9, height, c["accent"], 2) + "</g>"
            )
    body += chart_dates(rows, c) + footer(data, c)
    return frame("Vighzhen 的每日提交",
                 f'今日 {today["commits"]} 次，近 30 天 {total["commits"]} 次提交。',
                 body, theme)


def code_card(data, theme):
    c = THEMES[theme]
    today, total, rows = data["today"], data["last_30_days"], data["daily_activity"]
    body = text(20, 33, "每日代码变化", c["text"], 18, 700)
    body += text(400, 33, "行", c["muted"], 12, anchor="end")
    body += text(20, 63, "今日新增", c["muted"], 12)
    body += text(216, 63, "今日删除", c["muted"], 12)
    body += text(20, 101, f'+{compact(today["additions"])}', c["green"], 30, 700)
    body += text(216, 101, f'−{compact(today["deletions"])}', c["red"], 30, 700)
    body += text(20, 132, "近 30 天", c["muted"], 11)
    body += text(90, 132, f'+{compact(total["additions"])}', c["green"], 12, 600)
    body += text(216, 132, f'−{compact(total["deletions"])}', c["red"], 12, 600)
    peak = max(1, max(max(row["additions"], row["deletions"]) for row in rows))
    body += rect(20, 203, 380, 1, c["border"])
    for index, row in enumerate(rows):
        x = 24 + index * 12.8
        additions, deletions = row["additions"] / peak * 49, row["deletions"] / peak * 49
        body += f'<g><title>{row["date"]}：新增 {row["additions"]} 行，删除 {row["deletions"]} 行</title>'
        if row["additions"]:
            body += rect(x, 203 - max(1, additions), 9, max(1, additions), c["green"], 1)
        if row["deletions"]:
            body += rect(x, 204, 9, max(1, deletions), c["red"], 1)
        body += "</g>"
    body += chart_dates(rows, c) + footer(data, c)
    return frame("Vighzhen 的每日代码变化",
                 f'今日新增 {today["additions"]} 行，删除 {today["deletions"]} 行。',
                 body, theme)


def page(data, cards):
    stamp = html.escape(data["updated_at_china"])
    login = html.escape(data["login"])
    def embedded(name, theme):
        prefix = f"{name}-{theme}"
        return (cards[prefix].replace('id="title"', f'id="{prefix}-title"')
                .replace('id="description"', f'id="{prefix}-description"')
                .replace('aria-labelledby="title description"',
                         f'aria-labelledby="{prefix}-title {prefix}-description"'))

    panels = "".join(
        f'<div class="card"><div class="light">{embedded(name, "light")}</div>'
        f'<div class="dark">{embedded(name, "dark")}</div></div>'
        for name in ("overview", "languages", "commits", "code")
    )
    rows = "".join(
        f'<tr><th scope="row">{row["date"]}</th><td>{row["commits"]:,}</td>'
        f'<td class="added">+{row["additions"]:,}</td>'
        f'<td class="removed">−{row["deletions"]:,}</td></tr>'
        for row in reversed(data["daily_activity"])
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{login} · GitHub 统计</title>
<style>
:root{{color-scheme:light dark;--bg:#f6f8fa;--panel:#fff;--border:#d8dee4;
--text:#1f2328;--muted:#59636e;--accent:#0969da;--green:#1a7f37;--red:#cf222e}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);
font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}}
main{{max-width:920px;margin:auto;padding:48px 28px 32px}}
.eyebrow{{color:var(--accent);font-size:12px;font-weight:700;letter-spacing:.12em}}
h1{{font-size:36px;line-height:1.2;letter-spacing:-.03em;margin:12px 0}}
p{{color:var(--muted)}}.cards{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:28px 0}}
svg{{display:block;width:100%;height:auto}}.dark{{display:none}}
table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;background:var(--panel)}}
th,td{{padding:11px 18px;border-bottom:1px solid var(--border);text-align:right}}
th:first-child{{text-align:left}}thead th{{color:var(--muted);font-size:12px;font-weight:500}}
tbody th{{font-weight:400}}.added{{color:var(--green)}}.removed{{color:var(--red)}}
h2{{font-size:18px;margin-top:36px}}caption{{text-align:left;color:var(--muted);font-size:12px;padding-bottom:12px}}
nav{{display:flex;gap:20px;flex-wrap:wrap}}a{{color:var(--accent);text-underline-offset:4px}}
a:focus-visible{{outline:2px solid var(--accent);outline-offset:4px}}
footer{{font-size:12px;color:var(--muted);margin-top:30px}}
@media(prefers-color-scheme:dark){{:root{{--bg:#010409;--panel:#0d1117;--border:#30363d;
--text:#f0f6fc;--muted:#9da7b3;--accent:#58a6ff;--green:#3fb950;--red:#ff7b72}}
.light{{display:none}}.dark{{display:block}}}}
@media(max-width:660px){{main{{padding:32px 16px}}h1{{font-size:28px}}.cards{{grid-template-columns:1fr}}}}
@media(max-width:420px){{th,td{{padding:10px 6px;font-size:12px}}}}
</style></head><body><main>
<div class="eyebrow">VIGHZHEN / GITHUB</div>
<h1>开发足迹</h1>
<p>{stamp} · UTC+8</p>
<div class="cards">{panels}</div>
<nav><a href="https://github.com/{login}">GitHub 主页</a></nav>
<h2>每日明细</h2>
<table><caption>近 30 天 · UTC+8</caption>
<thead><tr><th scope="col">日期</th><th scope="col">提交</th>
<th scope="col">新增行</th><th scope="col">删除行</th></tr></thead>
<tbody>{rows}</tbody></table>
<footer>© {data["year"]} {login}</footer>
</main></body></html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", required=True)
    parser.add_argument("--output", default="_site")
    args = parser.parse_args()
    data = collect(args.login)
    output = pathlib.Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    cards = {}
    for theme in THEMES:
        cards[f"overview-{theme}"] = overview(data, theme)
        cards[f"languages-{theme}"] = language_card(data, theme)
        cards[f"commits-{theme}"] = commit_card(data, theme)
        cards[f"code-{theme}"] = code_card(data, theme)
    for name, content in cards.items():
        (output / f"{name}.svg").write_text(content, encoding="utf-8")
    (output / "stats.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "index.html").write_text(page(data, cards), encoding="utf-8")
    print(f'Built statistics for {data["login"]} at {data["updated_at"]}')


if __name__ == "__main__":
    main()
