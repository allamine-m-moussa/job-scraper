"""
Job Scraper + AI Digest System
Moussa Allamine — Development Economics PhD
Runs daily via GitHub Actions, sends a Gmail digest of relevant opportunities.
"""

import os
import json
import hashlib
import smtplib
import datetime
import time
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import anthropic

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

GMAIL_SENDER   = os.environ["GMAIL_SENDER"]       # your Gmail address
GMAIL_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]  # Gmail App Password (not your login password)
GMAIL_TO       = os.environ["GMAIL_TO"]           # where to receive the digest (can be same as sender)
ANTHROPIC_KEY  = os.environ["ANTHROPIC_API_KEY"]

SEEN_FILE = Path("data/seen_jobs.json")           # tracks already-processed listings
MIN_SCORE = 3                                      # only include jobs scored 3+ by Claude

PROFILE_SUMMARY = """
The candidate is Moussa Allamine Mahamat, a first-year PhD student in Development Economics at
Université Clermont Auvergne (LEO laboratory), based between Chad and France.

CORE EXPERTISE:
- Impact evaluation & causal inference: Difference-in-Differences, spatial DiD, event studies,
  local projections, synthetic control, IV, RDD, PSM
- Econometric & data analysis: R, Stata, Python, QGIS, Google Earth Engine, LaTeX, Git
- Geospatial analysis: satellite data (Global Forest Watch, VIIRS), QGIS, GEE
- Policy & strategic evaluation: theory of change, contribution analysis, portfolio review,
  results frameworks, IATI data, coding grids
- Data sources: IMF MONA/WEO, World Bank WDI, ACLED, IATI, Global Forest Watch

RESEARCH TOPICS: deforestation, hydropower, IMF programmes, fiscal space, macroeconomic
sovereignty, conflict economies, natural resources, Chad/DRC/Sub-Saharan Africa

LANGUAGES: French (fluent), English (fluent), Arabic (fluent)

EXPERIENCE:
- ADE Belgium: EU-funded strategic evaluation (EU-DRC, Facility for Refugees Turkey, GAP III)
- CEDESI Chad: UNDP, UNICEF, CEEAC consultancies (social mapping, local development plans)
- Harvard Aspire Leaders Program alumnus
- Working paper: Matebe hydropower and deforestation in Virunga National Park (spatial DiD)

TARGET ROLES: short-term consultancies (STC), research assistantships, internships, fellowships,
evaluation support — at World Bank, AfDB, UNDP, UNU-WIDER, FERDI, CERDI, AFD, EU evaluation
firms, NGOs working on Africa/development/environment/fiscal policy.

NOT a good fit: roles requiring physical relocation from Chad/France, purely private-sector
finance, marketing, non-development tech roles, or psychometrics/education-only roles.
"""

# ─────────────────────────────────────────────
# PORTAL SCRAPERS
# Each function returns a list of dicts:
# {title, org, url, deadline, description, source}
# ─────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}


