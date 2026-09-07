"""Body-evidenced title suffixes for site13; does not rewrite existing factual guides."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import subprocess
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, quote, urljoin
import xml.etree.ElementTree as ET

from title_suffix_rules import HUB_SUFFIXES, select_detail

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "https://xn--9l4b2jy4h4wan0chw4b.com"
BASELINE_COMMIT = "b6804d601c4d0f0dc03b54c01815a39542460fa5"
EXPECTED_TARGETS = 3348
EXPECTED_HTML = 3379
EXPECTED_SITEMAP = 3378
EXPECTED_NOINDEX = 0
REPORT = ROOT / "tools/reports/title-suffix-audit.json"
TITLE_RE = re.compile(r"(<title\b[^>]*>)(.*?)(</title>)", re.I | re.S)
META_RE = re.compile(r"<meta\b[^>]*>", re.I)
ATTR_RE = re.compile(r"""([:\w-]+)\s*=\s*(["'])(.*?)\2""", re.S)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
def clean(value):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())

def attrs(tag):
    return {m[1].lower(): html.unescape(m[3]) for m in ATTR_RE.finditer(tag)}

def title_of(source):
    matches = list(TITLE_RE.finditer(source))
    if len(matches) != 1:
        raise ValueError("Expected exactly one title")
    return html.unescape(matches[0][2])

def replace_titles(source, title):
    escaped = html.escape(title)
    result, count = TITLE_RE.subn(lambda m: m[1] + escaped + m[3], source)
    if count != 1:
        raise ValueError("Expected exactly one title")
    def replace_meta(m):
        values = attrs(m[0])
        if values.get("property", values.get("name")) not in {"og:title", "twitter:title"}:
            return m[0]
        if "content" not in values:
            raise ValueError("Social title has no content")
        return ATTR_RE.sub(lambda a: a[0][:a.start(3)-a.start()] + escaped + a[0][a.end(3)-a.start():]
                           if a[1].lower() == "content" else a[0], m[0])
    return META_RE.sub(replace_meta, result)

def masked(source):
    return replace_titles(source, "__PAGE_TITLE__")

def sha(source):
    return hashlib.sha256(source.encode("utf-8")).hexdigest()

class CopyParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.active = []
        self.blocks = []

    def hidden(self):
        return any(t in {"script", "style", "nav", "footer"} or "hidden" in a
                   or a.get("aria-hidden") == "true"
                   or re.search(r"display\s*:\s*none", a.get("style", "") or "", re.I)
                   for t, a in self.stack)

    def handle_starttag(self, tag, values):
        data = dict(values)
        if tag in {"p", "h2", "h3"} and any(t == "main" for t, _ in self.stack) and not self.hidden():
            self.active.append(dict(tag=tag, own=data, ancestors=list(self.stack), chunks=[], depth=len(self.stack)))
        if tag not in VOID:
            self.stack.append((tag, data))

    def handle_startendtag(self, tag, values):
        if tag not in VOID:
            self.handle_starttag(tag, values)
            self.handle_endtag(tag)

    def handle_data(self, value):
        if not self.hidden():
            for block in self.active:
                block["chunks"].append(value)

    def handle_endtag(self, tag):
        index = next((i for i in range(len(self.stack)-1, -1, -1) if self.stack[i][0] == tag), None)
        if index is None:
            return
        for block in list(self.active):
            if block["depth"] >= index:
                block["text"] = " ".join("".join(block.pop("chunks")).split())
                self.blocks.append(block)
                self.active.remove(block)
        del self.stack[index:]

def classify(path):
    parts = path.parts
    if parts[0] not in {"과목별학원", "전국학원"} or len(parts) not in {3, 4}:
        raise ValueError("Out of scope: " + path.as_posix())
    category = parts[1]
    stage = "elementary" if category.startswith("초") else "middle" if category.startswith("중") else "high" if category.startswith("고") else "all"
    subject = "combined" if "영수" in category else "english" if "영어" in category else "math" if "수학" in category else "general"
    kind = "category" if len(parts) == 3 else "subject" if parts[0] == "과목별학원" else "national"
    return dict(kind=kind, group=category, stage=stage, subject=subject)


