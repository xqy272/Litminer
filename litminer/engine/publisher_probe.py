#!/usr/bin/env python3
"""Probe DOI/publisher landing pages for Agent evidence planning.

This is not a scraper and does not parse PDFs. It resolves DOI URLs, records
access status, and detects obvious PDF/SI links so an Agent can decide the next
action.
"""

from __future__ import annotations

import argparse
import html
import ipaddress
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from litminer import __version__
from litminer.engine.common import normalize_doi, read_csv_rows, write_csv_atomic

USER_AGENT = f"litminer/{__version__} (+https://github.com/xqy272/Litminer)"
REQUEST_TIMEOUT = 30
HTML_LIMIT = 500_000
ALLOWED_SCHEMES = {"http", "https"}

PDF_LINK_RE = re.compile(
    r"""href=["']([^"']*(?:pdf|article/pii|download)[^"']*)["'][^>]*>(.{0,120}?(?:pdf|download).{0,120}?)<""",
    re.I | re.S,
)
SI_LINK_RE = re.compile(
    r"""href=["']([^"']*)["'][^>]*>(.{0,160}?(?:supplement|supporting information|appendix|data).{0,160}?)<""",
    re.I | re.S,
)
LINK_RE = re.compile(r"""<a\b[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>""", re.I | re.S)
META_PDF_RE = re.compile(
    r"""<meta\b[^>]*(?:name|property)=["']citation_pdf_url["'][^>]*content=["']([^"']+)["']""",
    re.I | re.S,
)
FULL_TEXT_RE = re.compile(r"\b(full text|materials and methods|experimental|supporting information)\b", re.I)
ABSTRACT_RE = re.compile(r"\babstract\b", re.I)
PAYWALL_RE = re.compile(r"\b(purchase|rent|subscribe|institutional access|access through your institution)\b", re.I)
METHODS_RE = re.compile(r"\b(methods?|materials and methods|experimental|procedure)\b", re.I)
RESULTS_RE = re.compile(r"\b(results?|discussion|conclusion|fig(?:ure)?\.?\s*\d+|table\s+\d+)\b", re.I)

PUBLISHER_FAMILIES = [
    ("sciencedirect.com", "elsevier_sciencedirect"),
    ("elsevier.com", "elsevier"),
    ("springer.com", "springer"),
    ("link.springer.com", "springer"),
    ("nature.com", "springer_nature"),
    ("acs.org", "acs"),
    ("pubs.acs.org", "acs"),
    ("rsc.org", "rsc"),
    ("wiley.com", "wiley"),
    ("onlinelibrary.wiley.com", "wiley"),
    ("tandfonline.com", "taylor_francis"),
    ("mdpi.com", "mdpi"),
    ("frontiersin.org", "frontiers"),
    ("cell.com", "cell_press"),
    ("science.org", "aaas"),
    ("pnas.org", "pnas"),
    ("aip.org", "aip"),
    ("iopscience.iop.org", "iop"),
    ("hindawi.com", "hindawi"),
    ("degruyter.com", "de_gruyter"),
    ("sagepub.com", "sage"),
]


def first_value(row: dict[str, str], fields: list[str]) -> str:
    for field in fields:
        value = (row.get(field) or "").strip()
        if value:
            return value
    return ""


def absolute_url(base: str, href: str) -> str:
    href = html.unescape(href or "").strip()
    if not href:
        return ""
    return urllib.parse.urljoin(base, href)