def safe_get(url, timeout=15):
    """HTTP GET with error handling."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"  ⚠️  Failed to fetch {url}: {e}")
        return None


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
        url = "https://www.wider.unu.edu" + link_el["href"] if link_el and link_el["href"].startswith("/") else (link_el["href"] if link_el else "")
        desc = block.get_text(" ", strip=True)[:500]
        deadline_match = re.search(r"Closing date[:\s]+([A-Za-z0-9 ,]+)", desc)
        deadline = deadline_match.group(1).strip() if deadline_match else "See link"
        if title and url:
            jobs.append({"title": title, "org": "UNU-WIDER", "url": url,
                         "deadline": deadline, "description": desc, "source": "UNU-WIDER"})
    return jobs


def scrape_reliefweb():
    jobs = []
    url = (
        "https://api.reliefweb.int/v1/jobs?appname=moussa-scraper"
        "&filter[field]=career_categories.name&filter[value][]=Donor%20relations%2FFundraising"
        "&filter[operator]=OR"
        "&profile=list&limit=20&sort[]=date.created:desc"
    )
    # Use ReliefWeb's proper API
    api_url = (
        "https://api.reliefweb.int/v1/jobs?appname=moussa-scraper"
        "&profile=list&limit=20&sort[]=date.created:desc"
        "&query[value]=evaluation+OR+econometrics+OR+research+assistant+OR+monitoring+Africa"
        "&query[operator]=OR"
    )
    r = safe_get(api_url)
    if not r:
        return jobs
    try:
        data = r.json()
        for item in data.get("data", []):
            fields = item.get("fields", {})
            jobs.append({
                "title": fields.get("title", ""),
                "org": fields.get("source", [{}])[0].get("name", "Unknown") if fields.get("source") else "Unknown",
                "url": fields.get("url_alias", "https://reliefweb.int/jobs"),
                "deadline": fields.get("date", {}).get("closing", "See link"),
                "description": fields.get("body", "")[:500],
                "source": "ReliefWeb"
            })
    except Exception as e:
        print(f"  ⚠️  ReliefWeb parse error: {e}")
    return jobs


def scrape_devex():
    """Devex blocks scrapers — we use their public RSS instead."""
    jobs = []
    r = safe_get("https://www.devex.com/jobs/search.rss?keywords=evaluation+economist+africa&location=remote")
    if not r:
        return jobs
    soup = BeautifulSoup(r.text, "xml")
    for item in soup.find_all("item")[:15]:
        title = item.find("title").get_text(strip=True) if item.find("title") else ""
        url = item.find("link").get_text(strip=True) if item.find("link") else ""
        desc = BeautifulSoup(item.find("description").get_text(), "html.parser").get_text()[:400] if item.find("description") else ""
        pub_date = item.find("pubDate").get_text(strip=True) if item.find("pubDate") else "See link"
        if title:
            jobs.append({"title": title, "org": "via Devex", "url": url,
                         "deadline": pub_date, "description": desc, "source": "Devex"})
    return jobs


def scrape_world_bank():
    """World Bank Jobs API — public endpoint."""
    jobs = []
    api = "https://search.worldbank.org/api/v2/wds?format=json&qterm=evaluation+research+africa&rows=10&os=0"
    # Use the proper jobs board
    r = safe_get("https://jobs.worldbank.org/en/jobs/search#?term=evaluation%20economist%20africa")
    # Fallback: scrape the jobs search page
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
        desc = card.get_text(" ", strip=True)[:400]
        jobs.append({"title": title, "org": "World Bank Group", "url": url,
                     "deadline": "See link", "description": desc, "source": "World Bank"})
    return jobs


def scrape_afdb():
    jobs = []
    r = safe_get("https://www.afdb.org/en/about/careers/current-vacancies")
    if not r:
        return jobs
    soup = BeautifulSoup(r.text, "html.parser")
    for row in soup.select("tr, .vacancy-item, .job-listing")[:15]:
        cells = row.find_all(["td", "li"])
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
        desc = row.get_text(" ", strip=True)[:400]
        jobs.append({"title": title, "org": "African Development Bank", "url": url,
                     "deadline": "See link", "description": desc, "source": "AfDB"})
    return jobs


def scrape_undp():
    """UNDP uses a jobs API."""
    jobs = []
    api = "https://jobs.undp.org/cj_view_jobs.cfm?cur_job_level=&cur_job_family=&cur_department=&cur_country=&search=evaluation+research+africa"
    r = safe_get("https://jobs.undp.org/cj_view_jobs.cfm?search=evaluation%20economist%20research")
    if not r:
        return jobs
    soup = BeautifulSoup(r.text, "html.parser")
    for row in soup.select("tr")[:15]:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        title_el = row.find("a")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        url = title_el.get("href", "https://jobs.undp.org")
        if not url.startswith("http"):
            url = "https://jobs.undp.org" + url
        desc = row.get_text(" ", strip=True)[:400]
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
        desc = item.get_text(" ", strip=True)[:400]
        jobs.append({"title": title, "org": "FERDI", "url": url,
                     "deadline": "See link", "description": desc, "source": "FERDI"})
    return jobs


def scrape_cerdi():
    jobs = []
    r = safe_get("https://cerdi.uca.fr/version-francaise/la-recherche/les-evenements/offres-d-emploi")
    if not r:
        return jobs
    soup = BeautifulSoup(r.text, "html.parser")
    for item in soup.select("article, .offre, li")[:10]:
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
        desc = item.get_text(" ", strip=True)[:400]
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
        desc = item.get_text(" ", strip=True)[:400]
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
    """Stable hash for a job listing."""
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
# AI SCORING
# ─────────────────────────────────────────────

def score_jobs(jobs):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    scored = []
    for job in jobs:
        prompt = f"""You are evaluating job/consulting opportunities for a development economics PhD student.