def body_blocks(source,kind):
    """Only the actual fact-guide article; never nav, FAQ, review or hidden copy."""
    parser=CopyParser()
    parser.feed(source)
    blocks=[]
    for block in parser.blocks:
        classes=set(" ".join(a.get("class","") or "" for _,a in block["ancestors"]).split())
        if "academy-main-article" not in classes or block["tag"]!="p":
            continue
        role="intro" if "academy-intro-answer" in classes else "prose" if "academy-prose-section" in classes else None
        if role and block["text"]:
            blocks.append(dict(text=block["text"],role=role))
    return blocks

def excerpt(text, match):
    start = max(0, match.start() - 38)
    end = min(len(text), match.end() + 75)
    return text[start:end]

def hub_copy(source, terms):
    parser = CopyParser()
    parser.feed(source)
    # Keep one real paragraph. Joining disjoint paragraphs would silently
    # skip the visible H1 and produce a non-contiguous evidence excerpt.
    return next((b["text"] for b in parser.blocks
                 if all(term in b["text"] for term in terms) and any(
                     "academy-hero" in (a.get("class", "") or "").split()
                     for _, a in b["ancestors"])), "")

def resolved_page_url(canonical, rel):
    expected = DOMAIN + quote("/" + rel.parent.as_posix() + "/", safe="/")
    if unquote(urljoin(DOMAIN + "/", canonical)) != unquote(expected):
        raise ValueError("Canonical does not match its file route: " + rel.as_posix())
    return expected

def read_page(path):
    source = path.read_bytes().decode("utf-8")
    rel = path.relative_to(ROOT)
    page = dict(path=path, rel=rel.as_posix(), source=source, before=title_of(source), **classify(rel))
    if "|" not in page["before"]:
        raise ValueError("Existing suffix separator missing: " + page["rel"])
    page["prefix"] = page["before"].split("|", 1)[0].rstrip()
    canonicals = [attrs(m[0])["href"] for m in re.finditer(r"<link\b[^>]*>", source, re.I)
                  if attrs(m[0]).get("rel") == "canonical"]
    if len(canonicals) != 1:
        raise ValueError("Wrong canonical: " + page["rel"])
    # Preserve canonical bytes; resolve only the URL used for HTTP checking.
    page["canonical"] = canonicals[0]
    page["url"] = resolved_page_url(canonicals[0], rel)
    robots = [attrs(m[0]).get("content", "") for m in META_RE.finditer(source) if attrs(m[0]).get("name") == "robots"]
    if len(robots) != 1:
        raise ValueError("Expected one robots meta: " + page["rel"])
    page["indexable"] = "noindex" not in robots[0].lower()
    if page["kind"] == "category":
        suffix, terms = HUB_SUFFIXES[page["group"]]
        text = hub_copy(source, terms)
        if not text or not all(term in text for term in terms):
            raise ValueError("Hub lacks its stated evidence: " + page["rel"])
        page["suffix"] = suffix
        page["evidence"] = [dict(label=suffix, match=term, excerpt=excerpt(text, re.search(re.escape(term), text)), subject="") for term in terms]
    else:
        page["blocks"] = body_blocks(source, page["kind"])
        if not page["blocks"]:
            raise ValueError("No eligible factual body copy: " + page["rel"])
    return page


def plan(pages):
    for page in pages:
        if page["kind"]!="category":
            page["suffix"],page["evidence"],page["gradeMissing"]=select_detail(page)
        else:
            page["gradeMissing"]=False
        page["after"]=page["prefix"]+" | "+page["suffix"]
        if not 12<=len(page["after"])<=85:
            raise ValueError("Title length: "+page["rel"])
        page["updated"]=replace_titles(page["source"],page["after"])
        if masked(page["source"])!=masked(page["updated"]):
            raise ValueError("Non-title mutation: "+page["rel"])
    if len({p["after"] for p in pages})!=len(pages):
        raise ValueError("Duplicate complete titles")

def inventory():
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    all_html = [n for n in names if n.endswith(".html")]
    if len(all_html) != EXPECTED_HTML:
        raise ValueError("HTML inventory changed")
    targets = [ROOT/n for n in all_html if n.startswith(("전국학원/", "과목별학원/"))
               and n not in {"전국학원/index.html", "과목별학원/index.html"}]
    if len(targets) != EXPECTED_TARGETS:
        raise ValueError("Target count changed")
    target_set = set(targets)
    return sorted(targets), [n for n in all_html if ROOT/n not in target_set]

