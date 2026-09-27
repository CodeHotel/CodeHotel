#!/usr/bin/env python3
"""Render a GitHub contribution line graph as a static SVG.

A self-hosted replacement for github-readme-activity-graph.vercel.app (its
public deployment was disabled), drawn in that project's dracula theme.
Standard library only, so it runs on a bare GitHub Actions runner.

Data comes from the GraphQL API when GITHUB_TOKEN is set; otherwise from the
public contributions calendar page (used for local previews).

Usage: activity_graph.py --user CodeHotel --out dist/activity-graph.svg
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import urllib.request

WIDTH, HEIGHT = 1200, 420
# Chartist geometry used by the original card: chart padding + axis offsets.
PLOT_LEFT, PLOT_RIGHT = 20 + 70, WIDTH - 50
PLOT_TOP, PLOT_BOTTOM = 80, HEIGHT - 20 - 50

DRACULA = {
    "bg": "#44475a",
    "text": "#f8f8f2",
    "title": "#f8f8f2",
    "line": "#ff79c6",
    "point": "#bd93f9",
}

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    name
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar { weeks { contributionDays { date contributionCount } } }
    }
  }
}
"""


def fetch_graphql(user, token, days):
    now = dt.datetime.now(dt.timezone.utc)
    body = json.dumps({
        "query": QUERY,
        "variables": {
            "login": user,
            "from": (now - dt.timedelta(days=days)).isoformat(),
            "to": now.isoformat(),
        },
    }).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql", data=body,
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    data = json.load(urllib.request.urlopen(req, timeout=30))
    if data.get("errors"):
        raise RuntimeError(data["errors"])
    u = data["data"]["user"]
    weeks = u["contributionsCollection"]["contributionCalendar"]["weeks"]
    counts = [(d["date"], d["contributionCount"]) for w in weeks for d in w["contributionDays"]]
    return u["name"], counts


def fetch_public_calendar(user):
    req = urllib.request.Request(f"https://github.com/users/{user}/contributions",
                                 headers={"User-Agent": "Mozilla/5.0"})
    page = urllib.request.urlopen(req, timeout=30).read().decode()
    dates = dict(re.findall(r'data-date="([\d-]+)" id="([^"]+)"', page))
    tips = {m[0]: m[1] for m in re.findall(r'<tool-tip[^>]*for="([^"]+)"[^>]*>([^<]*)</tool-tip>', page)}
    counts = []
    for date, cell in sorted(dates.items()):
        m = re.match(r"(\d+) contribution", tips.get(cell, ""))
        counts.append((date, int(m.group(1)) if m else 0))
    return None, counts


def y_ticks(peak):
    """Integer ticks from 0, at least ~30 px apart, like Chartist's onlyInteger axis."""
    max_ticks = (PLOT_BOTTOM - PLOT_TOP) // 30
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if peak / step <= max_ticks:
            break
    top = max(step, -(-peak // step) * step)
    return list(range(0, top + 1, step)), top


def smooth_path(points):
    """Monotone cubic (Fritsch-Carlson) path, so the curve never dips below zero."""
    n = len(points)
    if n < 3:
        return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)
    xs, ys = zip(*points)
    d = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(n - 1)]
    m = [d[0]] + [0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2 for i in range(1, n - 1)] + [d[-1]]
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0
        else:
            a, b = m[i] / d[i], m[i + 1] / d[i]
            s = a * a + b * b
            if s > 9:
                t = 3 / s ** 0.5
                m[i], m[i + 1] = t * a * d[i], t * b * d[i]
    path = [f"M{xs[0]:.1f},{ys[0]:.1f}"]
    for i in range(n - 1):
        h = (xs[i + 1] - xs[i]) / 3
        path.append(f"C{xs[i] + h:.1f},{ys[i] + h * m[i]:.1f} "
                    f"{xs[i + 1] - h:.1f},{ys[i + 1] - h * m[i + 1]:.1f} "
                    f"{xs[i + 1]:.1f},{ys[i + 1]:.1f}")
    return " ".join(path)


def render(title, counts, c=DRACULA):
    ticks, top = y_ticks(max(v for _, v in counts))
    step_x = (PLOT_RIGHT - PLOT_LEFT) / (len(counts) - 1)
    to_y = lambda v: PLOT_BOTTOM - v / top * (PLOT_BOTTOM - PLOT_TOP)
    pts = [(PLOT_LEFT + i * step_x, to_y(v)) for i, (_, v) in enumerate(counts)]

    grid = [f'<line x1="{PLOT_LEFT}" x2="{PLOT_RIGHT}" y1="{to_y(t):.1f}" y2="{to_y(t):.1f}"/>' for t in ticks]
    grid += [f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{PLOT_TOP}" y2="{PLOT_BOTTOM}"/>' for x, _ in pts]
    ylabels = [f'<text x="{PLOT_LEFT - 10}" y="{to_y(t) + 4:.1f}" text-anchor="end">{t}</text>' for t in ticks]
    xlabels = [f'<text x="{x:.1f}" y="{PLOT_BOTTOM + 20}" text-anchor="middle">{int(d[8:])}</text>'
               for (x, _), (d, _) in zip(pts, counts)]
    dots = [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5"><title>{d}: {v}</title></circle>'
            for (x, y), (d, v) in zip(pts, counts)]
    mid_y = (PLOT_TOP + PLOT_BOTTOM) / 2

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{html.escape(title)}">
<title>{html.escape(title)}</title>
<style>
  text {{ font: 600 12px 'Segoe UI', Ubuntu, sans-serif; fill: {c["text"]}; }}
  .title {{ font-size: 20px; fill: {c["title"]}; }}
  .axis {{ font-size: 14px; }}
  .grid line {{ stroke: {c["text"]}; stroke-opacity: .3; stroke-dasharray: 2; }}
  .line {{ fill: none; stroke: {c["line"]}; stroke-width: 4; stroke-dasharray: 5000; animation: dash 5s ease-in-out both; }}
  .dots circle {{ fill: {c["point"]}; animation: blink 1s ease-in-out both; }}
  /* Animate from hidden, so renderers without CSS animation still show the finished graph. */
  @keyframes dash {{ from {{ stroke-dashoffset: 5000; }} }}
  @keyframes blink {{ from {{ opacity: 0; }} }}
</style>
<rect width="100%" height="100%" fill="{c["bg"]}"/>
<text class="title" x="{WIDTH / 2}" y="45" text-anchor="middle">{html.escape(title)}</text>
<g class="grid">{"".join(grid)}</g>
<g>{"".join(ylabels)}{"".join(xlabels)}</g>
<text class="axis" x="{(PLOT_LEFT + PLOT_RIGHT) / 2}" y="{HEIGHT - 12}" text-anchor="middle">Days</text>
<text class="axis" transform="translate(28 {mid_y}) rotate(-90)" text-anchor="middle">Contributions</text>
<path class="line" d="{smooth_path(pts)}"/>
<g class="dots">{"".join(dots)}</g>
</svg>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--days", type=int, default=31)
    ap.add_argument("--title", help="default: \"<name>'s Contribution Graph\"")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    name, counts = fetch_graphql(args.user, token, args.days) if token else fetch_public_calendar(args.user)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    counts = [c for c in counts if c[0] <= today][-args.days:]
    title = args.title or f"{name or args.user}'s Contribution Graph"

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write(render(title, counts))
    print(f"{args.out}: {len(counts)} days, {counts[0][0]}..{counts[-1][0]}, peak {max(v for _, v in counts)}")


if __name__ == "__main__":
    main()
