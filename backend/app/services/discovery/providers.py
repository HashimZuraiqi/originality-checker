"""External search providers.

Every provider returns `Candidate`s: metadata plus whatever text the search
API itself exposes (abstract or snippet). Full text is retrieved later, and
only for the best candidates (retrieval.py).

Academic APIs (OpenAlex, Crossref, Semantic Scholar, arXiv) and Wikipedia are
free and need no key. Open-web search needs a licensed search API key (Brave
Search API or Google Programmable Search); without one, the open web is not
searched and the coverage statement says so.
"""

import html
import re
from dataclasses import dataclass, field

from defusedxml import ElementTree as ET

from ...config import Settings
from .http import ProviderClient, ProviderError
from .queries import Query


@dataclass
class Candidate:
    provider: str
    title: str
    kind: str  # academic | encyclopedia | web
    url: str | None = None
    doi: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    text: str = ""  # abstract / snippet / extract available from the search API
    text_kind: str = "snippet"  # snippet | abstract | full_text
    pdf_url: str | None = None
    license: str | None = None
    rank: int = 0
    query_ids: list[str] = field(default_factory=list)
    provider_id: str | None = None  # e.g. Wikipedia pageid, OpenAlex id


def _strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).replace("  ", " ").strip()


def _norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = doi.strip()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", doi, flags=re.I)
    return doi.lower() or None


class Provider:
    name = "base"
    label = ""
    kind = "academic"
    families: tuple[str, ...] = ()
    requires_key = False

    def __init__(self, settings: Settings, client: ProviderClient):
        self.settings = settings
        self.client = client

    def configured(self) -> bool:
        return True

    def search(self, query: Query) -> list[Candidate]:
        raise NotImplementedError


class OpenAlexProvider(Provider):
    name = "openalex"
    label = "OpenAlex (250M+ scholarly works)"
    families = ("keyword", "reference", "phrase")
    BASE = "https://api.openalex.org/works"
    FIELDS = "id,doi,title,display_name,publication_year,authorships,abstract_inverted_index,best_oa_location,open_access,primary_location"

    def _params(self, extra: dict) -> dict:
        params = {"select": self.FIELDS, **extra}
        if self.settings.contact_email:
            params["mailto"] = self.settings.contact_email
        return params

    @staticmethod
    def abstract_from_inverted(index: dict | None) -> str:
        if not index:
            return ""
        positions = [(pos, word) for word, poss in index.items() for pos in poss]
        return " ".join(w for _, w in sorted(positions))

    def _candidate(self, w: dict, rank: int) -> Candidate:
        best = w.get("best_oa_location") or {}
        primary = w.get("primary_location") or {}
        abstract = self.abstract_from_inverted(w.get("abstract_inverted_index"))
        return Candidate(
            provider=self.name,
            title=w.get("display_name") or w.get("title") or "Untitled work",
            kind="academic",
            url=best.get("landing_page_url") or primary.get("landing_page_url") or w.get("doi") or w.get("id"),
            doi=_norm_doi(w.get("doi")),
            authors=[a.get("author", {}).get("display_name", "") for a in (w.get("authorships") or [])][:8],
            year=w.get("publication_year"),
            text=abstract,
            text_kind="abstract" if abstract else "snippet",
            pdf_url=best.get("pdf_url"),
            license=best.get("license"),
            rank=rank,
            provider_id=w.get("id"),
        )

    def search(self, query: Query) -> list[Candidate]:
        per_page = 3 if query.family == "reference" else 8
        q = f'"{query.text}"' if query.family == "phrase" else query.text
        data = self.client.get_json(self.BASE, self._params({"search": q, "per-page": per_page}))
        return [self._candidate(w, i) for i, w in enumerate(data.get("results") or [])]

    def by_doi(self, doi: str) -> Candidate | None:
        try:
            w = self.client.get_json(f"{self.BASE}/https://doi.org/{doi}", self._params({}))
        except ProviderError as e:
            if e.status == 404:
                return None
            raise
        return self._candidate(w, 0)


