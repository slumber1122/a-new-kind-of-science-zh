#!/usr/bin/env python3
"""Build the polished Chinese translation into a readable HTML volume."""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import shutil
from pathlib import Path

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt


ROOT = Path(__file__).resolve().parents[1]
POLISHED_DIR = ROOT / "translation" / "polished"
FIGURE_DIR = ROOT / "translation" / "assets" / "original-figures"
ASSET_MANIFEST = FIGURE_DIR / "manifest.json"
STANDALONE_OUTPUT = ROOT / "translation" / "output" / "a-new-kind-of-science-zh.html"
PAGES_OUTPUT = ROOT / "docs" / "index.html"

PAGE_RE = re.compile(r"^\[\[PDF page (\d+)\]\]$", re.MULTILINE)
FIGURE_COMMENT_RE = re.compile(r"^<!-- ORIGINAL_FIGURES .*?-->\s*$", re.MULTILINE)
HEADING_RE = re.compile(r"^# (.+?)\s*$", re.MULTILINE)


def part_id(path: Path) -> str:
    stem = path.stem
    if stem == "01-preface":
        return "preface"
    if stem == "14-notes":
        return "notes"
    match = re.search(r"chapter-(\d+)", stem)
    if match:
        return f"chapter-{int(match.group(1)):02d}"
    return stem


def normalize_title(value: str) -> str:
    return re.sub(r"[\s：:，,。.!！?？]", "", value)


def prepare_markdown(path: Path) -> tuple[str, str]:
    source = path.read_text(encoding="utf-8")
    first_heading = HEADING_RE.search(source)
    if not first_heading:
        raise ValueError(f"Missing level-one heading in {path}")

    title = first_heading.group(1).strip()
    body = source[: first_heading.start()] + source[first_heading.end() :]

    # Chapter files repeat the chapter title on the original opening page.
    next_heading = HEADING_RE.search(body)
    if next_heading:
        repeated = next_heading.group(1).strip()
        normalized_title = normalize_title(title)
        normalized_repeated = normalize_title(repeated)
        if normalized_repeated in normalized_title or normalized_title in normalized_repeated:
            body = body[: next_heading.start()] + body[next_heading.end() :]

    body = FIGURE_COMMENT_RE.sub("", body)
    body = PAGE_RE.sub(
        lambda match: (
            f'<div class="pdf-page" id="pdf-page-{match.group(1)}" '
            f'data-page="{match.group(1)}"><a href="#pdf-page-{match.group(1)}">'
            f'PDF 第 {match.group(1)} 页</a></div>'
        ),
        body,
    )
    return title, body.strip()


class HeadingIds:
    def __init__(self) -> None:
        self.counter = 0

    def next(self) -> str:
        self.counter += 1
        return f"section-{self.counter:03d}"


def render_markdown(
    md: MarkdownIt,
    source: str,
    heading_ids: HeadingIds,
    image_sizes: dict[str, tuple[int, int]],
    embed_images: bool,
) -> tuple[str, list[tuple[int, str, str]]]:
    tokens = md.parse(source)
    headings: list[tuple[int, str, str]] = []

    for index, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        level = int(token.tag[1:])
        title = tokens[index + 1].content.strip()
        heading_id = heading_ids.next()
        token.attrSet("id", heading_id)
        headings.append((level, title, heading_id))

    fragment = md.renderer.render(tokens, md.options, {})
    soup = BeautifulSoup(fragment, "html.parser")

    for image in soup.find_all("img"):
        source_path = image.get("src", "")
        image["loading"] = "lazy"
        image["decoding"] = "async"
        dimensions = image_sizes.get(source_path)
        if dimensions:
            image["width"], image["height"] = dimensions
        if source_path.startswith("../assets/original-figures/"):
            image["data-source"] = source_path
            asset_path = FIGURE_DIR / Path(source_path).name
            if embed_images:
                image["src"] = "data:image/png;base64," + base64.b64encode(asset_path.read_bytes()).decode("ascii")
            else:
                image["src"] = f"assets/original-figures/{asset_path.name}"

    for paragraph in list(soup.find_all("p")):
        images = paragraph.find_all("img", recursive=False)
        non_image_tags = [child for child in paragraph.find_all(recursive=False) if child.name != "img"]
        if images and not paragraph.get_text(strip=True) and not non_image_tags:
            group = soup.new_tag("div", attrs={"class": "figure-group"})
            for image in images:
                alt_text = image.get("alt", "")
                figure = soup.new_tag("figure")
                image.extract()
                figure.append(image)
                if alt_text:
                    caption = soup.new_tag("figcaption")
                    caption.string = alt_text
                    figure.append(caption)
                group.append(figure)
            paragraph.replace_with(group)

    return str(soup), headings


