#!/usr/bin/env python3
import argparse
import html as html_lib
import posixpath
import re
import zipfile
from pathlib import Path, PurePosixPath


TOC_NAME_PARTS = ("toc", "contents", "navigation")
NON_LINEAR_STEMS = re.compile(r"popdef\d+[a-z]?$", re.IGNORECASE)
TEST_STEM = re.compile(r"test[_-](\d+)-(\d+)([a-z]?)$", re.IGNORECASE)
CHAPTER_STEM = re.compile(r"(?:^|[_-])(?:c|ch|chapter)[_-]?(\d+)$", re.IGNORECASE)
CHAPTER_FRAGMENT_STEM = re.compile(r"(?:^|[_-])c[_-]?(\d+)[_-](\d+)([a-z]?)$", re.IGNORECASE)
PART_STEM = re.compile(r"(?:^|[_-])p(?:art)?[_-]?(\d+)$", re.IGNORECASE)
BACKMATTER_STEM = re.compile(r"(?:^|[_-])bm[_-]?(\d+)$", re.IGNORECASE)
FRONTMATTER_STEM = re.compile(r"(?:^|[_-])fm[_-]?(\d+)$", re.IGNORECASE)
GENERATED_TOC_NAME = "generated_toc.xhtml"


def natural_sort_key(text):
    tokens = re.findall(r"\d+|[a-z]+", str(text).lower())
    return tuple((0, int(token)) if token.isdigit() else (1, token) for token in tokens)


def normalize_space(text):
    if not isinstance(text, str):
        return ""
    return re.sub(r"\s+", " ", text).strip()


def strip_tags(text):
    return normalize_space(html_lib.unescape(re.sub(r"<[^>]+>", "", text)))


def resolve_href(base_dir, href):
    href = href.split("#", 1)[0].strip()
    if not href:
        return ""
    return posixpath.normpath(posixpath.join(base_dir, href))


def remap_toc_href(resolved_href, manifest_hrefs):
    if resolved_href in manifest_hrefs:
        return resolved_href

    manifest_list = list(manifest_hrefs)
    manifest_by_name = {}
    manifest_by_stem = {}
    for href in manifest_list:
        path = PurePosixPath(href)
        manifest_by_name.setdefault(path.name.lower(), []).append(href)
        manifest_by_stem.setdefault(path.stem.lower(), []).append(href)

    resolved_path = PurePosixPath(resolved_href)
    stem = resolved_path.stem.lower()
    name = resolved_path.name.lower()

    if "cover" in stem and "default_cover.xhtml" in manifest_hrefs:
        return "default_cover.xhtml"

    direct_name_matches = manifest_by_name.get(name, [])
    if len(direct_name_matches) == 1:
        return direct_name_matches[0]

    stem_matches = manifest_by_stem.get(stem, [])
    if len(stem_matches) == 1:
        return stem_matches[0]

    ext_variants = {
        stem + ".xhtml",
        stem + ".html",
        stem + ".htm",
    }
    variant_matches = []
    for variant in ext_variants:
        variant_matches.extend(manifest_by_name.get(variant, []))
    if len(variant_matches) == 1:
        return variant_matches[0]

    basename_matches = [
        href for href in manifest_list if PurePosixPath(href).name.lower().startswith(stem)
    ]
    if len(basename_matches) == 1:
        return basename_matches[0]

    return resolved_href


def parse_manifest(opf_text):
    return dict(
        re.findall(r'<item id="([^"]+)" href="([^"]+)" media-type="application/xhtml\+xml"\s*/?>', opf_text)
    )


def parse_spine(opf_text):
    return re.findall(r'<itemref idref="([^"]+)"\s*/?>', opf_text)


def parse_ncx_entries(ncx_text):
    entries = []
    for nav_id, label, src in re.findall(
        r'<navPoint id="([^"]+)"[^>]*><navLabel><text>(.*?)</text></navLabel><content src="([^"]+)"\s*/?>',
        ncx_text,
        flags=re.S,
    ):
        entries.append(
            {
                "id": nav_id,
                "label": strip_tags(label),
                "href": src,
            }
        )
    return entries


