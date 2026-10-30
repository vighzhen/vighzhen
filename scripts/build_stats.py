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
        "calendar": ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"],
    },
    "dark": {
        "bg": "#0d1117", "panel": "#161b22", "border": "#30363d",
        "text": "#f0f6fc", "muted": "#9da7b3", "accent": "#58a6ff",
        "track": "#21262d", "green": "#3fb950", "red": "#ff7b72",
        "calendar": ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"],
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


def frame(title, description, body, theme, width=420, height=320):
    c = THEMES[theme]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">'
        f"<title id=\"title\">{html.escape(title)}</title>"
        f"<desc id=\"description\">{html.escape(description)}</desc>"
        '<g font-family="-apple-system,BlinkMacSystemFont,Segoe UI,'
        'Noto Sans CJK SC,Microsoft YaHei,Arial,sans-serif">'
        + rect(0.5, 0.5, width - 1, height - 1, c["bg"], 12, c["border"])
        + body + "</g></svg>\n"
    )


def compact(value):
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if value >= 10_000:
        return f"{value / 1_000:.1f}k"
    return f"{value:,}"


def visible_languages(data):
    visible = [dict(item) for item in data["languages"][:5]]
    remainder = data["languages"][5:]
    if remainder:
        visible.append({
            "name": "其他", "color": "#8b949e",
            "percent": sum(item["percent"] for item in remainder),
        })
    return visible


