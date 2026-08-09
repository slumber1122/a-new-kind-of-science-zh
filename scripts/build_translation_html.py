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
    search_panel = """<div class="search-panel">
      <label class="search-label"><span>全文检索</span><input class="book-search" type="search" placeholder="输入关键词" autocomplete="off" autocapitalize="off" spellcheck="false" aria-label="全文检索"></label>
      <p class="search-status" aria-live="polite"></p>
      <div class="search-results"></div>
    </div>"""
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
  <meta name="color-scheme" content="dark light">
  <title>一种新科学 - 中文翻译合订版</title>
  <script>
    try {{
      document.documentElement.dataset.theme = localStorage.getItem('nks-theme') === 'light' ? 'light' : 'dark';
    }} catch (error) {{
      document.documentElement.dataset.theme = 'dark';
    }}
  </script>
  <style>
    :root {{
      color-scheme: dark;
      --paper: #151715;
      --surface: #1d201e;
      --surface-raised: #252925;
      --ink: #ece9e1;
      --muted: #a8afa9;
      --line: #373c38;
      --line-soft: rgba(168, 175, 169, .2);
      --accent: #ef7a62;
      --accent-cool: #78bdb3;
      --code: #222724;
      --quote: #c2c7c2;
      --mobile-surface: rgba(29, 32, 30, .96);
      --image-outline: rgba(255, 255, 255, .15);
      --search-hit: rgba(239, 122, 98, .2);
      --font-reading: "Noto Serif CJK SC", "Source Han Serif SC", "Songti SC", "STSong", "SimSun", serif;
      --font-ui: -apple-system, BlinkMacSystemFont, "PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei", sans-serif;
      --font-latin: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
      --font-mono: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      --sidebar-width: 292px;
    }}
    :root[data-theme="light"] {{
      color-scheme: light;
      --paper: #fbfbfa;
      --surface: #f1f3f4;
      --surface-raised: #e4e7e5;
      --ink: #202321;
      --muted: #68706b;
      --line: #d8dcda;
      --line-soft: rgba(104, 112, 107, .2);
      --accent: #a33b2f;
      --accent-cool: #236a68;
      --code: #eef0ef;
      --quote: #505853;
      --mobile-surface: rgba(251, 251, 250, .96);
      --image-outline: rgba(32, 35, 33, .12);
      --search-hit: rgba(163, 59, 47, .14);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; scroll-padding-top: 24px; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--paper);
      font-family: var(--font-reading);
      font-size: 18px;
      line-height: 1.95;
      letter-spacing: 0;
      text-rendering: optimizeLegibility;
      -webkit-font-smoothing: antialiased;
    }}
    ::selection {{ color: var(--paper); background: var(--accent); }}
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
      font-family: var(--font-ui);
      font-size: 20px;
      font-weight: 650;
      line-height: 1.4;
    }}
    .book-author {{
      margin: 6px 0 18px;
      color: var(--muted);
      font-family: var(--font-latin);
      font-size: 14px;
    }}
    .theme-control {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin: 0 0 18px;
      color: var(--muted);
      font-family: var(--font-ui);
      font-size: 12px;
      line-height: 1;
    }}
    .theme-toggle {{
      appearance: none;
      position: relative;
      width: 36px;
      height: 20px;
      flex: 0 0 auto;
      margin: 0;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--surface-raised);
      cursor: pointer;
    }}
    .theme-toggle::after {{
      content: "";
      position: absolute;
      top: 3px;
      left: 3px;
      width: 12px;
      height: 12px;
      border-radius: 50%;
      background: var(--muted);
      transition: transform .18s ease, background .18s ease;
    }}
    .theme-toggle:checked::after {{
      transform: translateX(16px);
      background: var(--accent);
    }}
    .theme-toggle:focus-visible {{ outline: 2px solid var(--accent-cool); outline-offset: 3px; }}
    .search-panel {{
      margin: 0 0 20px;
      font-family: var(--font-ui);
    }}
    .search-label {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      line-height: 1.4;
    }}
    .book-search {{
      display: block;
      width: 100%;
      height: 38px;
      margin-top: 8px;
      padding: 0 10px;
      border: 1px solid var(--line);
      border-radius: 4px;
      color: var(--ink);
      background: var(--paper);
      font: 14px/1.4 var(--font-ui);
      letter-spacing: 0;
    }}
    .book-search::placeholder {{ color: var(--muted); opacity: .85; }}
    .book-search:focus {{ border-color: var(--accent-cool); outline: 2px solid color-mix(in srgb, var(--accent-cool) 35%, transparent); outline-offset: 1px; }}
    .search-status {{
      display: none;
      margin: 9px 0 5px;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.45;
      text-align: left;
    }}
    .search-results {{
      display: none;
      max-height: 36vh;
      overflow-y: auto;
      border-bottom: 1px solid var(--line);
    }}
    .search-panel.has-query .search-status,
    .search-panel.has-query .search-results {{ display: block; }}
    .search-result {{
      display: block;
      width: 100%;
      margin: 0;
      padding: 10px 0;
      border: 0;
      border-top: 1px solid var(--line-soft);
      color: var(--ink);
      background: transparent;
      font-family: var(--font-ui);
      letter-spacing: 0;
      text-align: left;
      cursor: pointer;
    }}
    .search-result:hover,
    .search-result:focus-visible {{ color: var(--accent-cool); background: var(--search-hit); outline: none; }}
    .search-result-title,
    .search-result-snippet {{ display: block; }}
    .search-result-title {{
      margin-bottom: 4px;
      color: var(--accent);
      font-size: 11px;
      font-weight: 650;
      line-height: 1.35;
    }}
    .search-result-snippet {{
      font-size: 12px;
      line-height: 1.55;
    }}
    .search-result mark {{
      color: inherit;
      background: var(--search-hit);
    }}
    .search-target {{ animation: search-pulse 2.2s ease-out; }}
    @keyframes search-pulse {{
      0%, 35% {{ background: var(--search-hit); outline: 5px solid var(--search-hit); }}
      100% {{ background: transparent; outline: 0 solid transparent; }}
    }}
    .scope {{
      margin: 0 0 24px;
      padding: 12px 0;
      color: var(--muted);
      border-block: 1px solid var(--line);
      font-family: var(--font-ui);
      font-size: 12px;
      line-height: 1.65;
    }}
    .toc-label {{
      margin: 0 0 8px;
      color: var(--muted);
      font-family: var(--font-ui);
      font-size: 12px;
      font-weight: 700;
    }}
    .toc-part,
    .toc-group summary a {{
      display: block;
      padding: 7px 0;
      color: var(--ink);
      font-family: var(--font-ui);
      font-size: 14px;
      line-height: 1.5;
      text-decoration: none;
    }}
    .toc-group {{ border-bottom: 1px solid var(--line-soft); }}
    .toc-group summary {{ cursor: pointer; color: var(--muted); }}
    .toc-group summary::marker {{ color: var(--muted); font-size: 10px; }}
    .toc-group summary a {{ display: inline; }}
    .toc-sub {{
      display: block;
      padding: 5px 0 5px 17px;
      color: var(--muted);
      font-family: var(--font-ui);
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
      font-family: var(--font-ui);
      font-size: 12px;
      font-weight: 700;
    }}
    .title-page h1 {{
      max-width: 680px;
      margin: 0;
      font-family: var(--font-ui);
      font-size: 52px;
      font-weight: 650;
      line-height: 1.2;
      letter-spacing: 0;
    }}
    .title-page .subtitle {{
      margin: 20px 0 0;
      color: var(--muted);
      font-family: var(--font-latin);
      font-size: 22px;
    }}
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
      font-family: var(--font-ui);
      font-size: 36px;
      font-weight: 650;
      line-height: 1.35;
      letter-spacing: 0;
    }}
    h2, h3, h4 {{
      font-family: var(--font-ui);
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
      font-family: var(--font-ui);
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
      box-shadow: 0 0 0 1px var(--image-outline);
    }}
    figcaption {{
      margin-top: 10px;
      color: var(--muted);
      font-family: var(--font-ui);
      font-size: 12px;
      line-height: 1.5;
    }}
    ul, ol {{ margin: 0 0 1.4em; padding-left: 1.6em; }}
    li {{ margin-bottom: .55em; }}
    code {{
      padding: .08em .3em;
      border-radius: 3px;
      background: var(--code);
      font-family: var(--font-mono);
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
      color: var(--quote);
      border-left: 3px solid var(--line);
    }}
    .back-to-top {{
      display: inline-block;
      margin-top: 56px;
      color: var(--muted);
      font-family: var(--font-ui);
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
        background: var(--mobile-surface);
        font-family: var(--font-ui);
      }}
      .mobile-toc summary {{ padding: 12px 20px; cursor: pointer; font-size: 14px; font-weight: 700; }}
      .mobile-toc nav {{ max-height: 62vh; overflow-y: auto; padding: 4px 20px 16px; }}
      .mobile-toc .theme-control {{ margin-block: 10px 18px; }}
      .mobile-toc .search-panel {{ margin-bottom: 18px; }}
      .mobile-toc .search-results {{ max-height: 30vh; }}
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
    @media (prefers-reduced-motion: reduce) {{
      html {{ scroll-behavior: auto; }}
      .search-target {{ animation: none; background: var(--search-hit); }}
    }}
    @media print {{
      @page {{ size: A4; margin: 18mm 17mm 20mm; }}
      :root {{
        --paper: #fff;
        --ink: #111;
        --muted: #555;
        --line: #ccc;
        --code: #f2f2f2;
        --quote: #444;
      }}
      body {{ background: #fff; font-size: 11pt; line-height: 1.75; }}
      .sidebar, .mobile-toc, .progress, .back-to-top, .theme-control, .search-panel {{ display: none !important; }}
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
    <label class="theme-control"><span>浅色主题</span><input class="theme-toggle" type="checkbox" role="switch" aria-label="切换浅色主题"></label>
    {search_panel}
    <p class="scope">当前译稿范围：前言、第 1-12 章，以及注释至原 PDF 第 982 页。原书后续注释与索引尚未收入。</p>
    <p class="toc-label">目录</p>
    <nav>{desktop_toc}</nav>
  </aside>
  <main class="reader">
    <details class="mobile-toc">
      <summary>目录与译稿范围</summary>
      <nav><label class="theme-control"><span>浅色主题</span><input class="theme-toggle" type="checkbox" role="switch" aria-label="切换浅色主题"></label>{search_panel}<p class="scope">前言、第 1-12 章，以及注释至原 PDF 第 982 页。</p>{mobile_toc}</nav>
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
    const themeToggles = [...document.querySelectorAll('.theme-toggle')];
    const applyTheme = (theme, persist = false) => {{
      document.documentElement.dataset.theme = theme;
      themeToggles.forEach(toggle => {{ toggle.checked = theme === 'light'; }});
      document.querySelector('meta[name="color-scheme"]').content = theme === 'light' ? 'light dark' : 'dark light';
      if (persist) {{
        try {{ localStorage.setItem('nks-theme', theme); }} catch (error) {{}}
      }}
    }};
    applyTheme(document.documentElement.dataset.theme === 'light' ? 'light' : 'dark');
    themeToggles.forEach(toggle => {{
      toggle.addEventListener('change', () => applyTheme(toggle.checked ? 'light' : 'dark', true));
    }});
    const searchInputs = [...document.querySelectorAll('.book-search')];
    const searchPanels = [...document.querySelectorAll('.search-panel')];
    const searchLimit = 80;
    let searchIndex = null;
    let searchTimer = null;
    const normalizeSearchText = value => value.toLocaleLowerCase('zh-CN').replace(/\\s+/g, ' ').trim();
    const buildSearchIndex = () => {{
      let currentPage = '';
      let locationNumber = 0;
      const records = [];
      const nodes = document.querySelectorAll('.book-part .pdf-page, .book-part h1, .book-part h2, .book-part h3, .book-part h4, .book-part p, .book-part li, .book-part figcaption, .book-part pre');
      nodes.forEach(element => {{
        if (element.classList.contains('pdf-page')) {{
          currentPage = element.dataset.page || '';
          return;
        }}
        const text = element.innerText.replace(/\\s+/g, ' ').trim();
        if (text.length < 2) return;
        const part = element.closest('.book-part');
        const chapter = part?.querySelector('.part-header h1')?.textContent.trim() || '';
        if (!element.id) {{
          locationNumber += 1;
          element.id = `search-location-${{locationNumber}}`;
        }}
        records.push({{
          element,
          text,
          normalized: normalizeSearchText(text),
          chapter,
          page: currentPage
        }});
      }});
      return records;
    }};
    const appendHighlightedText = (container, text, terms) => {{
      const normalized = text.toLocaleLowerCase('zh-CN');
      let cursor = 0;
      while (cursor < text.length) {{
        let nextIndex = -1;
        let nextTerm = '';
        terms.forEach(term => {{
          const index = normalized.indexOf(term, cursor);
          if (index !== -1 && (nextIndex === -1 || index < nextIndex)) {{
            nextIndex = index;
            nextTerm = term;
          }}
        }});
        if (nextIndex === -1) {{
          container.append(document.createTextNode(text.slice(cursor)));
          break;
        }}
        if (nextIndex > cursor) container.append(document.createTextNode(text.slice(cursor, nextIndex)));
        const mark = document.createElement('mark');
        mark.textContent = text.slice(nextIndex, nextIndex + nextTerm.length);
        container.append(mark);
        cursor = nextIndex + nextTerm.length;
      }}
    }};
    const resultSnippet = (record, terms) => {{
      const firstPosition = Math.min(...terms.map(term => record.normalized.indexOf(term)).filter(index => index >= 0));
      const start = Math.max(0, firstPosition - 42);
      const end = Math.min(record.text.length, firstPosition + 96);
      return `${{start > 0 ? '…' : ''}}${{record.text.slice(start, end)}}${{end < record.text.length ? '…' : ''}}`;
    }};
    const jumpToSearchResult = (record, panel) => {{
      panel.closest('.mobile-toc')?.removeAttribute('open');
      history.replaceState(null, '', `#${{record.element.id}}`);
      const previousScrollBehavior = document.documentElement.style.scrollBehavior;
      document.documentElement.style.scrollBehavior = 'auto';
      record.element.scrollIntoView({{ block: 'center' }});
      document.documentElement.style.scrollBehavior = previousScrollBehavior;
      record.element.classList.remove('search-target');
      void record.element.offsetWidth;
      record.element.classList.add('search-target');
      window.setTimeout(() => record.element.classList.remove('search-target'), 2300);
    }};
    const renderSearchResults = query => {{
      const normalizedQuery = normalizeSearchText(query);
      const terms = normalizedQuery.split(' ').filter(Boolean);
      searchPanels.forEach(panel => panel.classList.toggle('has-query', terms.length > 0));
      if (!terms.length) {{
        searchPanels.forEach(panel => {{
          panel.querySelector('.search-status').textContent = '';
          panel.querySelector('.search-results').replaceChildren();
        }});
        return;
      }}
      if (!searchIndex) searchIndex = buildSearchIndex();
      const matches = searchIndex
        .filter(record => terms.every(term => record.normalized.includes(term)))
        .sort((a, b) => {{
          const aHeading = /^H[1-4]$/.test(a.element.tagName) ? -1000 : 0;
          const bHeading = /^H[1-4]$/.test(b.element.tagName) ? -1000 : 0;
          return (aHeading + a.normalized.indexOf(terms[0])) - (bHeading + b.normalized.indexOf(terms[0]));
        }});
      const visibleMatches = matches.slice(0, searchLimit);
      searchPanels.forEach(panel => {{
        const status = panel.querySelector('.search-status');
        const results = panel.querySelector('.search-results');
        status.textContent = matches.length > searchLimit ? `找到 ${{matches.length}} 处，显示前 ${{searchLimit}} 条` : `找到 ${{matches.length}} 处`;
        results.replaceChildren();
        if (!matches.length) return;
        visibleMatches.forEach(record => {{
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'search-result';
          const title = document.createElement('span');
          title.className = 'search-result-title';
          title.textContent = `${{record.chapter}}${{record.page ? ` · PDF 第 ${{record.page}} 页` : ''}}`;
          const snippet = document.createElement('span');
          snippet.className = 'search-result-snippet';
          appendHighlightedText(snippet, resultSnippet(record, terms), terms);
          button.append(title, snippet);
          button.addEventListener('click', () => jumpToSearchResult(record, panel));
          results.append(button);
        }});
      }});
    }};
    searchInputs.forEach(input => {{
      input.addEventListener('input', event => {{
        const query = event.currentTarget.value;
        searchInputs.forEach(peer => {{ if (peer !== event.currentTarget) peer.value = query; }});
        window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(() => renderSearchResults(query), 90);
      }});
      input.addEventListener('keydown', event => {{
        if (event.key === 'Escape') {{
          searchInputs.forEach(peer => {{ peer.value = ''; }});
          renderSearchResults('');
          event.currentTarget.blur();
        }} else if (event.key === 'Enter') {{
          event.preventDefault();
          event.currentTarget.closest('.search-panel').querySelector('.search-result')?.click();
        }}
      }});
    }});
    const initialSearchQuery = searchInputs.find(input => input.value)?.value || '';
    if (initialSearchQuery) {{
      searchInputs.forEach(input => {{ input.value = initialSearchQuery; }});
      renderSearchResults(initialSearchQuery);
    }}
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