CANDIDATE PROFILE:
{PROFILE_SUMMARY}

JOB LISTING:
Title: {job['title']}
Organisation: {job['org']}
Source: {job['source']}
Deadline: {job['deadline']}
Description: {job['description']}

Score this opportunity from 1 to 5:
5 = Excellent fit — directly matches expertise, Africa/development focus, remote-friendly
4 = Good fit — relevant field, some gap in one dimension
3 = Moderate fit — worth reviewing, transferable skills apply
2 = Weak fit — significant mismatch in topic or requirements
1 = Not relevant — wrong field, requires relocation or unrelated skills

Respond ONLY in this exact JSON format, no other text:
{{"score": <1-5>, "reason": "<one sentence, max 20 words>", "action": "<Apply / Review / Skip>"}}"""

        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = response.content[0].text.strip()
            result = json.loads(raw)
            job["score"] = result["score"]
            job["reason"] = result["reason"]
            job["action"] = result["action"]
        except Exception as e:
            print(f"  ⚠️  Scoring failed for '{job['title']}': {e}")
            job["score"] = 0
            job["reason"] = "Could not score"
            job["action"] = "Review"

        time.sleep(0.5)  # gentle rate limiting
        if job["score"] >= MIN_SCORE:
            scored.append(job)

    return sorted(scored, key=lambda x: x["score"], reverse=True)


# ─────────────────────────────────────────────
# EMAIL DIGEST
# ─────────────────────────────────────────────

SCORE_COLORS = {5: "#1a7a4a", 4: "#2e86de", 3: "#f39c12", 2: "#888", 1: "#ccc"}
ACTION_COLORS = {"Apply": "#1a7a4a", "Review": "#2e86de", "Skip": "#aaa"}


def build_html_digest(jobs, run_date):
    if not jobs:
        body = "<p style='color:#666'>No new relevant opportunities found this week. The scraper ran successfully.</p>"
    else:
        cards = ""
        for j in jobs:
            sc = j.get("score", 0)
            color = SCORE_COLORS.get(sc, "#888")
            ac = j.get("action", "Review")
            acolor = ACTION_COLORS.get(ac, "#888")
            cards += f"""
            <div style="border:1px solid #e0e0e0;border-radius:8px;padding:18px;margin-bottom:16px;background:#fff;">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <span style="background:{color};color:#fff;padding:3px 10px;border-radius:12px;font-size:13px;font-weight:bold;">
                  ★ {sc}/5
                </span>
                <span style="background:{acolor};color:#fff;padding:3px 10px;border-radius:12px;font-size:12px;">
                  {ac}
                </span>
              </div>
              <h3 style="margin:8px 0 4px;font-size:16px;color:#1a1a2e;">
                <a href="{j['url']}" style="color:#1a1a2e;text-decoration:none;">{j['title']}</a>
              </h3>
              <p style="margin:2px 0 6px;color:#555;font-size:13px;">
                <strong>{j['org']}</strong> &nbsp;·&nbsp; {j['source']} &nbsp;·&nbsp; 
                <span style="color:#c0392b;">⏰ {j['deadline']}</span>
              </p>
              <p style="margin:6px 0;color:#444;font-size:13px;font-style:italic;">
                💡 {j.get('reason', '')}
              </p>
              <a href="{j['url']}" style="display:inline-block;margin-top:8px;padding:6px 14px;
                 background:#1a1a2e;color:#fff;border-radius:5px;font-size:13px;text-decoration:none;">
                View opportunity →
              </a>
            </div>"""
        body = cards

    counts = {5: 0, 4: 0, 3: 0}
    for j in jobs:
        sc = j.get("score", 0)
        if sc in counts:
            counts[sc] += 1

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="font-family:Georgia,serif;max-width:680px;margin:0 auto;padding:20px;background:#f5f6fa;color:#222;">
  <div style="background:#1a1a2e;color:#fff;padding:28px 24px;border-radius:10px 10px 0 0;">
    <h1 style="margin:0;font-size:22px;">📋 Weekly Opportunity Digest</h1>
    <p style="margin:6px 0 0;opacity:0.8;font-size:14px;">
      {run_date} &nbsp;·&nbsp; {len(jobs)} relevant listings found
    </p>
  </div>
  <div style="background:#fff;padding:20px 24px;border-radius:0 0 10px 10px;margin-bottom:20px;">
    <div style="display:flex;gap:12px;margin-bottom:20px;flex-wrap:wrap;">
      <span style="background:#1a7a4a;color:#fff;padding:5px 14px;border-radius:20px;font-size:13px;">
        ★★★★★ Excellent: {counts[5]}
      </span>
      <span style="background:#2e86de;color:#fff;padding:5px 14px;border-radius:20px;font-size:13px;">
        ★★★★ Good: {counts[4]}
      </span>
      <span style="background:#f39c12;color:#fff;padding:5px 14px;border-radius:20px;font-size:13px;">
        ★★★ Moderate: {counts[3]}
      </span>
    </div>
    {body}
    <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
    <p style="font-size:12px;color:#999;text-align:center;">
      Automated digest for Moussa Allamine · Development Economics PhD · UCA/LEO<br>
      Portals: World Bank · AfDB · UNDP · UNU-WIDER · FERDI · CERDI · AFD · ReliefWeb · Devex
    </p>
  </div>
</body>
</html>"""
    return html


