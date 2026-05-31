"""
Job Scraper + Keyword Digest System
Moussa Allamine — Development Economics PhD
Runs daily via GitHub Actions, sends a Gmail digest of relevant opportunities.
100% free — no AI API required. Uses keyword matching to filter listings.
"""

import os
import json
import hashlib
import smtplib
import datetime
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

GMAIL_SENDER   = os.environ["GMAIL_SENDER"]
GMAIL_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
GMAIL_TO       = os.environ["GMAIL_TO"]

SEEN_FILE = Path("data/seen_jobs.json")

# ─────────────────────────────────────────────
# KEYWORD FILTER
# Jobs must match at least one STRONG keyword,
# or two or more GENERAL keywords to pass.
# ─────────────────────────────────────────────

STRONG_KEYWORDS = [
    "evaluation", "econometric", "econometrics", "impact evaluation",
    "difference-in-differences", "causal inference", "research assistant",
    "policy analysis", "development economics", "spatial", "geospatial",
    "africa", "sub-saharan", "chad", "sahel", "francophone",
    "fiscal", "imf", "world bank", "afdb", "undp", "unu",
    "deforestation", "environment", "natural resource",
    "monitoring", "m&e", "results framework", "theory of change",
    "phd", "doctoral", "fellowship", "internship", "consultant", "consultancy",
    "short-term", "stc", "remote", "télétravail", "à distance",
    "évaluation", "économiste", "recherche", "développement",
    "données", "analyse", "quantitative", "statistique",
]

NEGATIVE_KEYWORDS = [
    "software engineer", "marketing", "sales", "accountant",
    "nurse", "driver", "security guard", "chef", "teacher k-12",
    "real estate", "insurance agent", "customer service",
]


def keyword_score(job):
    """
    Returns (score, matched_keywords) where score is:
    3 = strong match (2+ strong keywords)
    2 = moderate match (1 strong keyword)
    1 = weak match (general relevance)
    0 = not relevant
    """
    text = (job["title"] + " " + job["description"] + " " + job["org"]).lower()

    # Reject if negative keyword found
    for neg in NEGATIVE_KEYWORDS:
        if neg in text:
            return 0, []

    matched = [kw for kw in STRONG_KEYWORDS if kw in text]

    if len(matched) >= 3:
        return 3, matched
    elif len(matched) == 2:
        return 2, matched
    elif len(matched) == 1:
        return 1, matched
    return 0, []


SCORE_LABELS = {
    3: ("🟢 Strong match", "#1a7a4a"),
    2: ("🔵 Good match",   "#2e86de"),
    1: ("🟡 Worth a look", "#f39c12"),
}

# ─────────────────────────────────────────────
# HTTP HELPER
# ─────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}


def safe_get(url, timeout=15):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"  ⚠️  Failed: {url} — {e}")
        return None


# ─────────────────────────────────────────────
# SCRAPERS
# ─────────────────────────────────────────────

def scrape_unu_wider():
    jobs = []
    r = safe_get("https://www.wider.unu.edu/opportunities?f%5B0%5D=field_opportunity_status%3Acurrent")
    if not r:
        return jobs
    soup = BeautifulSoup(r.text, "html.parser")
    for block in soup.select("article, .view-row, .opportunity"):
        title_el = block.find(["h2", "h3", "h4", "a"])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        link_el = block.find("a", href=True)
        if not link_el:
            continue
        href = link_el["href"]
        url = ("https://www.wider.unu.edu" + href) if href.startswith("/") else href
        desc = block.get_text(" ", strip=True)[:600]
        deadline_match = re.search(r"Closing date[:\s]+([A-Za-z0-9 ,]+)", desc)
        deadline = deadline_match.group(1).strip() if deadline_match else "See link"
        if title and url:
            jobs.append({"title": title, "org": "UNU-WIDER", "url": url,
                         "deadline": deadline, "description": desc, "source": "UNU-WIDER"})
    return jobs


def scrape_reliefweb():
    jobs = []
    api_url = (
        "https://api.reliefweb.int/v1/jobs?appname=moussa-scraper"
        "&profile=list&limit=25&sort[]=date.created:desc"
        "&query[value]=evaluation+research+economist+africa+monitoring"
        "&query[operator]=OR"
    )
    r = safe_get(api_url)
    if not r:
        return jobs
    try:
        data = r.json()
        for item in data.get("data", []):
            fields = item.get("fields", {})
            source_list = fields.get("source", [{}])
            org = source_list[0].get("name", "Unknown") if source_list else "Unknown"
            jobs.append({
                "title": fields.get("title", ""),
                "org": org,
                "url": fields.get("url_alias", "https://reliefweb.int/jobs"),
                "deadline": fields.get("date", {}).get("closing", "See link"),
                "description": fields.get("body", "")[:600],
                "source": "ReliefWeb"
            })
    except Exception as e:
        print(f"  ⚠️  ReliefWeb parse error: {e}")
    return jobs


