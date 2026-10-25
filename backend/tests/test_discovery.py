import httpx
import pytest

from app.config import get_settings
from app.services.citations import analyze_citations
from app.services.discovery import http as dhttp
from app.services.discovery.engine import discover_sources
from app.services.discovery.http import FetchBlocked, ProviderClient, fetch_resource
from app.services.discovery.providers import ArxivProvider, CrossrefProvider, OpenAlexProvider, WikipediaProvider
from app.services.discovery.queries import Query, generate_queries
from app.services.discovery.retrieval import RobotsCache, html_to_text
from app.services.text import all_sentences, normalize_document, segment

from . import fakeweb

# Captured at import time, before the autouse fixture swaps in the fake.
REAL_CHECK_PUBLIC_HOST = dhttp._check_public_host

DOC = normalize_document(
    "Plants and history\n\n"
    "During photosynthesis in green plants, light energy is captured and used to convert water, carbon dioxide, and minerals "
    "into oxygen and energy-rich organic compounds. This is copied.\n\n"
    "The French Revolution was a period of political and societal change in France that began with the Estates General of 1789 "
    "and ended with the coup of 18 Brumaire in 1799. Many of its ideas are considered fundamental principles of liberal democracy.\n\n"
    "References\n\nSmith, J. (2020). Warming trends in the modern era. Nature. https://doi.org/10.1000/known.2020\n"
    "Doe, A. (2019). An invented article that does not exist. Journal. https://doi.org/10.9999/fake.1\n"
)


def _ctx():
    paras = segment(DOC)
    sents = all_sentences(paras)
    return paras, sents, analyze_citations(DOC, sents)


def test_query_generation_families_and_spread():
    paras, sents, ctx = _ctx()
    qs = generate_queries(DOC, paras, sents, ctx, "en")
    fams = {q.family for q in qs}
    assert {"phrase", "keyword", "reference"} <= fams
    phrases = [q for q in qs if q.family == "phrase"]
    assert all(len(q.text.split()) <= 12 for q in phrases)
    assert all(q.origin_start is not None and DOC[q.origin_start : q.origin_end].replace('"', "") for q in phrases)
    assert all(not ctx.in_bibliography(q.origin_start) for q in phrases)


def _pc(name):
    return ProviderClient(name, fakeweb.client(), use_cache=False)


def test_openalex_parses_abstract_and_oa_pdf():
    p = OpenAlexProvider(get_settings(), _pc("openalex"))
    res = p.search(Query("k1", "keyword", "french revolution estates general"))
    assert res[0].doi == "10.1000/rev.1789"
    assert res[0].text.startswith("The French Revolution was")
    assert res[0].pdf_url == "https://repo.example.org/rev.pdf"
    assert "mailto=tests%40example.org" in fakeweb.requests_to("api.openalex.org")[0]


def test_wikipedia_search_and_full_text():
    p = WikipediaProvider(get_settings(), _pc("wikipedia"))
    hits = p.search(Query("p1", "phrase", "light energy is captured and used to convert water"))
    assert hits[0].provider_id == "24544" and hits[0].url.endswith("/wiki/Photosynthesis")
    assert p.full_text("24544").startswith("Photosynthesis is the process")
    assert "%22light+energy" in fakeweb.requests_to("en.wikipedia.org")[0]  # exact-phrase search


def test_crossref_doi_verification():
    p = CrossrefProvider(get_settings(), _pc("crossref"))
    assert p.verify_doi("10.1000/known.2020")["status"] == "verified"
    assert p.verify_doi("10.9999/fake.1")["status"] == "not_found"


def test_arxiv_empty_feed():
    assert ArxivProvider(get_settings(), _pc("arxiv")).search(Query("k", "keyword", "quantum gravity loops")) == []


def test_discovery_end_to_end_finds_full_text_sources_and_verifies_references():
    paras, sents, ctx = _ctx()
    res = discover_sources(DOC, paras, sents, ctx, "en")
    titles = {s.title: s for s in res.sources}
    assert "Photosynthesis — Wikipedia" in titles
    assert titles["Photosynthesis — Wikipedia"].text_kind == "full_text"
    rev = next(s for s in res.sources if s.doi == "10.1000/rev.1789")
    assert rev.text_kind == "full_text"  # open-access PDF retrieved and parsed
    statuses = {r["status"] for r in res.reference_checks}
    assert {"verified", "not_found"} <= statuses
    cov = {p["name"]: p for p in res.coverage["providers"]}
    assert cov["brave"]["status"] == "not_configured"
    assert cov["openalex"]["status"] == "ok"
    assert "compared against" in res.coverage["summary"]


def test_discovery_reports_outage_honestly():
    fakeweb.reset("down")
    paras, sents, ctx = _ctx()
    res = discover_sources(DOC, paras, sents, ctx, "en")
    assert res.sources == []
    statuses = {p["status"] for p in res.coverage["providers"] if p["name"] not in ("brave", "google_cse")}
    assert statuses <= {"unavailable", "not_used"}
    assert "coverage gap" in res.coverage["summary"]


def test_offline_fails_fast_with_circuit_breaker():
    fakeweb.reset("offline")
    paras, sents, ctx = _ctx()
    res = discover_sources(DOC, paras, sents, ctx, "en")
    for p in res.coverage["providers"]:
        if p.get("requests"):
            # The breaker opens after two connection errors; at most the
            # requests already in flight on the 6 search threads still go out.
            assert p["requests"] <= 6
            assert p["status"] == "unavailable"
        if p.get("queries", 0) > 6:
            assert p["requests"] < p["queries"]


def test_external_search_disabled_sends_nothing():
    paras, sents, ctx = _ctx()
    res = discover_sources(DOC, paras, sents, ctx, "en", external_search=False)
    assert fakeweb.state["requests"] == []
    assert res.coverage["external_search"] is False


def test_ssrf_private_addresses_blocked():
    with pytest.raises(FetchBlocked):
        fetch_resource(fakeweb.client(), "http://169.254.169.254/latest/meta-data", 1000)


def test_real_ssrf_check_rejects_loopback_schemes_and_ports():
    with pytest.raises(FetchBlocked):
        REAL_CHECK_PUBLIC_HOST("http://127.0.0.1/")
    with pytest.raises(FetchBlocked):
        REAL_CHECK_PUBLIC_HOST("http://[::1]/")
    with pytest.raises(FetchBlocked):
        REAL_CHECK_PUBLIC_HOST("ftp://example.org/file")
    with pytest.raises(FetchBlocked):
        REAL_CHECK_PUBLIC_HOST("http://example.org:8080/")


def test_robots_txt_respected():
    robots = RobotsCache(fakeweb.client(), "tests")
    assert robots.allowed("https://repo.example.org/rev.pdf")
    assert not robots.allowed("https://repo.example.org/private/x.pdf")


def test_redirect_to_private_address_blocked():
    def handler(req):
        if req.url.host == "public.example.org":
            return httpx.Response(302, headers={"location": "http://10.0.0.5/secret"})
        return httpx.Response(200, text="secret")

    c = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(FetchBlocked):
        fetch_resource(c, "https://public.example.org/start", 1000)


def test_html_to_text_skips_boilerplate():
    html = "<html><head><title>T</title><script>evil()</script></head><body><nav>Home About</nav><p>" + "Real article sentence with content. " * 4 + "</p><footer>Copyright</footer></body></html>"
    text, title = html_to_text(html)
    assert title == "T" and "Real article" in text and "evil" not in text and "Home About" not in text