def send_email(html, n_jobs, run_date):
    subject = f"[Job Digest] {n_jobs} new opportunities — {run_date}"
    if n_jobs == 0:
        subject = f"[Job Digest] No new matches today — {run_date}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_SENDER
    msg["To"] = GMAIL_TO
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

    # 1. Collect all listings
    all_jobs = []
    for scraper_fn in SCRAPERS:
        name = scraper_fn.__name__.replace("scrape_", "").upper()
        print(f"  Scraping {name}...")
        try:
            jobs = scraper_fn()
            print(f"    → {len(jobs)} listings found")
            all_jobs.extend(jobs)
        except Exception as e:
            print(f"    ⚠️  Error: {e}")

    print(f"\n📦 Total raw listings: {len(all_jobs)}")

    # 2. Filter out already-seen listings
    seen = load_seen()
    new_jobs = filter_new(all_jobs, seen)
    print(f"🆕 New listings (not seen before): {len(new_jobs)}")

    if not new_jobs:
        print("No new listings. Sending empty digest.")
        html = build_html_digest([], run_date)
        send_email(html, 0, run_date)
        return

    # 3. Score with Claude
    print(f"\n🤖 Scoring {len(new_jobs)} listings with Claude...")
    relevant = score_jobs(new_jobs)
    print(f"✅ {len(relevant)} listings scored {MIN_SCORE}+ and included in digest")

    # 4. Mark all new jobs as seen (even unscored ones, to avoid re-processing)
    for j in new_jobs:
        seen[j["id"]] = {"title": j["title"], "seen_on": str(datetime.date.today())}
    save_seen(seen)

    # 5. Build and send digest
    html = build_html_digest(relevant, run_date)
    send_email(html, len(relevant), run_date)
    print("\n🎉 Done.")


if __name__ == "__main__":
    main()