def scrape_devex():
    jobs = []
    feeds = [
        "https://www.devex.com/jobs/search.rss?keywords=evaluation+economist+africa&location=remote",
        "https://www.devex.com/jobs/search.rss?keywords=research+assistant+development&location=remote",
    ]
    for feed_url in feeds:
        r = safe_get(feed_url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "xml")
        for item in soup.find_all("item")[:15]:
            title = item.find("title").get_text(strip=True) if item.find("title") else ""
            url = item.find("link").get_text(strip=True) if item.find("link") else ""
            desc_raw = item.find("description").get_text() if item.find("description") else ""
            desc = BeautifulSoup(desc_raw, "html.parser").get_text()[:500]
            pub_date = item.find("pubDate").get_text(strip=True) if item.find("pubDate") else "See link"
            if title:
                jobs.append({"title": title, "org": "via Devex", "url": url,
                             "deadline": pub_date, "description": desc, "source": "Devex"})
    return jobs


def scrape_world_bank():
    jobs = []
    r = safe_get("https://jobs.worldbank.org/en/jobs/search#?term=evaluation%20economist%20africa")
    if not r:
        return jobs
    soup = BeautifulSoup(r.text, "html.parser")
    for card in soup.select(".job-card, .search-result, article")[:10]:
        title_el = card.find(["h2", "h3", "h4"])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        link_el = card.find("a", href=True)
        url = link_el["href"] if link_el else "https://jobs.worldbank.org"
        if not url.startswith("http"):
            url = "https://jobs.worldbank.org" + url
        desc = card.get_text(" ", strip=True)[:500]
        jobs.append({"title": title, "org": "World Bank Group", "url": url,
                     "deadline": "See link", "description": desc, "source": "World Bank"})
    return jobs


def scrape_afdb():
    jobs = []
    r = safe_get("https://www.afdb.org/en/about/careers/current-vacancies")
    if not r:
        return jobs
    soup = BeautifulSoup(r.text, "html.parser")
    for row in soup.select("tr, .vacancy-item, .job-listing")[:20]:
        title_el = row.find(["a", "h3", "h4"])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if len(title) < 5:
            continue
        link_el = row.find("a", href=True)
        url = link_el["href"] if link_el else "https://www.afdb.org/en/about/careers"
        if not url.startswith("http"):
            url = "https://www.afdb.org" + url
        desc = row.get_text(" ", strip=True)[:500]
        jobs.append({"title": title, "org": "African Development Bank", "url": url,
                     "deadline": "See link", "description": desc, "source": "AfDB"})
    return jobs


def scrape_undp():
    jobs = []
    r = safe_get("https://jobs.undp.org/cj_view_jobs.cfm?search=evaluation%20economist%20research")
    if not r:
        return jobs
    soup = BeautifulSoup(r.text, "html.parser")
    for row in soup.select("tr")[:20]:
        title_el = row.find("a")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        url = title_el.get("href", "https://jobs.undp.org")
        if not url.startswith("http"):
            url = "https://jobs.undp.org" + url
        desc = row.get_text(" ", strip=True)[:500]
        jobs.append({"title": title, "org": "UNDP", "url": url,
                     "deadline": "See link", "description": desc, "source": "UNDP"})
    return jobs


def scrape_ferdi():
    jobs = []
    r = safe_get("https://www.ferdi.fr/en/jobs-and-grants")
    if not r:
        return jobs
    soup = BeautifulSoup(r.text, "html.parser")
    for item in soup.select("article, .job-item, .field-item")[:10]:
        title_el = item.find(["h2", "h3", "a"])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if len(title) < 5:
            continue
        link_el = item.find("a", href=True)
        url = link_el["href"] if link_el else "https://www.ferdi.fr/en/jobs-and-grants"
        if not url.startswith("http"):
            url = "https://www.ferdi.fr" + url
        desc = item.get_text(" ", strip=True)[:500]
        jobs.append({"title": title, "org": "FERDI", "url": url,
                     "deadline": "See link", "description": desc, "source": "FERDI"})
    return jobs