def activity_calendar(data, c, x, y, step=20, size=15):
    rows = data["daily_activity"]
    first = dt.date.fromisoformat(rows[0]["date"])
    start = first - dt.timedelta(days=first.weekday())
    peak = max(1, max(row["commits"] for row in rows))
    palette = c["calendar"]
    body = ""
    for weekday, label in ((0, "一"), (2, "三"), (4, "五")):
        body += text(x - 10, y + weekday * step + size - 3,
                     label, c["muted"], 10, anchor="end")
    for row in rows:
        date = dt.date.fromisoformat(row["date"])
        offset = (date - start).days
        count = row["commits"]
        level = 0 if not count else min(4, max(1, (count * 4 + peak - 1) // peak))
        bx, by = x + offset // 7 * step, y + date.weekday() * step
        body += (
            f'<g data-date="{row["date"]}" data-commits="{count}">'
            f'<title>{row["date"]} · {count} 次提交 · '
            f'新增 {row["additions"]} 行 · 删除 {row["deletions"]} 行</title>'
            + rect(bx, by, size, size, palette[level], 3) + "</g>"
        )
    return body


def activity_card(data, theme, mobile=False):
    c = THEMES[theme]
    today, total = data["today"], data["last_30_days"]
    width, height = (420, 472) if mobile else (840, 342)
    margin = 24 if mobile else 28
    body = text(margin, 36, "开发记录", c["text"], 18, 600)
    body += text(width - margin, 35, data["updated_at_china"],
                 c["muted"], 10, anchor="end")
    positions = (24, 158, 292) if mobile else (28, 206, 396)
    for x, label, value, color in zip(
        positions, ("今日提交", "今日新增", "今日删除"),
        (f'{today["commits"]:,}', f'+{compact(today["additions"])}',
         f'−{compact(today["deletions"])}'),
        (c["text"], c["green"], c["red"]),
    ):
        body += text(x, 77, label, c["muted"], 12)
        body += text(x, 119, value, color, 30 if mobile else 36, 600)

    body += rect(margin, 143, 372 if mobile else 542, 1, c["border"])
    body += text(margin, 166, "近 30 天", c["muted"], 11)
    positions = (24, 123, 222, 321) if mobile else (28, 174, 322, 476)
    for x, label, value, color in zip(
        positions, ("提交", "新增行", "删除行", "活跃天"),
        (f'{total["commits"]:,}', f'+{compact(total["additions"])}',
         f'−{compact(total["deletions"])}', str(total["active_days"])),
        (c["text"], c["green"], c["red"], c["text"]),
    ):
        body += text(x, 190, label, c["muted"], 11)
        body += text(x, 218, value, color, 21 if mobile else 24, 500)

    first, last = data["daily_activity"][0]["date"][5:], today["date"][5:]
    if mobile:
        body += rect(24, 238, 372, 1, c["border"])
        body += text(24, 262, "提交日历", c["muted"], 11)
        body += text(182, 262, f"{first} — {last}", c["muted"], 10, anchor="end")
        body += activity_calendar(data, c, 44, 275, 18, 13)
        for index, (label, value) in enumerate((
            ("仓库", data["total_repositories"]), ("星标", data["stars"]),
            ("关注者", data["followers"]), ("今年贡献", data["contributions"]),
        )):
            x, y = 220 + index % 2 * 95, 288 + index // 2 * 56
            body += text(x, y, label, c["muted"], 10)
            body += text(x, y + 24, f"{value:,}", c["text"], 21, 500)
        language_y = 402
        body += rect(24, 380, 372, 1, c["border"])
    else:
        body += rect(604, 66, 1, 159, c["border"])
        body += text(640, 75, "提交日历", c["muted"], 12)
        body += activity_calendar(data, c, 658, 90, 18, 13)
        body += text(640, 239, f"{first} — {last}", c["muted"], 10)
        body += text(28, 258,
                     f'{data["total_repositories"]} 仓库'
                     f'（公开 {data["public_repositories"]} / 私有 {data["private_repositories"]}）'
                     f'    ·    {data["stars"]} 星标'
                     f'    ·    {data["followers"]} 关注者'
                     f'    ·    {data["contributions"]} 今年贡献',
                     c["muted"], 12)
        body += rect(28, 276, 784, 1, c["border"])
        language_y = 300

    for index, item in enumerate(visible_languages(data)):
        columns = 3 if mobile else 6
        x = margin + index % columns * (130 if mobile else 133)
        y = language_y + index // columns * 38
        body += rect(x, y - 7, 7, 7, item["color"], 3.5)
        body += text(x + 13, y, item["name"], c["muted"], 10.5)
        body += text(x + 13, y + 18, f'{item["percent"]:.1f}%', c["text"], 13, 500)
    if not data["languages"]:
        body += text(margin, language_y, "暂无代码语言数据", c["muted"], 12)
    return frame(
        "Vighzhen 的开发记录",
        f'今日 {today["commits"]} 次提交，新增 {today["additions"]} 行，'
        f'删除 {today["deletions"]} 行；近 30 天 {total["commits"]} 次提交。'
        "包含公开、私有项目与开发分支。",
        body, theme, width, height,
    )


def trend_card(data, theme, mobile=False):
    c = THEMES[theme]
    rows = data["daily_activity"]
    body = text(24, 32, "每日趋势", c["text"], 16, 600)
    for index, (x, title, keys, colors) in enumerate((
        (24, "提交次数", ("commits",), (c["accent"],)),
        (450, "代码变化 · 行", ("additions", "deletions"), (c["green"], c["red"])),
    )):
        shift = index * 202 if mobile else 0
        x = 24 if mobile else x
        body += text(x, 65 + shift, title, c["muted"], 12)
        peak = max(1, max(row[key] for row in rows for key in keys))
        body += text(x + 358, 65 + shift, compact(peak), c["muted"], 10, anchor="end")
        for grid_y in (90, 146, 202):
            body += rect(x, grid_y + shift, 358, 1, c["border"])
        for key, color in zip(keys, colors):
            points = [(x + index * 358 / 29, 202 + shift - row[key] / peak * 104)
                      for index, row in enumerate(rows)]
            path = " ".join(f'{"M" if i == 0 else "L"}{px:.2f},{py:.2f}'
                            for i, (px, py) in enumerate(points))
            body += f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2"/>'
            for row, (px, py) in zip(rows, points):
                label = {"commits": "提交", "additions": "新增行", "deletions": "删除行"}[key]
                body += (f'<circle cx="{px:.2f}" cy="{py:.2f}" r="3" fill="{color}">'
                         f'<title>{row["date"]} · {label} {row[key]:,}</title></circle>')
        if len(keys) == 2:
            body += text(x + 145, 65 + shift, "新增", c["green"], 11)
            body += text(x + 195, 65 + shift, "删除", c["red"], 11)
        body += text(x, 225 + shift, rows[0]["date"][5:], c["muted"], 10)
        body += text(x + 358, 225 + shift, rows[-1]["date"][5:], c["muted"], 10, anchor="end")
    return frame("Vighzhen 的每日开发趋势", "近 30 天提交与代码变化的折线趋势。",
                 body, theme, 420 if mobile else 840, 446 if mobile else 246)

def page(data, cards):
    stamp = html.escape(data["updated_at_china"])
    login = html.escape(data["login"])
    def embedded(name, theme):
        prefix = f"{name}-{theme}"
        return (cards[prefix].replace('id="title"', f'id="{prefix}-title"')
                .replace('id="description"', f'id="{prefix}-description"')
                .replace('aria-labelledby="title description"',
                         f'aria-labelledby="{prefix}-title {prefix}-description"'))

    def responsive_panel(name):
        return (
            f'<div class="desktop"><div class="light">{embedded(name, "light")}</div>'
            f'<div class="dark">{embedded(name, "dark")}</div></div>'
            f'<div class="mobile"><div class="light">{embedded(f"{name}-mobile", "light")}</div>'
            f'<div class="dark">{embedded(f"{name}-mobile", "dark")}</div></div>'
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
:root{{color-scheme:light dark;--bg:#fff;--panel:#fff;--border:#d8dee4;
--text:#1f2328;--muted:#59636e;--accent:#0969da;--green:#1a7f37;--red:#cf222e}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);
font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}}
main{{max-width:920px;margin:auto;padding:48px 28px 32px}}
.eyebrow{{color:var(--muted);font-size:12px;font-weight:500;letter-spacing:.08em}}
h1{{font-size:32px;line-height:1.2;letter-spacing:-.03em;margin:16px 0}}
p{{color:var(--muted)}}.activity{{margin:32px 0 16px}}.annual{{font-size:12px}}
.trend{{margin:28px 0}}svg{{display:block;width:100%;height:auto}}
.dark,.mobile{{display:none}}
table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;background:var(--panel)}}
th,td{{padding:11px 18px;border-bottom:1px solid var(--border);text-align:right}}
th:first-child{{text-align:left}}thead th{{color:var(--muted);font-size:12px;font-weight:500}}
tbody th{{font-weight:400}}.added{{color:var(--green)}}.removed{{color:var(--red)}}
h2{{font-size:18px;margin-top:36px}}caption{{text-align:left;color:var(--muted);font-size:12px;padding-bottom:12px}}
nav{{display:flex;gap:20px;flex-wrap:wrap;margin-top:24px}}a{{color:var(--accent);text-underline-offset:4px}}
a:focus-visible{{outline:2px solid var(--accent);outline-offset:4px}}
footer{{font-size:12px;color:var(--muted);margin-top:30px}}
summary{{cursor:pointer;font-size:16px;font-weight:500;margin:28px 0 16px}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0d1117;--panel:#0d1117;--border:#30363d;
--text:#f0f6fc;--muted:#9da7b3;--accent:#58a6ff;--green:#3fb950;--red:#ff7b72}}
.light{{display:none}}.dark{{display:block}}}}
@media(max-width:767px){{main{{padding:32px 16px}}h1{{font-size:28px}}.desktop{{display:none}}.mobile{{display:block}}}}
@media(max-width:420px){{th,td{{padding:10px 6px;font-size:12px}}}}
</style></head><body><main>
<div class="eyebrow">VIGHZHEN</div>
<h1>开发记录</h1>
<p>{stamp} · UTC+8</p>
<div class="activity">{responsive_panel("activity")}</div>
<p class="annual">{data["year"]} 年度贡献 · 提交 {data["commits"]:,}
 · PR {data["pull_requests"]:,} · 议题 {data["issues"]:,} · 审查 {data["reviews"]:,}
 · 公开 {data["public_repositories"]} / 私有 {data["private_repositories"]}</p>
<div class="trend">{responsive_panel("trends")}</div>
<nav><a href="https://github.com/{login}">GitHub 主页</a></nav>
<details open><summary>每日明细</summary>
<table><caption>近 30 天 · UTC+8</caption>
<thead><tr><th scope="col">日期</th><th scope="col">提交</th>
<th scope="col">新增行</th><th scope="col">删除行</th></tr></thead>
<tbody>{rows}</tbody></table></details>
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
        cards[f"activity-{theme}"] = activity_card(data, theme)
        cards[f"activity-mobile-{theme}"] = activity_card(data, theme, mobile=True)
        cards[f"trends-{theme}"] = trend_card(data, theme)
        cards[f"trends-mobile-{theme}"] = trend_card(data, theme, mobile=True)
    for name, content in cards.items():
        (output / f"{name}.svg").write_text(content, encoding="utf-8")
    (output / "stats.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "index.html").write_text(page(data, cards), encoding="utf-8")
    print(f'Built statistics for {data["login"]} at {data["updated_at"]}')


if __name__ == "__main__":
    main()