class CrossrefProvider(Provider):
    name = "crossref"
    label = "Crossref (DOI registry metadata)"
    families = ("reference", "keyword")
    BASE = "https://api.crossref.org/works"

    def _params(self, extra: dict) -> dict:
        params = dict(extra)
        if self.settings.contact_email:
            params["mailto"] = self.settings.contact_email
        return params

    def _candidate(self, item: dict, rank: int) -> Candidate:
        title = (item.get("title") or ["Untitled work"])[0]
        issued = (item.get("issued") or {}).get("date-parts") or [[None]]
        abstract = _strip_tags(item.get("abstract", ""))
        lic = (item.get("license") or [{}])[0].get("URL")
        return Candidate(
            provider=self.name,
            title=_strip_tags(title),
            kind="academic",
            url=item.get("URL"),
            doi=_norm_doi(item.get("DOI")),
            authors=[f"{a.get('given', '')} {a.get('family', '')}".strip() for a in (item.get("author") or [])][:8],
            year=issued[0][0] if issued and issued[0] else None,
            text=abstract,
            text_kind="abstract" if abstract else "snippet",
            license=lic,
            rank=rank,
        )

    def search(self, query: Query) -> list[Candidate]:
        rows = 3 if query.family == "reference" else 6
        data = self.client.get_json(
            self.BASE,
            self._params({"query.bibliographic": query.text, "rows": rows, "select": "DOI,title,author,issued,URL,abstract,license"}),
        )
        return [self._candidate(it, i) for i, it in enumerate((data.get("message") or {}).get("items") or [])]

    def verify_doi(self, doi: str) -> dict:
        """Look a DOI up in the registry: {'status': 'verified'|'not_found'|'unchecked', ...}."""
        try:
            data = self.client.get_json(f"{self.BASE}/{doi}", self._params({}))
        except ProviderError as e:
            if e.status == 404:
                return {"status": "not_found"}
            return {"status": "unchecked", "reason": str(e)}
        c = self._candidate(data.get("message") or {}, 0)
        return {"status": "verified", "title": c.title, "year": c.year, "authors": c.authors[:3], "url": c.url}


class SemanticScholarProvider(Provider):
    name = "semantic_scholar"
    label = "Semantic Scholar (AI2 academic graph)"
    families = ("keyword", "reference")
    BASE = "https://api.semanticscholar.org/graph/v1/paper/search"

    def search(self, query: Query) -> list[Candidate]:
        headers = {"x-api-key": self.settings.semantic_scholar_api_key} if self.settings.semantic_scholar_api_key else None
        data = self.client.get_json(
            self.BASE,
            {"query": query.text[:300], "limit": 3 if query.family == "reference" else 8, "fields": "title,abstract,url,year,authors,externalIds,openAccessPdf"},
            headers=headers,
        )
        out = []
        for i, p in enumerate(data.get("data") or []):
            pdf = (p.get("openAccessPdf") or {}).get("url") or None
            out.append(
                Candidate(
                    provider=self.name,
                    title=p.get("title") or "Untitled paper",
                    kind="academic",
                    url=p.get("url"),
                    doi=_norm_doi((p.get("externalIds") or {}).get("DOI")),
                    authors=[a.get("name", "") for a in (p.get("authors") or [])][:8],
                    year=p.get("year"),
                    text=p.get("abstract") or "",
                    text_kind="abstract" if p.get("abstract") else "snippet",
                    pdf_url=pdf,
                    rank=i,
                    provider_id=p.get("paperId"),
                )
            )
        return out


class ArxivProvider(Provider):
    name = "arxiv"
    label = "arXiv (open-access preprints)"
    families = ("keyword",)
    BASE = "https://export.arxiv.org/api/query"
    NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

    def search(self, query: Query) -> list[Candidate]:
        terms = [t for t in re.findall(r"[A-Za-z0-9]+", query.text) if len(t) > 2][:6]
        if not terms:
            return []
        search = " AND ".join(f"all:{t}" for t in terms)
        xml_text = self.client.get_text(self.BASE, {"search_query": search, "start": 0, "max_results": 5})
        root = ET.fromstring(xml_text)
        out = []
        for i, e in enumerate(root.findall("a:entry", self.NS)):
            pdf = next((ln.get("href") for ln in e.findall("a:link", self.NS) if ln.get("title") == "pdf"), None)
            published = (e.findtext("a:published", "", self.NS) or "")[:4]
            out.append(
                Candidate(
                    provider=self.name,
                    title=" ".join((e.findtext("a:title", "", self.NS) or "").split()),
                    kind="academic",
                    url=e.findtext("a:id", None, self.NS),
                    doi=_norm_doi(e.findtext("arxiv:doi", None, self.NS)),
                    authors=[a.findtext("a:name", "", self.NS) for a in e.findall("a:author", self.NS)][:8],
                    year=int(published) if published.isdigit() else None,
                    text=" ".join((e.findtext("a:summary", "", self.NS) or "").split()),
                    text_kind="abstract",
                    pdf_url=pdf,
                    license="arXiv",
                    rank=i,
                )
            )
        return out