def parse_toc_document(toc_path, oebps_root, manifest_hrefs):
    relative_toc = toc_path.relative_to(oebps_root).as_posix()
    toc_dir = PurePosixPath(relative_toc).parent.as_posix()
    if toc_dir == ".":
        toc_dir = ""

    text = toc_path.read_text(errors="ignore")
    entries = []
    seen = set()
    for href, label_html in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, flags=re.I | re.S):
        if href.startswith(("#", "mailto:", "javascript:")) or re.match(r"^[a-z]+://", href, re.I):
            continue

        resolved = resolve_href(toc_dir, href)
        if not resolved:
            continue

        remapped = remap_toc_href(resolved, manifest_hrefs)

        if remapped not in manifest_hrefs and not (oebps_root / remapped).exists():
            continue

        label = strip_tags(label_html)
        if not label:
            continue

        href_base = remapped
        if href_base in seen:
            continue

        seen.add(href_base)
        entries.append({"label": label, "href": href_base})

    return entries


def find_toc_candidates(oebps_root):
    candidates = []
    for path in oebps_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".xhtml", ".html", ".htm"}:
            continue

        name = path.name.lower()
        if name == GENERATED_TOC_NAME:
            continue
        if any(part in name for part in TOC_NAME_PARTS):
            candidates.append(path)

    return sorted(candidates, key=lambda path: natural_sort_key(path.relative_to(oebps_root).as_posix()))


def choose_toc_candidate(candidates, oebps_root, manifest_hrefs):
    if not candidates:
        return None

    def score(path):
        rel = path.relative_to(oebps_root).as_posix().lower()
        base = path.name.lower()
        in_manifest = rel in manifest_hrefs
        exact_names = {
            "toc.xhtml",
            "toc.html",
            "toc.htm",
            "contents.xhtml",
            "contents.html",
            "contents.htm",
            "navigation.xhtml",
            "navigation.html",
            "navigation.htm",
        }
        ext_rank = {".xhtml": 0, ".html": 1, ".htm": 2}.get(path.suffix.lower(), 3)
        if base in exact_names:
            return (0 if in_manifest else 1, 0, ext_rank, natural_sort_key(rel))
        if base.endswith("_toc.xhtml") or "_toc." in base:
            return (0 if in_manifest else 1, 1, ext_rank, natural_sort_key(rel))
        if "contents" in base:
            return (0 if in_manifest else 1, 2, ext_rank, natural_sort_key(rel))
        if "navigation" in base:
            return (0 if in_manifest else 1, 3, ext_rank, natural_sort_key(rel))
        return (0 if in_manifest else 1, 4, ext_rank, natural_sort_key(rel))

    return sorted(candidates, key=score)[0]


def classify_front_matter(rel_path):
    stem = PurePosixPath(rel_path).stem.lower()
    title_keywords = ("title",)
    copyright_keywords = ("copyright",)
    dedication_keywords = ("dedication",)

    if rel_path == "default_cover.xhtml":
        return (0, 0, natural_sort_key(rel_path))
    if any(key in stem for key in title_keywords):
        return (1, 0, natural_sort_key(rel_path))
    if any(key in stem for key in copyright_keywords):
        return (2, 0, natural_sort_key(rel_path))
    if any(key in stem for key in dedication_keywords):
        return (3, 0, natural_sort_key(rel_path))
    if FRONTMATTER_STEM.search(stem):
        return (4, int(FRONTMATTER_STEM.search(stem).group(1)), natural_sort_key(rel_path))
    if stem.startswith("prf") or "preface" in stem or stem.startswith("ack"):
        return (5, 0, natural_sort_key(rel_path))
    return None


def is_duplicate_cover(rel_path, manifest_hrefs):
    if rel_path == "default_cover.xhtml":
        return False
    if "default_cover.xhtml" not in manifest_hrefs:
        return False

    stem = PurePosixPath(rel_path).stem.lower()
    return stem == "cover" or stem.endswith("_cover") or stem.endswith("_cov")