def scrape_cerdi():
    jobs = []
    r = safe_get("https://cerdi.uca.fr/version-francaise/la-recherche/les-evenements/offres-d-emploi")
    if not r:
        return jobs
    soup = BeautifulSoup(r.text, "html.parser")
    for item in soup.select("article, .offre, li")[:15]:
        title_el = item.find(["h2", "h3", "a"])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if len(title) < 5 or len(title) > 200:
            continue
        link_el = item.find("a", href=True)
        url = link_el["href"] if link_el else "https://cerdi.uca.fr"
        if not url.startswith("http"):
            url = "https://cerdi.uca.fr" + url
        desc = item.get_text(" ", strip=True)[:500]
        jobs.append({"title": title, "org": "CERDI / UCA", "url": url,
                     "deadline": "See link", "description": desc, "source": "CERDI"})
    return jobs


def scrape_afd():
    jobs = []
    r = safe_get("https://www.afd.fr/en/page-emploi-afd")
    if not r:
        return jobs
    soup = BeautifulSoup(r.text, "html.parser")
    for item in soup.select("article, .job, .offre")[:10]:
        title_el = item.find(["h2", "h3", "a"])
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if len(title) < 5:
            continue
        link_el = item.find("a", href=True)
        url = link_el["href"] if link_el else "https://www.afd.fr"
        if not url.startswith("http"):
            url = "https://www.afd.fr" + url
        desc = item.get_text(" ", strip=True)[:500]
        jobs.append({"title": title, "org": "AFD", "url": url,
                     "deadline": "See link", "description": desc, "source": "AFD"})
    return jobs


# ─────────────────────────────────────────────
# DEDUPLICATION
# ─────────────────────────────────────────────

def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text())
    return {}


def save_seen(seen):
    SEEN_FILE.parent.mkdir(exist_ok=True)
    SEEN_FILE.write_text(json.dumps(seen, indent=2))


def job_id(job):
    return hashlib.md5((job["title"] + job["url"]).encode()).hexdigest()


def filter_new(jobs, seen):
    new = []
    for j in jobs:
        jid = job_id(j)
        if jid not in seen:
            j["id"] = jid
            new.append(j)
    return new


# ─────────────────────────────────────────────
# EMAIL DIGEST
# ─────────────────────────────────────────────

def build_html_digest(jobs_by_score, run_date, total_new):
    all_jobs = []
    for score in [3, 2, 1]:
        all_jobs.extend(jobs_by_score.get(score, []))

    if not all_jobs:
        body = """
        <div style='text-align:center;padding:40px;color:#666;'>
            <p style='font-size:18px;'>✅ Scraper ran successfully</p>
            <p>No new relevant listings found today. Check back tomorrow.</p>
        </div>"""
    else:
        cards = ""
        for j in all_jobs:
            sc = j.get("score", 1)
            label, color = SCORE_LABELS.get(sc, ("🟡 Worth a look", "#f39c12"))
            kws = ", ".join(j.get("matched_keywords", [])[:5])
            cards += f"""
            <div style="border:1px solid #e0e0e0;border-radius:8px;padding:18px;
                        margin-bottom:16px;background:#fff;">
              <div style="display:flex;justify-content:space-between;
                          align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px;">
                <span style="background:{color};color:#fff;padding:4px 12px;
                             border-radius:12px;font-size:13px;font-weight:bold;">
                  {label}
                </span>
                <span style="color:#c0392b;font-size:13px;font-weight:bold;">
                  ⏰ {j['deadline']}
                </span>
              </div>
              <h3 style="margin:6px 0 4px;font-size:16px;color:#1a1a2e;">
                <a href="{j['url']}" style="color:#1a1a2e;text-decoration:none;">
                  {j['title']}
                </a>
              </h3>
              <p style="margin:2px 0 8px;color:#555;font-size:13px;">
                <strong>{j['org']}</strong> &nbsp;·&nbsp;
                <span style="color:#888;">{j['source']}</span>
              </p>
              <p style="margin:4px 0 10px;color:#666;font-size:12px;font-style:italic;">
                🏷️ Matched: {kws}
              </p>
              <a href="{j['url']}"
                 style="display:inline-block;padding:7px 16px;background:#1a1a2e;
                        color:#fff;border-radius:5px;font-size:13px;text-decoration:none;">
                View opportunity →
              </a>
            </div>"""
        body = cards

    n3 = len(jobs_by_score.get(3, []))
    n2 = len(jobs_by_score.get(2, []))
    n1 = len(jobs_by_score.get(1, []))

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
</head>
<body style="font-family:Georgia,serif;max-width:680px;margin:0 auto;
             padding:20px;background:#f5f6fa;color:#222;">

  <div style="background:#1a1a2e;color:#fff;padding:28px 24px;
              border-radius:10px 10px 0 0;">
    <h1 style="margin:0;font-size:22px;">📋 Daily Opportunity Digest</h1>
    <p style="margin:6px 0 0;opacity:0.8;font-size:14px;">
      {run_date} &nbsp;·&nbsp;
      {len(all_jobs)} relevant listings from {total_new} new scraped today
    </p>
  </div>

  <div style="background:#fff;padding:20px 24px;
              border-radius:0 0 10px 10px;margin-bottom:20px;">

    <div style="display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap;">
      <span style="background:#1a7a4a;color:#fff;padding:5px 14px;
                   border-radius:20px;font-size:13px;">
        🟢 Strong: {n3}
      </span>
      <span style="background:#2e86de;color:#fff;padding:5px 14px;
                   border-radius:20px;font-size:13px;">
        🔵 Good: {n2}
      </span>
      <span style="background:#f39c12;color:#fff;padding:5px 14px;
                   border-radius:20px;font-size:13px;">
        🟡 Worth a look: {n1}
      </span>
    </div>

    {body}

    <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
    <p style="font-size:12px;color:#999;text-align:center;">
      Automated digest for Moussa Allamine · Development Economics PhD · UCA/LEO<br>
      Portals: World Bank · AfDB · UNDP · UNU-WIDER · FERDI · CERDI · AFD ·
      ReliefWeb · Devex<br>
      <em>No AI used — keyword matching only · 100% free</em>
    </p>
  </div>
