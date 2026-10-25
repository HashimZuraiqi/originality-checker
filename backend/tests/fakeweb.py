"""A fake internet for tests: canned responses in the real APIs' formats.

Responses follow the documented shapes of the OpenAlex works API, the
MediaWiki action API (formatversion=2), Crossref REST, Semantic Scholar
Graph API and the arXiv Atom feed. They are test fixtures, never used by the
application at runtime.
"""

import io
import json
from urllib.parse import parse_qs, urlparse

import httpx

from app.services.discovery.http import FetchBlocked

PHOTOSYNTHESIS = (
    "Photosynthesis is the process by which green plants and certain other organisms transform light energy into chemical energy. "
    "During photosynthesis in green plants, light energy is captured and used to convert water, carbon dioxide, and minerals into "
    "oxygen and energy-rich organic compounds. It would be impossible to overestimate the importance of photosynthesis in the "
    "maintenance of life on Earth. If photosynthesis ceased, there would soon be little food or other organic matter on Earth. "
    "Most organisms would disappear, and in time Earth's atmosphere would become nearly devoid of gaseous oxygen. "
    "The process of photosynthesis is commonly written as six molecules of carbon dioxide and six molecules of water yielding glucose "
    "and six molecules of oxygen, although the actual chemistry involves many intermediate reactions in the chloroplast."
)
REVOLUTION = (
    "The French Revolution was a period of political and societal change in France that began with the Estates General of 1789 "
    "and ended with the coup of 18 Brumaire in 1799. Many of its ideas are considered fundamental principles of liberal democracy, "
    "while its values and institutions remain central to modern French political discourse. Its causes are generally agreed to be "
    "a combination of social, political, and economic factors which the existing regime proved unable to manage. "
    "Financial crisis and widespread social distress led to the convocation of the Estates General in May 1789."
)

state = {"mode": "normal", "requests": []}


def reset(mode: str = "normal") -> None:
    state["mode"] = mode
    state["requests"] = []


def check_public_host(url: str) -> None:
    host = urlparse(url).hostname or ""
    if host in ("127.0.0.1", "localhost", "169.254.169.254", "10.0.0.5"):
        raise FetchBlocked("address not public")


def _inverted(text: str) -> dict:
    index: dict[str, list[int]] = {}
    for i, w in enumerate(text.split()):
        index.setdefault(w, []).append(i)
    return index


def _pdf_bytes(text: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4).build([Paragraph(text, getSampleStyleSheet()["BodyText"])])
    return buf.getvalue()


def handler(request: httpx.Request) -> httpx.Response:
    url = request.url
    host, path = url.host, url.path
    q = parse_qs(urlparse(str(url)).query)
    state["requests"].append(str(url))
    mode = state["mode"]

    if mode == "down":
        return httpx.Response(503, text="maintenance")
    if mode == "offline":
        raise httpx.ConnectError("blocked", request=request)

    if host == "api.openalex.org":
        if path.startswith("/works/https://doi.org/"):
            return httpx.Response(404, json={"error": "not found"})
        search = q.get("search", [""])[0].lower()
        results = []
        if "revolution" in search or "brumaire" in search or "estates" in search:
            results.append(
                {
                    "id": "https://openalex.org/W1",
                    "doi": "https://doi.org/10.1000/rev.1789",
                    "display_name": "The French Revolution: an overview",
                    "publication_year": 2015,
                    "authorships": [{"author": {"display_name": "Jean Dupont"}}],
                    "abstract_inverted_index": _inverted(REVOLUTION),
                    "best_oa_location": {"pdf_url": "https://repo.example.org/rev.pdf", "landing_page_url": "https://repo.example.org/rev", "license": "cc-by"},
                    "open_access": {"is_oa": True},
                    "primary_location": {"landing_page_url": "https://repo.example.org/rev"},
                }
            )
        return httpx.Response(200, json={"meta": {"count": len(results)}, "results": results})

    if host == "repo.example.org":
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        if path == "/rev.pdf":
            return httpx.Response(200, content=_pdf_bytes(REVOLUTION + " " + REVOLUTION), headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    if host == "en.wikipedia.org":
        if q.get("list") == ["search"]:
            sr = q.get("srsearch", [""])[0].lower()
            hits = []
            if "photosynthesis" in sr or "light energy" in sr or "chemical energy" in sr or "carbon dioxide" in sr or "organic" in sr:
                hits.append({"title": "Photosynthesis", "pageid": 24544, "snippet": "<span class=\"searchmatch\">Photosynthesis</span> is the process"})
            return httpx.Response(200, json={"batchcomplete": True, "query": {"search": hits}})
        if q.get("prop") == ["extracts"]:
            return httpx.Response(200, json={"query": {"pages": [{"pageid": 24544, "title": "Photosynthesis", "extract": PHOTOSYNTHESIS}]}})
        return httpx.Response(400)

    if host == "api.crossref.org":
        if path.startswith("/works/"):
            doi = path[len("/works/") :]
            if doi.lower() == "10.1000/known.2020":
                return httpx.Response(200, json={"message": {"DOI": doi, "title": ["Warming trends in the modern era"], "issued": {"date-parts": [[2020]]}, "author": [{"given": "J", "family": "Smith"}], "URL": f"https://doi.org/{doi}"}})
            return httpx.Response(404, text="Resource not found.")
        return httpx.Response(200, json={"message": {"items": []}})

    if host == "api.semanticscholar.org":
        return httpx.Response(200, json={"total": 0, "data": []})

    if host == "export.arxiv.org":
        return httpx.Response(200, text='<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>')

    return httpx.Response(404)


def client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": "tests"})


def requests_to(host: str) -> list[str]:
    return [u for u in state["requests"] if urlparse(u).hostname == host]


__all__ = ["PHOTOSYNTHESIS", "REVOLUTION", "reset", "client", "requests_to", "json"]