def update_rss(pages, source):
    by_url = {unquote(p["url"]): p["after"] for p in pages}
    changed = 0
    def item(m):
        nonlocal changed
        link = re.search(r"<link>(.*?)</link>", m[0], re.S)
        key = unquote(html.unescape(link[1])) if link else ""
        if key not in by_url:
            return m[0]
        updated = re.sub(r"(<title>).*?(</title>)", lambda n:n[1]+html.escape(by_url[key])+n[2], m[0], count=1, flags=re.S)
        changed += updated != m[0]
        return updated
    return re.sub(r"<item>.*?</item>", item, source, flags=re.S), changed

def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    paths, protected = inventory()
    def checked_read(path):
        try:
            return read_page(path), None
        except Exception as exc:
            return None, str(exc)
    with ThreadPoolExecutor(max_workers=8) as pool:
        loaded = list(pool.map(checked_read, paths))
    errors = [error for _, error in loaded if error]
    if errors:
        print(json.dumps(dict(failures=len(errors), examples=errors[:20]), ensure_ascii=False))
        raise SystemExit(1)
    pages = [page for page, _ in loaded]
    plan(pages)
    old_entries = {}
    if REPORT.exists():
        old_entries = {x["path"]:x for x in json.loads(REPORT.read_text(encoding="utf-8"))["entries"]}
    rss_exists = (ROOT/"rss.xml").is_file()
    rss = (ROOT/"rss.xml").read_bytes().decode("utf-8") if rss_exists else ""
    rss_new, rss_changes = update_rss(pages, rss)
    entries = []
    for page in pages:
        before = page["before"]
        old = old_entries.get(page["rel"])
        if old and old["unchangedContentSha256"] == sha(masked(page["source"])):
            before = old["before"]
        entries.append(dict(path=page["rel"],url=page["url"],group=page["group"],kind=page["kind"],stage=page["stage"],subject=page["subject"],
                            before=before,after=page["after"],suffix=page["suffix"],evidence=page["evidence"],indexable=page["indexable"],canonical=page["canonical"],gradeMissing=page["gradeMissing"],
                            unchangedContentSha256=sha(masked(page["source"])),
                            unchangedNormalizedContentSha256=sha(masked(page["source"]).replace("\r\n","\n"))))
    changed = [p for p in pages if p["source"] != p["updated"]]
    report = dict(site=DOMAIN,baselineCommit=BASELINE_COMMIT,pages=len(pages),changedThisRun=len(changed),
                  changedFromOriginal=sum(x["before"]!=x["after"] for x in entries),rssTitlesChanged=rss_changes,
                  sitemapCount=len(ET.parse(ROOT/"sitemap.xml").findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}url")),
                  rssExists=rss_exists,rssCount=len(ET.fromstring(rss).findall(".//item")) if rss_exists else 0,
                  uniqueTitles=len({x["after"] for x in entries}),uniqueSuffixes=len({x["suffix"] for x in entries}),
                  titleLength=dict(min=min(len(x["after"]) for x in entries),max=max(len(x["after"]) for x in entries)),
                  protectedHtml=protected,nonTitleChanges=0,noindexCount=sum(not p["indexable"] for p in pages),missingGradeCount=sum(p["gradeMissing"] for p in pages),entries=entries)
    if report["sitemapCount"] != EXPECTED_SITEMAP or report["noindexCount"] != EXPECTED_NOINDEX:
        raise ValueError("Existing indexing policy changed")
    output = REPORT if args.write else REPORT.with_name("title-suffix-plan.json")
    if not args.check:
        output.parent.mkdir(parents=True,exist_ok=True)
        if args.write:
            for page in changed:
                page["path"].write_bytes(page["updated"].encode("utf-8"))
            if rss_new != rss:
                (ROOT/"rss.xml").write_bytes(rss_new.encode("utf-8"))
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8",newline="\n")
    print(json.dumps({k:v for k,v in report.items() if k not in {"entries","protectedHtml"}},ensure_ascii=False))
    for group in sorted({p["group"] for p in pages}):
        members=[p for p in pages if p["group"]==group]
        print(json.dumps(dict(group=group,pages=len(members),uniqueSuffixes=len({p["suffix"] for p in members}),
                              mostRepeated=Counter(p["suffix"] for p in members).most_common(3)),ensure_ascii=False))
    for p in pages:
        if "명일동" in p["rel"]:
            print(p["after"])
    if args.check and (changed or rss_changes):
        raise SystemExit("FAIL: title regeneration is not idempotent")
    print("TITLE_SUFFIX_"+("WRITE" if args.write else "CHECK" if args.check else "PLAN")+"_PASS")

if __name__ == "__main__":
    main()