def build_spine_order(manifest_hrefs, toc_href, toc_entries):
    toc_map = {entry["href"]: index for index, entry in enumerate(toc_entries)}
    ordered = []
    seen = set()

    def add(href):
        if href in manifest_hrefs and href not in seen and not is_duplicate_cover(href, manifest_hrefs):
            ordered.append(href)
            seen.add(href)

    # Cover + title/copyright/dedication first.
    front_candidates = []
    for href in manifest_hrefs:
        front_key = classify_front_matter(href)
        if front_key is not None:
            front_candidates.append((front_key, href))

    for _key, href in sorted(front_candidates):
        add(href)

    # Put a TOC page near the front.
    if toc_href:
        add(toc_href)

    # Follow authored TOC order where available.
    for entry in toc_entries:
        href = entry["href"]
        add(href)

        chapter_match = CHAPTER_STEM.match(PurePosixPath(href).stem)
        if chapter_match:
            chapter_no = int(chapter_match.group(1))
            for candidate in manifest_hrefs:
                stem = PurePosixPath(candidate).stem
                test_match = TEST_STEM.match(stem)
                if not test_match:
                    continue
                if int(test_match.group(1)) != chapter_no:
                    continue
                add(candidate)

    # Natural fallback for remaining linear items.
    remaining = []
    for href in manifest_hrefs:
        if href in seen:
            continue
        stem = PurePosixPath(href).stem
        lower_stem = stem.lower()
        basename = PurePosixPath(href).name.lower()
        if basename in {GENERATED_TOC_NAME, "toc.xhtml", "toc.html", "toc.htm", "nav.xhtml", "nav.html", "nav.htm"}:
            continue
        if NON_LINEAR_STEMS.match(lower_stem):
            continue
        if is_duplicate_cover(href, manifest_hrefs):
            continue
        remaining.append(href)

    def remaining_key(href):
        stem = PurePosixPath(href).stem.lower()
        part_match = PART_STEM.search(stem)
        chapter_match = CHAPTER_STEM.search(stem)
        fragment_match = CHAPTER_FRAGMENT_STEM.search(stem)
        test_match = TEST_STEM.match(stem)
        backmatter_match = BACKMATTER_STEM.search(stem)
        frontmatter_match = FRONTMATTER_STEM.search(stem)
        if part_match:
            return (10, int(part_match.group(1)), natural_sort_key(href))
        if frontmatter_match:
            return (15, int(frontmatter_match.group(1)), natural_sort_key(href))
        if chapter_match:
            return (20, int(chapter_match.group(1)), natural_sort_key(href))
        if fragment_match:
            return (
                21,
                int(fragment_match.group(1)),
                int(fragment_match.group(2)),
                1 if fragment_match.group(3) else 0,
                natural_sort_key(href),
            )
        if test_match:
            return (
                21,
                int(test_match.group(1)),
                int(test_match.group(2)),
                1 if test_match.group(3) else 0,
                natural_sort_key(href),
            )
        if stem.startswith("prf") or "preface" in stem or stem.startswith("ack") or "intro" in stem:
            return (16, 0, natural_sort_key(href))
        if backmatter_match or "biblio" in stem or "index" in stem or "about" in stem or "appendix" in stem:
            return (90, int(backmatter_match.group(1)) if backmatter_match else 0, natural_sort_key(href))
        if "fm_" in stem or stem.startswith("prf") or "preface" in stem or "ack" in stem or "intro" in stem:
            return (15, 0, natural_sort_key(href))
        return (50, 0, natural_sort_key(href))

    for href in sorted(remaining, key=remaining_key):
        add(href)

    return ordered


def build_nav_entries(toc_href, toc_entries, spine_hrefs, existing_ncx_entries):
    label_map = {}
    for entry in existing_ncx_entries:
        href = entry["href"].split("#", 1)[0]
        if href and href not in label_map and entry["label"]:
            label_map[href] = entry["label"]

    entries = []
    allowed_hrefs = set(spine_hrefs)
    if spine_hrefs and spine_hrefs[0] == "default_cover.xhtml":
        entries.append({"label": "Cover", "href": "default_cover.xhtml"})

    if toc_href:
        entries.append({"label": "Contents", "href": toc_href})

    if toc_entries:
        for entry in toc_entries:
            href = entry["href"]
            if href == toc_href or href == "default_cover.xhtml" or href not in allowed_hrefs:
                continue
            entries.append({"label": entry["label"], "href": href})
    else:
        seen = {entry["href"] for entry in entries}
        for href in spine_hrefs:
            if href in seen:
                continue
            label = label_map.get(href)
            if not label:
                label = PurePosixPath(href).stem.replace("_", " ").replace("-", " ").strip() or href
            entries.append({"label": label, "href": href})
            seen.add(href)

    deduped = []
    seen = set()
    for entry in entries:
        href = entry["href"]
        if href in seen:
            continue
        deduped.append(entry)
        seen.add(href)

    return deduped


