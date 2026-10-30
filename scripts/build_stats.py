#!/usr/bin/env python3
"""Build public aggregate statistics, including the owner's private projects."""

import argparse
import collections
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

THEMES = {
    "light": {
        "bg": "#ffffff", "panel": "#f6f8fa", "border": "#d8dee4",
        "text": "#1f2328", "muted": "#59636e", "accent": "#0969da",
        "track": "#eaeef2", "green": "#1a7f37",
    },
    "dark": {
        "bg": "#0d1117", "panel": "#161b22", "border": "#30363d",
        "text": "#f0f6fc", "muted": "#9da7b3", "accent": "#58a6ff",
        "track": "#21262d", "green": "#3fb950",
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
    return {
        "login": profile["login"], "year": now.year,
        "updated_at": now.isoformat().replace("+00:00", "Z"),
        "updated_at_china": now.astimezone(
            dt.timezone(dt.timedelta(hours=8))
        ).strftime("%Y-%m-%d %H:%M"),
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
        '<svg xmlns="http://www.w3.org/2000/svg" width="420" height="380" '
        'viewBox="0 0 420 380" role="img" aria-labelledby="title description">'
        f"<title id=\"title\">{html.escape(title)}</title>"
        f"<desc id=\"description\">{html.escape(description)}</desc>"
        '<g font-family="-apple-system,BlinkMacSystemFont,Segoe UI,'
        'Noto Sans CJK SC,Microsoft YaHei,Arial,sans-serif">'
        + rect(0.5, 0.5, 419, 379, c["bg"], 16, c["border"])
        + body + "</g></svg>\n"
    )


def footer(data, c):
    return (
        rect(20, 331, 380, 1, c["border"])
        + text(20, 352, f'更新于 {data["updated_at_china"]} · UTC+8', c["muted"], 10.5)
        + text(20, 369, "计划每 5 分钟取数 · 展示可能受缓存延迟影响", c["muted"], 10)
    )


def overview(data, theme):
    c = THEMES[theme]
    contribution_label = "今年贡献"
    body = text(20, 33, "GitHub 数据概览", c["text"], 18, 700)
    body += text(20, 56, f'@{data["login"]} · {data["year"]} · 包含私有项目', c["muted"], 12)
    body += rect(319, 20, 81, 24, c["panel"], 12)
    body += text(359.5, 36, "官方 API", c["accent"], 10.5, 600, "middle")
    metrics = [
        ("全部仓库", data["total_repositories"],
         f'公开 {data["public_repositories"]} · 私有 {data["private_repositories"]}'),
        ("仓库星标", data["stars"], "非 Fork 仓库 · 包含私有"),
        ("关注者", data["followers"], "GitHub Followers"),
        (contribution_label, data["contributions"], "包含公开与私有活动"),
    ]
    for index, (label, value, detail) in enumerate(metrics):
        x, y = 20 + (index % 2) * 196, 77 + (index // 2) * 100
        body += rect(x, y, 184, 90, c["panel"], 10)
        body += text(x + 14, y + 23, label, c["muted"], 12, 500)
        body += text(x + 14, y + 58, f"{value:,}", c["accent"], 30, 700)
        body += text(x + 14, y + 77, detail, c["muted"], 10)
    body += text(20, 290, contribution_label + "明细", c["text"], 12, 600)
    for x, label, key in [(20, "提交", "commits"), (151, "PR", "pull_requests"),
                          (278, "Issue", "issues")]:
        body += text(x, 314, f'{label}  {data[key]:,}', c["muted"], 12)
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
    body += text(20, 56, "代码字节占比 · 非 Fork 项目 · 包含私有", c["muted"], 12)
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
        y = 88 + index * 35
        body += rect(20, y - 9, 8, 8, item["color"], 4)
        body += text(36, y, item["name"], c["text"], 12, 500)
        body += text(400, y, f'{item["percent"]:.1f}%', c["muted"], 12, 500, "end")
        body += rect(20, y + 8, 380, 5, c["track"], 2.5)
        if item["percent"] > 0:
            body += rect(20, y + 8, 380 * item["percent"] / 100, 5, item["color"], 2.5)
    body += text(20, 313, "私有项目只展示汇总，不显示仓库名称或源码。", c["muted"], 10)
    body += footer(data, c)
    return frame("Vighzhen 的代码语言分布", "公开与私有的非 Fork 项目代码字节占比。", body, theme)


def page(data, cards):
    stamp = html.escape(data["updated_at_china"])
    login = html.escape(data["login"])
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{login} · GitHub 统计</title>
<style>
:root{{color-scheme:light dark;--bg:#f6f8fa;--text:#1f2328;--muted:#59636e;--accent:#0969da}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);
font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}}
main{{max-width:920px;margin:auto;padding:64px 28px 32px}}
.eyebrow{{color:var(--accent);font-size:12px;font-weight:700;letter-spacing:.12em}}
h1{{font-size:36px;line-height:1.2;letter-spacing:-.03em;margin:12px 0}}
p{{color:var(--muted)}}.cards{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:28px 0}}
svg{{display:block;width:100%;height:auto}}.dark{{display:none}}
nav{{display:flex;gap:20px;flex-wrap:wrap}}a{{color:var(--accent);text-underline-offset:4px}}
footer{{font-size:12px;color:var(--muted);margin-top:30px}}
@media(prefers-color-scheme:dark){{:root{{--bg:#010409;--text:#f0f6fc;--muted:#9da7b3;--accent:#58a6ff}}
.light{{display:none}}.dark{{display:block}}}}
@media(max-width:660px){{main{{padding:32px 16px}}h1{{font-size:28px}}.cards{{grid-template-columns:1fr}}}}
</style></head><body><main>
<div class="eyebrow">VIGHZHEN / GITHUB</div>
<h1>让数据说得更清楚。</h1>
<p>最后取数：{stamp}（UTC+8）。计划每 5 分钟自动更新，任务排队和缓存可能延迟展示。</p>
<div class="cards">
<div><div class="light">{cards["overview-light"]}</div><div class="dark">{cards["overview-dark"]}</div></div>
<div><div class="light">{cards["languages-light"]}</div><div class="dark">{cards["languages-dark"]}</div></div>
</div>
<nav><a href="https://github.com/{login}">返回 GitHub 主页</a>
<a href="https://github.com/{login}/{login}/actions/workflows/profile-stats.yml">手动更新</a>
<a href="https://github.com/{login}/{login}/blob/main/STATS.md">查看统计口径</a></nav>
<footer>统计包含公开与私有项目，只展示汇总数字。语言占比按代码字节计算，不表示技能熟练程度。</footer>
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
    for name, content in cards.items():
        (output / f"{name}.svg").write_text(content, encoding="utf-8")
    (output / "stats.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "index.html").write_text(page(data, cards), encoding="utf-8")
    print(f'Built statistics for {data["login"]} at {data["updated_at"]}')


if __name__ == "__main__":
    main()
