"""
Competition discovery module for CompBot.
Scrapes SA competition aggregator sites and returns fresh URLs.
"""

import re
import httpx
from bs4 import BeautifulSoup
from rich.console import Console

console = Console()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "en-ZA,en;q=0.9",
}

SOURCES = [
    {
        "name": "GivingMore",
        "homepage": "https://givingmore.co.za",
        "sitemap": "https://givingmore.co.za/sitemap.xml",
        "url_pattern": r"https://givingmore\.co\.za/competitions/[a-z0-9\-]+/?$",
    },
]


def _scrape_homepage(url: str, pattern: str) -> list[str]:
    """Scrape competition links directly from a homepage."""
    try:
        r = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        seen = set()
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"].rstrip("/")
            if re.match(pattern, href) and href not in seen:
                seen.add(href)
                links.append(href)
        return links
    except Exception as e:
        console.print(f"[yellow]Homepage scrape failed: {e}[/yellow]")
        return []


def _scrape_sitemap(url: str, pattern: str, limit: int = 200) -> list[str]:
    """Extract competition URLs from an XML sitemap."""
    try:
        r = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        r.raise_for_status()
        # Parse URLs from sitemap
        urls = re.findall(r"<loc>(https?://[^<]+)</loc>", r.text)
        matched = []
        seen = set()
        for u in urls:
            u = u.rstrip("/")
            if re.match(pattern, u) and u not in seen:
                seen.add(u)
                matched.append(u)
                if len(matched) >= limit:
                    break
        return matched
    except Exception as e:
        console.print(f"[yellow]Sitemap scrape failed: {e}[/yellow]")
        return []


def discover_all(limit_per_source: int = 50) -> list[dict]:
    """
    Scrape all configured sources and return list of dicts:
    {"url": str, "source": str}
    """
    results = []

    for source in SOURCES:
        name = source["name"]
        pattern = source["url_pattern"]
        console.print(f"[dim]Scraping {name}...[/dim]")

        urls = []

        # Try sitemap first (more comprehensive)
        if source.get("sitemap"):
            urls = _scrape_sitemap(source["sitemap"], pattern, limit=limit_per_source)
            console.print(f"[dim]  Sitemap: {len(urls)} URLs[/dim]")

        # Fall back to homepage if sitemap yielded nothing
        if not urls and source.get("homepage"):
            urls = _scrape_homepage(source["homepage"], pattern)
            console.print(f"[dim]  Homepage: {len(urls)} URLs[/dim]")

        # Always also grab homepage for freshest listings
        if source.get("homepage"):
            fresh = _scrape_homepage(source["homepage"], pattern)
            for u in fresh:
                if u not in [r["url"] for r in results] and u not in urls:
                    urls.insert(0, u)  # prepend so newest run first

        for url in urls[:limit_per_source]:
            results.append({"url": url, "source": name})

    return results
