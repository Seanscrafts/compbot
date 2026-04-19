"""
Competition discovery module for CompBot.
Scrapes SA competition aggregator sites and returns fresh URLs.

Strategy: scrape date-sorted listing pages (newest first) rather than sitemaps.
Stop pagination early once we hit too many already-known URLs in a row.
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

# Each source can have:
#   listing  — paginated listing URL (append ?page=N or &paged=N)
#   sitemap  — fallback XML sitemap
#   url_pattern — regex that individual competition URLs must match
# ConsumerRewards has no listing page — competitions are fixed known URLs.
# We scrape the homepage to find active competition slugs.
CONSUMER_REWARDS_BASE = "https://consumerrewards.co.za"
CONSUMER_REWARDS_SKIP = {
    "", "become-an-advertiser", "blog", "leaderboards",
    "product-discovery", "tablet", "generator", "plans",
    "privacy-policy", "terms", "contact",
}

def _discover_consumer_rewards(known_urls: set) -> list[dict]:
    """Scrape consumerrewards.co.za homepage for active competition slugs."""
    try:
        r = httpx.get(CONSUMER_REWARDS_BASE, headers=HEADERS, timeout=15, follow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        found = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip().rstrip("/")
            # Handle both absolute and relative URLs
            if href.startswith(CONSUMER_REWARDS_BASE):
                slug = href.replace(CONSUMER_REWARDS_BASE, "").lstrip("/")
            elif href.startswith("/") and not href.startswith("//"):
                slug = href.lstrip("/")
            else:
                continue
            if not slug or slug in CONSUMER_REWARDS_SKIP or "#" in slug or "?" in slug:
                continue
            url = f"{CONSUMER_REWARDS_BASE}/{slug}"
            if url in seen or url in known_urls:
                continue
            seen.add(url)
            found.append({"url": url, "source": "ConsumerRewards"})
        return found
    except Exception as e:
        console.print(f"[yellow]  ConsumerRewards failed: {e}[/yellow]")
        return []


SOURCES = [
    {
        "name": "GivingMore",
        "listing": "https://givingmore.co.za/online-competition-club/all-prizes/?orderby=date",
        "listing_page_param": "paged",   # WordPress pagination param
        "sitemap": "https://givingmore.co.za/sitemap.xml",
        "url_pattern": r"https://givingmore\.co\.za/competitions/[a-z0-9\-]+/?$",
    },
    {
        "name": "WinWinSA",
        "listing": "https://winwinsa.co.za/competitions/",
        "listing_page_param": "page",
        "url_pattern": r"https://winwinsa\.co\.za/competition/[a-z0-9\-]+/?$",
    },
    {
        "name": "AllCompetitions",
        "listing": "https://www.allcompetitions.co.za/",
        "listing_page_param": "page",
        "url_pattern": r"https://www\.allcompetitions\.co\.za/competition/[a-z0-9\-]+/?$",
    },
]


def _scrape_listing_page(base_url: str, pattern: str, page_param: str, page_num: int) -> list[str]:
    """Fetch one page of a listing and return matching competition URLs."""
    sep = "&" if "?" in base_url else "?"
    url = f"{base_url}{sep}{page_param}={page_num}" if page_num > 1 else base_url
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
        console.print(f"[yellow]  Page {page_num} failed: {e}[/yellow]")
        return []


def _scrape_sitemap(url: str, pattern: str, limit: int = 200) -> list[str]:
    """Fallback: extract competition URLs from an XML sitemap."""
    try:
        r = httpx.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        r.raise_for_status()
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


def discover_all(
    limit_per_source: int = 50,
    known_urls: set | None = None,
    stop_after_known: int = 10,
) -> list[dict]:
    """
    Scrape all configured sources, newest-first, and return:
        [{"url": str, "source": str}, ...]

    known_urls: set of URLs already in DB — used for early-stop pagination.
    stop_after_known: stop paginating a source after this many consecutive known URLs.
    """
    known_urls = known_urls or set()
    results = []

    for source in SOURCES:
        name = source["name"]
        pattern = source["url_pattern"]
        page_param = source.get("listing_page_param", "page")
        console.print(f"[dim]Scraping {name}...[/dim]")

        seen = set()
        collected = []

        if source.get("listing"):
            consecutive_known = 0
            for page_num in range(1, 20):  # max 20 pages
                page_urls = _scrape_listing_page(source["listing"], pattern, page_param, page_num)
                if not page_urls:
                    break

                for u in page_urls:
                    if u in seen:
                        continue
                    seen.add(u)
                    if u in known_urls:
                        consecutive_known += 1
                    else:
                        consecutive_known = 0
                        collected.append(u)

                if consecutive_known >= stop_after_known:
                    console.print(f"[dim]  Stopped at page {page_num} ({stop_after_known} consecutive known)[/dim]")
                    break

                if len(collected) >= limit_per_source:
                    break

            console.print(f"[dim]  Listing: {len(collected)} new URLs[/dim]")

        # Sitemap fallback if listing yielded nothing
        if not collected and source.get("sitemap"):
            all_urls = _scrape_sitemap(source["sitemap"], pattern, limit=limit_per_source * 3)
            for u in all_urls:
                if u not in known_urls and u not in seen:
                    collected.append(u)
                    seen.add(u)
            console.print(f"[dim]  Sitemap fallback: {len(collected)} new URLs[/dim]")

        for url in collected[:limit_per_source]:
            results.append({"url": url, "source": name})

    # ConsumerRewards — fixed URL discovery
    console.print("[dim]Scraping ConsumerRewards...[/dim]")
    cr_items = _discover_consumer_rewards(known_urls)
    console.print(f"[dim]  Found: {len(cr_items)} new URLs[/dim]")
    results.extend(cr_items)

    return results