def render_generated_toc(book_title, nav_entries):
    lines = [
        '<!DOCTYPE html>',
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">',
        "<head>",
        f"<title>{html_lib.escape(book_title)} - Contents</title>",
        '<meta http-equiv="Content-Type" content="text/html; charset=utf-8" />',
        "</head>",
        "<body>",
        '<div id="sbo-rt-content">',
        "<h1>Contents</h1>",
        "<ul>",
    ]

    for entry in nav_entries:
        if entry["href"] == GENERATED_TOC_NAME:
            continue
        lines.append(
            f'<li><a href="{html_lib.escape(entry["href"])}">{html_lib.escape(entry["label"])}</a></li>'
        )

    lines.extend(["</ul>", "</div>", "</body>", "</html>"])
    return "\n".join(lines) + "\n"


def rebuild_epub(book_dir):
    epub_path = book_dir / f"{book_dir.name.split('(')[-1].rstrip(')')}.epub"
    if epub_path.exists():
        epub_path.unlink()

    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.write(book_dir / "mimetype", "mimetype", compress_type=zipfile.ZIP_STORED)

    with zipfile.ZipFile(epub_path, "a", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for base in ("META-INF", "OEBPS"):
            for path in sorted((book_dir / base).rglob("*")):
                if not path.is_file() or path.name == ".DS_Store":
                    continue
                zf.write(path, path.relative_to(book_dir).as_posix())

    return epub_path


def update_manifest(opf_text, href):
    item_id = PurePosixPath(href).stem
    manifest_entry = (
        f'<item id="{item_id}" href="{href}" media-type="application/xhtml+xml" />'
    )

    if f'href="{href}"' in opf_text:
        return opf_text

    return re.sub(r"(</manifest>)", manifest_entry + "\n" + r"\1", opf_text, count=1)


def rewrite_opf(opf_path, spine_hrefs, cover_href, extra_manifest_hrefs=None):
    opf_text = opf_path.read_text(errors="ignore")
    manifest = parse_manifest(opf_text)
    href_to_id = {href: item_id for item_id, href in manifest.items()}

    for href in extra_manifest_hrefs or []:
        if href not in href_to_id:
            opf_text = update_manifest(opf_text, href)
            manifest = parse_manifest(opf_text)
            href_to_id = {href_: item_id for item_id, href_ in manifest.items()}

    new_spine = "".join(
        f'<itemref idref="{href_to_id[href]}"/>\n'
        for href in spine_hrefs
        if href in href_to_id
    )
    opf_text = re.sub(r"(<spine[^>]*>\n)(.*?)(</spine>)", lambda m: m.group(1) + new_spine + m.group(3), opf_text, count=1, flags=re.S)
    opf_text = re.sub(
        r'(<guide><reference href=")([^"]+)(" title="Cover" type="cover"\s*/></guide>)',
        lambda m: m.group(1) + cover_href + m.group(3),
        opf_text,
        count=1,
    )
    opf_path.write_text(opf_text)


def rewrite_ncx(ncx_path, book_title, author, nav_entries):
    nav_lines = []
    for index, entry in enumerate(nav_entries, start=1):
        nav_lines.append(
            f'<navPoint id="chap_{index}" playOrder="{index}"><navLabel><text>{html_lib.escape(entry["label"])}</text></navLabel><content src="{html_lib.escape(entry["href"])}"/></navPoint>'
        )
    navmap = "\n".join(nav_lines) + "\n"

    text = ncx_path.read_text(errors="ignore")
    text = re.sub(r"(<docTitle><text>)(.*?)(</text></docTitle>)", lambda m: m.group(1) + html_lib.escape(book_title) + m.group(3), text, count=1, flags=re.S)
    text = re.sub(r"(<docAuthor><text>)(.*?)(</text></docAuthor>)", lambda m: m.group(1) + html_lib.escape(author) + m.group(3), text, count=1, flags=re.S)
    text = re.sub(r'(<meta content=")(\d+)(" name="dtb:depth"\s*/>)', r'\g<1>1\g<3>', text, count=1)
    text = re.sub(r"(<navMap>)(.*?)(</navMap>)", lambda m: m.group(1) + navmap + m.group(3), text, count=1, flags=re.S)
    ncx_path.write_text(text)


def extract_metadata(opf_text):
    title = re.search(r"<dc:title>(.*?)</dc:title>", opf_text, flags=re.S)
    authors = re.findall(r"<dc:creator[^>]*>(.*?)</dc:creator>", opf_text, flags=re.S)
    return strip_tags(title.group(1) if title else "Untitled"), strip_tags(", ".join(authors))


def repair_book(book_dir):
    oebps_root = book_dir / "OEBPS"
    opf_path = oebps_root / "content.opf"
    ncx_path = oebps_root / "toc.ncx"
    opf_text = opf_path.read_text(errors="ignore")
    manifest = parse_manifest(opf_text)
    manifest_hrefs = list(manifest.values())
    title, author = extract_metadata(opf_text)

    toc_candidates = find_toc_candidates(oebps_root)
    selected_toc = choose_toc_candidate(toc_candidates, oebps_root, set(manifest_hrefs))
    toc_entries = parse_toc_document(selected_toc, oebps_root, set(manifest_hrefs)) if selected_toc else []
    toc_href = selected_toc.relative_to(oebps_root).as_posix() if selected_toc else ""

    existing_ncx_entries = parse_ncx_entries(ncx_path.read_text(errors="ignore")) if ncx_path.exists() else []

    if not toc_href:
        toc_href = GENERATED_TOC_NAME
        if toc_href not in manifest_hrefs:
            manifest_hrefs.append(toc_href)
        toc_entries = []
    else:
        if toc_href not in manifest_hrefs:
            manifest_hrefs.append(toc_href)

    spine_hrefs = build_spine_order(manifest_hrefs, toc_href, toc_entries)
    nav_entries = build_nav_entries(toc_href, toc_entries, spine_hrefs, existing_ncx_entries)

    if toc_href == GENERATED_TOC_NAME:
        (oebps_root / GENERATED_TOC_NAME).write_text(render_generated_toc(title, nav_entries))
        extra_manifest_hrefs = [GENERATED_TOC_NAME]
    else:
        extra_manifest_hrefs = [toc_href] if toc_href not in manifest else []

    cover_href = "default_cover.xhtml" if "default_cover.xhtml" in manifest_hrefs else spine_hrefs[0]
    rewrite_opf(opf_path, spine_hrefs, cover_href, extra_manifest_hrefs)
    rewrite_ncx(ncx_path, title, author, nav_entries)
    epub_path = rebuild_epub(book_dir)

    return {
        "book": book_dir.name,
        "toc_href": toc_href,
        "first_spine": spine_hrefs[:8],
        "epub": epub_path.name,
    }


def main():
    parser = argparse.ArgumentParser(description="Repair local EPUB packages from downloaded book folders.")
    parser.add_argument("book_ids", nargs="+", help="Book IDs to repair.")
    parser.add_argument("--books-root", default="Books", help="Books root directory.")
    args = parser.parse_args()

    books_root = Path(args.books_root).resolve()
    results = []
    missing = []
    for book_id in args.book_ids:
        matches = [path for path in books_root.iterdir() if path.is_dir() and f"({book_id})" in path.name]
        if not matches:
            missing.append(book_id)
            continue

        results.append(repair_book(matches[0]))

    for result in results:
        print(result["book"])
        print(f"  toc: {result['toc_href']}")
        print(f"  first_spine: {', '.join(result['first_spine'])}")
        print(f"  rebuilt: {result['epub']}")

    if missing:
        print("Missing:", ", ".join(missing))


if __name__ == "__main__":
    main()