def strip_tags(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def normalize_domain(value: str) -> str:
    domain = (value or "").lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def publisher_family(domain: str) -> str:
    domain = normalize_domain(domain)
    for suffix, family in PUBLISHER_FAMILIES:
        if domain == suffix or domain.endswith("." + suffix):
            return family
    if domain:
        return "other_publisher"
    return "unknown"


def validate_public_http_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValueError(f"Blocked URL scheme: {parsed.scheme or 'empty'}")
    host = parsed.hostname
    if not host:
        raise ValueError("Blocked URL without host")
    host_l = host.lower().rstrip(".")
    if host_l in {"localhost", "localhost.localdomain"} or host_l.endswith(".local"):
        raise ValueError(f"Blocked local host: {host}")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve host: {host}") from exc

    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError(f"Invalid resolved address for host {host}: {address}") from exc
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(f"Blocked non-public address for host {host}: {address}")


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


SAFE_OPENER = urllib.request.build_opener(SafeRedirectHandler)


def request_url(url: str, max_bytes: int = HTML_LIMIT) -> dict[str, str]:
    try:
        validate_public_http_url(url)
    except ValueError as exc:
        return {
            "ok": "false",
            "status": "blocked_url",
            "url": url,
            "content_type": "",
            "body": "",
            "error": str(exc),
        }

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with SAFE_OPENER.open(req, timeout=REQUEST_TIMEOUT) as resp:
            body = resp.read(max_bytes)
            return {
                "ok": "true",
                "status": str(resp.status),
                "url": resp.geturl(),
                "content_type": resp.headers.get("Content-Type", ""),
                "body": body.decode("utf-8", errors="replace"),
                "error": "",
            }
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read(min(max_bytes, 100_000)).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return {
            "ok": "false",
            "status": str(exc.code),
            "url": exc.geturl() or url,
            "content_type": exc.headers.get("Content-Type", "") if exc.headers else "",
            "body": body,
            "error": f"HTTP {exc.code}: {exc.reason}",
        }
    except Exception as exc:
        return {
            "ok": "false",
            "status": "",
            "url": url,
            "content_type": "",
            "body": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def detect_link(body: str, base_url: str, pattern: re.Pattern[str]) -> str:
    for match in pattern.finditer(body or ""):
        href = match.group(1)
        if href:
            return absolute_url(base_url, href)
    return ""


def detect_article_links(body: str, base_url: str) -> tuple[str, str, str]:
    """Return likely PDF URL, SI URL, and semicolon-separated link hints."""
    body = body or ""
    hints: list[str] = []

    meta_pdf = META_PDF_RE.search(body)
    if meta_pdf:
        pdf = absolute_url(base_url, meta_pdf.group(1))
        hints.append("pdf:meta_citation_pdf_url")
        return pdf, detect_link(body, base_url, SI_LINK_RE), "; ".join(hints)

    pdf_url = ""
    si_url = ""
    for href, raw_text in LINK_RE.findall(body):
        text = strip_tags(raw_text).lower()
        href_l = html.unescape(href).lower()
        absolute = absolute_url(base_url, href)
        if not pdf_url and (
            "pdf" in href_l
            or "download" in href_l
            or "pdf" in text
            or "download pdf" in text
        ):
            pdf_url = absolute
            hints.append(f"pdf:{text[:60] or href_l[:60]}")
        if not si_url and (
            "supplement" in href_l
            or "supporting" in href_l
            or "supplement" in text
            or "supporting information" in text
            or text in {"appendix", "data availability"}
        ):
            si_url = absolute
            hints.append(f"si:{text[:60] or href_l[:60]}")
        if pdf_url and si_url:
            break

    if not pdf_url:
        pdf_url = detect_link(body, base_url, PDF_LINK_RE)
        if pdf_url:
            hints.append("pdf:regex_fallback")
    if not si_url:
        si_url = detect_link(body, base_url, SI_LINK_RE)
        if si_url:
            hints.append("si:regex_fallback")
    return pdf_url, si_url, "; ".join(hints)


def page_features(body: str, content_type: str) -> str:
    body = body or ""
    features = []
    is_html = "html" in (content_type or "").lower() or "<html" in body[:1000].lower()
    if is_html:
        features.append("html")
    if ABSTRACT_RE.search(body):
        features.append("abstract_marker")
    if FULL_TEXT_RE.search(body):
        features.append("fulltext_marker")
    if METHODS_RE.search(body):
        features.append("methods_marker")
    if RESULTS_RE.search(body):
        features.append("results_marker")
    if PAYWALL_RE.search(body):
        features.append("paywall_marker")
    return "; ".join(features)


def classify_access(status: str, content_type: str, body: str) -> tuple[str, str, str, str, str]:
    body = body or ""
    if status == "blocked_url":
        return "blocked_url", "unknown", "unknown", "unknown", "Reject private/local/non-HTTP URL"
    status_int = int(status) if status.isdigit() else 0
    is_html = "html" in (content_type or "").lower() or "<html" in body[:1000].lower()

    if status_int in (401, 402, 403):
        return "blocked", "blocked", "unknown", "unknown", "Open in browser or institutional network"
    if status_int >= 500:
        return "server_error", "unknown", "unknown", "unknown", "Retry later"
    if not status_int:
        return "network_error", "unknown", "unknown", "unknown", "Check DOI URL manually"
    if not is_html:
        if "pdf" in (content_type or "").lower():
            return "pdf_direct", "not_html", "found", "unknown", "Record PDF URL for downstream tooling"
        return "non_html", "not_html", "unknown", "unknown", "Open resolved URL manually"

    has_full = bool(FULL_TEXT_RE.search(body))
    has_abs = bool(ABSTRACT_RE.search(body))
    paywall = bool(PAYWALL_RE.search(body))

    if has_full and not paywall:
        return "html_possible", "available", "unknown", "unknown", "Inspect publisher-visible HTML; record PDF/SI links if present"
    if has_abs:
        return "abstract_only_or_landing", "available", "unknown", "unknown", "Use publisher-visible fields only; record access limitation"
    return "landing_page", "available", "unknown", "unknown", "Open landing page in browser"


def probe_row(row: dict[str, str]) -> dict[str, str]:
    row = dict(row)
    doi = normalize_doi(first_value(row, ["doi", "crossref_doi"]))
    start_url = (
        f"https://doi.org/{doi}" if doi
        else first_value(row, ["publisher_url", "landing_page_url", "url", "crossref_url"])
    )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not start_url:
        row.update({
            "publisher_probe_start_url": "",
            "publisher_probe_method": "doi_or_url_http_heuristic",
            "publisher_probe_confidence": "low",
            "resolved_url": "",
            "resolved_domain": "",
            "publisher_family": "",
            "http_status": "",
            "content_type": "",
            "page_features": "",
            "access_status": "missing_url",
            "html_status": "unknown",
            "pdf_status": "unknown",
            "pdf_url": "",
            "si_status": "unknown",
            "si_url": "",
            "publisher_link_hints": "",
            "next_action": "Recover DOI or publisher URL before extraction",
            "publisher_probe_error": "No DOI or URL",
            "publisher_probe_note": "Heuristic planning signal only; inspect publisher page before using as evidence.",
            "publisher_probe_at": now,
        })
        return row

    result = request_url(start_url)
    body = result["body"]
    resolved = result["url"]
    pdf_url, si_url, link_hints = detect_article_links(body, resolved)
    if not pdf_url and row.get("best_oa_pdf_url"):
        pdf_url = row.get("best_oa_pdf_url", "")
        link_hints = "; ".join([hint for hint in [link_hints, "pdf:unpaywall_best_oa"] if hint])
    if not resolved and row.get("best_oa_landing_url"):
        resolved = row.get("best_oa_landing_url", "")
    access, html_status, pdf_status, si_status, next_action = classify_access(
        result["status"], result["content_type"], body
    )
    if pdf_url:
        pdf_status = "found"
    elif pdf_status == "unknown":
        pdf_status = "not_found"
    if si_url:
        si_status = "found"
    elif si_status == "unknown":
        si_status = "not_found"

    domain = normalize_domain(urllib.parse.urlparse(resolved).netloc.lower())
    family = publisher_family(domain)
    row.update({
        "publisher_url": row.get("publisher_url") or start_url,
        "publisher_probe_start_url": start_url,
        "publisher_probe_method": "doi_or_url_http_heuristic",
        "publisher_probe_confidence": "medium" if result["ok"] == "true" else "low",
        "resolved_url": resolved,
        "resolved_domain": domain,
        "publisher_family": family,
        "http_status": result["status"],
        "content_type": result["content_type"],
        "page_features": page_features(body, result["content_type"]),
        "access_status": access,
        "html_status": html_status,
        "pdf_status": pdf_status,
        "pdf_url": pdf_url,
        "si_status": si_status,
        "si_url": si_url,
        "publisher_link_hints": link_hints,
        "next_action": next_action,
        "publisher_probe_error": result["error"],
        "publisher_probe_note": "Heuristic planning signal only; inspect publisher page before using as evidence.",
        "publisher_probe_at": now,
    })
    return row


def probe_csv(input_path: Path, output_path: Path,
              limit: int | None = None,
              sleep_s: float = 0.5) -> dict[str, int]:
    fieldnames, rows = read_csv_rows(input_path)
    if not fieldnames:
        raise SystemExit("Input CSV has no header")

    probe_cols = [
        "publisher_probe_start_url",
        "publisher_probe_method",
        "publisher_probe_confidence",
        "resolved_url",
        "resolved_domain",
        "publisher_family",
        "http_status",
        "content_type",
        "page_features",
        "access_status",
        "html_status",
        "pdf_status",
        "pdf_url",
        "si_status",
        "si_url",
        "publisher_link_hints",
        "next_action",
        "publisher_probe_error",
        "publisher_probe_note",
        "publisher_probe_at",
    ]
    for col in probe_cols:
        if col not in fieldnames:
            fieldnames.append(col)

    output_rows = []
    counts: dict[str, int] = {}
    for idx, row in enumerate(rows):
        if limit is not None and idx >= limit:
            output_rows.append(row)
            continue
        probed = probe_row(row)
        status = probed.get("access_status", "unknown")
        counts[status] = counts.get(status, 0) + 1
        output_rows.append(probed)
        if sleep_s:
            time.sleep(sleep_s)

    write_csv_atomic(output_rows, output_path, fieldnames=fieldnames)

    print(f"Publisher probe: {sum(counts.values())} probed -> {output_path}", file=sys.stderr)
    for key, value in sorted(counts.items()):
        print(f"  {key}: {value}", file=sys.stderr)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe DOI/publisher landing pages.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=0.5)
    args = parser.parse_args()
    probe_csv(args.input, args.output, limit=args.limit, sleep_s=args.sleep)


if __name__ == "__main__":
    main()
