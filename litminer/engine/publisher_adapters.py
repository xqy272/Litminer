#!/usr/bin/env python3
"""Publisher inspection adapter registry.

The core Litminer runtime ships only a safe HTTP heuristic adapter. Browser,
PDF, OCR, and institutional-access adapters can be added by external Agents or
plugins without changing the queue contract.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class PublisherAdapter:
    name: str
    status: str
    capabilities: tuple[str, ...]
    boundary: str

    def row(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "capabilities": list(self.capabilities),
            "boundary": self.boundary,
        }


ADAPTERS: dict[str, PublisherAdapter] = {
    "http_heuristic": PublisherAdapter(
        name="http_heuristic",
        status="built_in",
        capabilities=("doi_resolution", "html_marker_detection", "pdf_si_link_hints", "ssrf_guard"),
        boundary="No JavaScript execution, no PDF parsing, no paywall bypass.",
    ),
    "browser_page": PublisherAdapter(
        name="browser_page",
        status="external_optional",
        capabilities=("javascript_page_opening", "screenshot_capture", "visible_text_extraction"),
        boundary="Requires an Agent/browser integration; must respect publisher access controls.",
    ),
    "pdf_extraction": PublisherAdapter(
        name="pdf_extraction",
        status="external_optional",
        capabilities=("pdf_text", "table_extraction", "supplementary_materials"),
        boundary="Outside Litminer core; use only on accessible PDFs/SI.",
    ),
}


def adapter_rows() -> list[dict[str, object]]:
    return [adapter.row() for adapter in ADAPTERS.values()]


def main() -> None:
    parser = argparse.ArgumentParser(description="List Litminer publisher inspection adapters.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    rows = adapter_rows()
    if args.json:
        print(json.dumps({"adapters": rows}, indent=2))
    else:
        for row in rows:
            print(f"{row['name']}: {row['status']} - {row['boundary']}")


if __name__ == "__main__":
    main()