class WikipediaProvider(Provider):
    name = "wikipedia"
    label = "Wikipedia (full article text, CC BY-SA)"
    kind = "encyclopedia"
    families = ("phrase", "keyword")

    @property
    def base(self) -> str:
        return f"https://{self.settings.wikipedia_language}.wikipedia.org/w/api.php"

    def search(self, query: Query) -> list[Candidate]:
        q = f'"{query.text}"' if query.family == "phrase" else query.text
        data = self.client.get_json(
            self.base,
            {"action": "query", "list": "search", "srsearch": q, "srlimit": 4, "srprop": "snippet", "format": "json", "formatversion": 2},
        )
        out = []
        for i, r in enumerate((data.get("query") or {}).get("search") or []):
            title = r.get("title", "")
            out.append(
                Candidate(
                    provider=self.name,
                    title=f"{title} — Wikipedia",
                    kind="encyclopedia",
                    url=f"https://{self.settings.wikipedia_language}.wikipedia.org/wiki/{title.replace(' ', '_')}",
                    text=_strip_tags(r.get("snippet", "")),
                    text_kind="snippet",
                    license="CC BY-SA 4.0",
                    rank=i,
                    provider_id=str(r.get("pageid")),
                )
            )
        return out

    def full_text(self, pageid: str) -> str:
        data = self.client.get_json(
            self.base,
            {"action": "query", "prop": "extracts", "explaintext": 1, "exsectionformat": "plain", "pageids": pageid, "format": "json", "formatversion": 2},
        )
        pages = (data.get("query") or {}).get("pages") or []
        return pages[0].get("extract", "") if pages else ""


class BraveProvider(Provider):
    name = "brave"
    label = "Brave Search API (open web)"
    kind = "web"
    families = ("phrase",)
    requires_key = True

    def configured(self) -> bool:
        return bool(self.settings.brave_api_key)

    def search(self, query: Query) -> list[Candidate]:
        data = self.client.get_json(
            "https://api.search.brave.com/res/v1/web/search",
            {"q": f'"{query.text}"', "count": 8},
            headers={"Accept": "application/json", "X-Subscription-Token": self.settings.brave_api_key},
        )
        return [
            Candidate(self.name, _strip_tags(r.get("title", "")) or r.get("url", ""), "web", url=r.get("url"), text=_strip_tags(r.get("description", "")), rank=i)
            for i, r in enumerate((data.get("web") or {}).get("results") or [])
        ]


class GoogleCSEProvider(Provider):
    name = "google_cse"
    label = "Google Programmable Search (open web)"
    kind = "web"
    families = ("phrase",)
    requires_key = True

    def configured(self) -> bool:
        return bool(self.settings.google_cse_key and self.settings.google_cse_cx)

    def search(self, query: Query) -> list[Candidate]:
        data = self.client.get_json(
            "https://www.googleapis.com/customsearch/v1",
            {"key": self.settings.google_cse_key, "cx": self.settings.google_cse_cx, "q": f'"{query.text}"', "num": 8},
        )
        return [
            Candidate(self.name, r.get("title", "") or r.get("link", ""), "web", url=r.get("link"), text=r.get("snippet", ""), rank=i)
            for i, r in enumerate(data.get("items") or [])
        ]


PROVIDER_CLASSES: list[type[Provider]] = [
    OpenAlexProvider,
    CrossrefProvider,
    SemanticScholarProvider,
    ArxivProvider,
    WikipediaProvider,
    BraveProvider,
    GoogleCSEProvider,
]


def provider_enabled(settings: Settings, name: str) -> bool:
    return {
        "openalex": settings.enable_openalex,
        "crossref": settings.enable_crossref,
        "semantic_scholar": settings.enable_semantic_scholar,
        "arxiv": settings.enable_arxiv,
        "wikipedia": settings.enable_wikipedia,
        "brave": True,
        "google_cse": True,
    }[name]