def load_image_sizes() -> dict[str, tuple[int, int]]:
    manifest = json.loads(ASSET_MANIFEST.read_text(encoding="utf-8"))
    sizes: dict[str, tuple[int, int]] = {}
    for figures in manifest.values():
        for figure in figures:
            sizes[figure["rel_path"]] = (figure["width_px"], figure["height_px"])
    return sizes


def toc_html(parts: list[dict[str, object]], mobile: bool = False) -> str:
    items: list[str] = []
    for part in parts:
        title = html.escape(str(part["title"]))
        anchor = html.escape(str(part["id"]))
        subheadings = [heading for heading in part["headings"] if heading[0] == 2]
        if subheadings and not mobile:
            links = "".join(
                f'<a class="toc-sub" href="#{heading_id}">{html.escape(label)}</a>'
                for _, label, heading_id in subheadings
            )
            items.append(
                '<details class="toc-group">'
                f'<summary><a href="#{anchor}">{title}</a></summary>{links}</details>'
            )
        else:
            items.append(f'<a class="toc-part" href="#{anchor}">{title}</a>')
    return "".join(items)


def build(output_path: Path, embed_images: bool) -> None:
    markdown_files = sorted(POLISHED_DIR.glob("*.md"))
    if not markdown_files:
        raise FileNotFoundError(f"No polished Markdown files found in {POLISHED_DIR}")

    md = MarkdownIt("commonmark", {"html": True, "linkify": False, "typographer": False})
    heading_ids = HeadingIds()
    image_sizes = load_image_sizes()
    parts: list[dict[str, object]] = []

    for path in markdown_files:
        title, source = prepare_markdown(path)
        rendered, headings = render_markdown(md, source, heading_ids, image_sizes, embed_images)
        parts.append(
            {
                "id": part_id(path),
                "title": title,
                "html": rendered,
                "headings": headings,
            }
        )

    desktop_toc = toc_html(parts)
    mobile_toc = toc_html(parts, mobile=True)
    content = "".join(
        f'<section class="book-part" id="{part["id"]}">'
        f'<header class="part-header"><p class="part-label">A New Kind of Science</p>'
        f'<h1>{html.escape(str(part["title"]))}</h1></header>'
        f'{part["html"]}<a class="back-to-top" href="#top">返回目录</a></section>'
        for part in parts
    )

    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>一种新科学 - 中文翻译合订版</title>
  <style>
    :root {{
      --paper: #fbfbfa;
      --surface: #f1f3f4;
      --ink: #202124;
      --muted: #697077;
      --line: #d8dcdf;
      --accent: #9d3027;
      --accent-cool: #1d6467;
      --code: #eef0f1;
      --sidebar-width: 292px;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; scroll-padding-top: 24px; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--paper);
      font-family: "Songti SC", "STSong", "Noto Serif CJK SC", "Source Han Serif SC", serif;
      font-size: 18px;
      line-height: 1.95;
      letter-spacing: 0;
    }}
    a {{ color: var(--accent-cool); text-decoration-thickness: 1px; text-underline-offset: 3px; }}
    .progress {{
      position: fixed;
      inset: 0 auto auto 0;
      z-index: 20;
      width: 0;
      height: 3px;
      background: var(--accent);
    }}
    .sidebar {{
      position: fixed;
      inset: 0 auto 0 0;
      width: var(--sidebar-width);
      overflow-y: auto;
      padding: 32px 24px 48px;
      background: var(--surface);
      border-right: 1px solid var(--line);
    }}
    .book-name {{
      margin: 0;
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
      font-size: 20px;
      font-weight: 700;
      line-height: 1.4;
    }}
    .book-author {{ margin: 6px 0 22px; color: var(--muted); font-size: 14px; }}
    .scope {{
      margin: 0 0 24px;
      padding: 12px 0;
      color: var(--muted);
      border-block: 1px solid var(--line);
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
      font-size: 12px;
      line-height: 1.65;
    }}
    .toc-label {{
      margin: 0 0 8px;
      color: var(--muted);
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
      font-size: 12px;
      font-weight: 700;
    }}
    .toc-part,
    .toc-group summary a {{
      display: block;
      padding: 7px 0;
      color: var(--ink);
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
      font-size: 14px;
      line-height: 1.5;
      text-decoration: none;
    }}
    .toc-group {{ border-bottom: 1px solid rgba(216, 220, 223, .7); }}
    .toc-group summary {{ cursor: pointer; color: var(--muted); }}
    .toc-group summary::marker {{ color: var(--muted); font-size: 10px; }}
    .toc-group summary a {{ display: inline; }}
    .toc-sub {{
      display: block;
      padding: 5px 0 5px 17px;
      color: var(--muted);
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
      font-size: 12px;
      line-height: 1.45;
      text-decoration: none;
    }}
    .toc-part:hover,
    .toc-group a:hover,
    .toc-part.active,
    .toc-group summary a.active {{ color: var(--accent); }}
    .reader {{ margin-left: var(--sidebar-width); }}
    .title-page {{
      min-height: 88vh;
      display: flex;
      flex-direction: column;
      justify-content: center;
      max-width: 800px;
      margin: 0 auto;
      padding: 72px 24px 64px;
      border-bottom: 1px solid var(--line);
    }}
    .title-page .eyebrow,
    .part-label {{
      margin: 0 0 14px;
      color: var(--accent);
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
      font-size: 12px;
      font-weight: 700;
    }}
    .title-page h1 {{
      max-width: 680px;
      margin: 0;
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
      font-size: 52px;
      line-height: 1.2;
      letter-spacing: 0;
    }}
    .title-page .subtitle {{ margin: 20px 0 0; color: var(--muted); font-size: 22px; }}
    .title-page .author {{ margin: 54px 0 0; font-size: 17px; }}
    .title-page .edition {{ margin: 8px 0 0; color: var(--muted); font-size: 14px; }}
    .mobile-toc {{ display: none; }}
    .book-part {{
      max-width: 800px;
      margin: 0 auto;
      padding: 96px 24px 56px;
      border-bottom: 1px solid var(--line);
    }}
    .part-header {{ margin-bottom: 54px; }}
    .part-header h1 {{
      margin: 0;
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
      font-size: 36px;
      line-height: 1.35;
      letter-spacing: 0;
    }}
    h2, h3, h4 {{
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
      letter-spacing: 0;
      scroll-margin-top: 24px;
    }}
    h2 {{ margin: 72px 0 24px; font-size: 27px; line-height: 1.4; }}
    h3 {{ margin: 54px 0 20px; font-size: 21px; line-height: 1.45; }}
    h4 {{ margin: 42px 0 16px; font-size: 18px; line-height: 1.5; }}
    p {{ margin: 0 0 1.15em; text-align: justify; }}
    hr {{ margin: 44px 0; border: 0; border-top: 1px solid var(--line); }}
    .pdf-page {{
      margin: 44px 0 24px;
      padding-top: 12px;
      border-top: 1px solid var(--line);
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
      font-size: 12px;
      line-height: 1.4;
    }}
    .pdf-page a {{ color: var(--muted); text-decoration: none; }}
    .figure-group {{ margin: 32px auto 40px; }}
    figure {{ margin: 0 auto 32px; text-align: center; }}
    figure:last-child {{ margin-bottom: 0; }}
    figure img {{
      display: block;
      width: auto;
      max-width: 100%;
      height: auto;
      margin: 0 auto;
      background: #fff;
    }}
    figcaption {{
      margin-top: 10px;
      color: var(--muted);
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
      font-size: 12px;
      line-height: 1.5;
    }}
    ul, ol {{ margin: 0 0 1.4em; padding-left: 1.6em; }}
    li {{ margin-bottom: .55em; }}
    code {{
      padding: .08em .3em;
      border-radius: 3px;
      background: var(--code);
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      font-size: .86em;
    }}
    pre {{
      overflow-x: auto;
      margin: 24px 0 32px;
      padding: 18px 20px;
      border-left: 3px solid var(--accent-cool);
      background: var(--code);
      font-size: 14px;
      line-height: 1.65;
      tab-size: 2;
    }}
    pre code {{ padding: 0; background: transparent; font-size: inherit; }}
    blockquote {{
      margin: 28px 0;
      padding: 4px 0 4px 22px;
      color: #4e565d;
      border-left: 3px solid var(--line);
    }}
    .back-to-top {{
      display: inline-block;
      margin-top: 56px;
      color: var(--muted);
      font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
      font-size: 13px;
      text-decoration: none;
    }}
    @media (max-width: 920px) {{
      .sidebar {{ display: none; }}
      .reader {{ margin-left: 0; }}
      .mobile-toc {{
        display: block;
        position: sticky;
        top: 0;
        z-index: 10;
        border-bottom: 1px solid var(--line);
        background: rgba(251, 251, 250, .96);
        font-family: "PingFang SC", "Microsoft YaHei", sans-serif;
      }}
      .mobile-toc summary {{ padding: 12px 20px; cursor: pointer; font-size: 14px; font-weight: 700; }}
      .mobile-toc nav {{ max-height: 62vh; overflow-y: auto; padding: 4px 20px 16px; }}
      .mobile-toc .toc-part {{ border-bottom: 1px solid var(--line); }}
      .title-page {{ min-height: 82vh; padding-top: 56px; }}
      .title-page h1 {{ font-size: 40px; }}
    }}
    @media (max-width: 560px) {{
      body {{ font-size: 17px; line-height: 1.9; }}
      .title-page, .book-part {{ padding-inline: 18px; }}
      .title-page h1 {{ font-size: 34px; }}
      .title-page .subtitle {{ font-size: 19px; }}
      .book-part {{ padding-top: 64px; }}
      .part-header h1 {{ font-size: 30px; }}
      h2 {{ margin-top: 56px; font-size: 24px; }}
      h3 {{ margin-top: 44px; font-size: 20px; }}
      p {{ text-align: left; }}
      pre {{ margin-inline: -8px; padding: 16px 14px; font-size: 12px; }}
    }}
    @media print {{
      @page {{ size: A4; margin: 18mm 17mm 20mm; }}
      body {{ background: #fff; font-size: 11pt; line-height: 1.75; }}
      .sidebar, .mobile-toc, .progress, .back-to-top {{ display: none !important; }}
      .reader {{ margin: 0; }}
      .title-page {{ min-height: 0; height: 240mm; border: 0; page-break-after: always; }}
      .book-part {{ max-width: none; padding: 0; border: 0; }}
      .book-part + .book-part {{ page-break-before: always; }}
      .part-header {{ margin-top: 0; }}
      h1, h2, h3, h4 {{ page-break-after: avoid; }}
      figure, pre, blockquote {{ page-break-inside: avoid; }}
      .pdf-page {{ page-break-before: auto; }}
      a {{ color: inherit; text-decoration: none; }}
    }}
  </style>
</head>
<body id="top">
  <div class="progress" aria-hidden="true"></div>
  <aside class="sidebar" aria-label="全书目录">
    <p class="book-name">一种新科学</p>
    <p class="book-author">Stephen Wolfram</p>
    <p class="scope">当前译稿范围：前言、第 1-12 章，以及注释至原 PDF 第 982 页。原书后续注释与索引尚未收入。</p>
    <p class="toc-label">目录</p>
    <nav>{desktop_toc}</nav>
  </aside>
  <main class="reader">
    <details class="mobile-toc">
      <summary>目录与译稿范围</summary>
      <nav><p class="scope">前言、第 1-12 章，以及注释至原 PDF 第 982 页。</p>{mobile_toc}</nav>
    </details>
    <header class="title-page">
      <p class="eyebrow">STEPHEN WOLFRAM</p>
      <h1>一种新科学</h1>
      <p class="subtitle">A New Kind of Science</p>
      <p class="author">斯蒂芬·沃尔弗拉姆 著</p>
      <p class="edition">中文翻译合订版 · 当前收录至原 PDF 第 982 页</p>
    </header>
    {content}
  </main>
  <script>
    const progress = document.querySelector('.progress');
    const partLinks = [...document.querySelectorAll('.sidebar a[href^="#chapter-"], .sidebar a[href="#preface"], .sidebar a[href="#notes"]')];
    const parts = [...document.querySelectorAll('.book-part')];
    const updateProgress = () => {{
      const scrollable = document.documentElement.scrollHeight - window.innerHeight;
      const ratio = scrollable > 0 ? window.scrollY / scrollable : 0;
      progress.style.width = `${{Math.min(1, Math.max(0, ratio)) * 100}}%`;
    }};
    const observer = new IntersectionObserver((entries) => {{
      const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!visible) return;
      partLinks.forEach(link => link.classList.toggle('active', link.getAttribute('href') === `#${{visible.target.id}}`));
    }}, {{ rootMargin: '-18% 0px -70% 0px', threshold: [0, .1, .5] }});
    parts.forEach(part => observer.observe(part));
    window.addEventListener('scroll', updateProgress, {{ passive: true }});
    updateProgress();
  </script>
</body>
</html>
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    if not embed_images:
        target_dir = output_path.parent / "assets" / "original-figures"
        target_dir.mkdir(parents=True, exist_ok=True)
        for asset_path in FIGURE_DIR.glob("*.png"):
            shutil.copy2(asset_path, target_dir / asset_path.name)
        (output_path.parent / ".nojekyll").write_text("", encoding="ascii")

    print(output_path)
    print(
        f"parts={len(parts)} images={document.count('<img ')} "
        f"page_anchors={document.count('class=\"pdf-page\"')} "
        f"embedded_images={document.count('src=\"data:image/png;base64,')}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pages",
        action="store_true",
        help="Build a linked-asset site in docs/ for GitHub Pages",
    )
    args = parser.parse_args()
    build(PAGES_OUTPUT if args.pages else STANDALONE_OUTPUT, embed_images=not args.pages)