</body>
</html>"""
    return html


def send_email(html, n_jobs, run_date):
    if n_jobs == 0:
        subject = f"[Job Digest] No new matches today — {run_date}"
    else:
        subject = f"[Job Digest] {n_jobs} new opportunities — {run_date}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = GMAIL_TO
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_SENDER, GMAIL_PASSWORD)
        server.sendmail(GMAIL_SENDER, GMAIL_TO, msg.as_string())
    print(f"✅ Email sent to {GMAIL_TO}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

SCRAPERS = [
    scrape_unu_wider,
    scrape_reliefweb,
    scrape_devex,
    scrape_world_bank,
    scrape_afdb,
    scrape_undp,
    scrape_ferdi,
    scrape_cerdi,
    scrape_afd,
]


def main():
    run_date = datetime.date.today().strftime("%B %d, %Y")
    print(f"\n🔍 Job Scraper starting — {run_date}\n")

    # 1. Scrape all portals
    all_jobs = []
    for fn in SCRAPERS:
        name = fn.__name__.replace("scrape_", "").upper()
        print(f"  Scraping {name}...")
        try:
            found = fn()
            print(f"    → {len(found)} listings")
            all_jobs.extend(found)
        except Exception as e:
            print(f"    ⚠️  Error: {e}")

    print(f"\n📦 Total raw listings: {len(all_jobs)}")

    # 2. Deduplicate
    seen = load_seen()
    new_jobs = filter_new(all_jobs, seen)
    print(f"🆕 New (not seen before): {len(new_jobs)}")

    # 3. Keyword filter + score
    jobs_by_score = {3: [], 2: [], 1: []}
    for job in new_jobs:
        score, matched = keyword_score(job)
        if score > 0:
            job["score"] = score
            job["matched_keywords"] = matched
            jobs_by_score[score].append(job)

    total_relevant = sum(len(v) for v in jobs_by_score.values())
    print(f"✅ Relevant after keyword filter: {total_relevant}")
    print(f"   🟢 Strong: {len(jobs_by_score[3])}")
    print(f"   🔵 Good:   {len(jobs_by_score[2])}")
    print(f"   🟡 Weak:   {len(jobs_by_score[1])}")

    # 4. Mark all new jobs as seen
    for j in new_jobs:
        seen[j["id"]] = {
            "title": j["title"],
            "seen_on": str(datetime.date.today())
        }
    save_seen(seen)

    # 5. Build and send digest
    html = build_html_digest(jobs_by_score, run_date, len(new_jobs))
    send_email(html, total_relevant, run_date)
    print("\n🎉 Done.")


if __name__ == "__main__":
    main()
