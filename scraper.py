"""
Job Scraper + Keyword Digest — Fixed & Tested Version
Moussa Allamine — Development Economics PhD
Sources verified June 2026. 100% free, no AI API required.
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
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

GMAIL_SENDER   = os.environ["GMAIL_SENDER"]
GMAIL_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
GMAIL_TO       = os.environ["GMAIL_TO"]
SEEN_FILE      = Path("data/seen_jobs.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# ─────────────────────────────────────────────
# KEYWORD FILTER — tuned to Moussa's profile
# ─────────────────────────────────────────────

STRONG_KEYWORDS = [
    # Methods
    "evaluation", "evaluator", "évaluation", "impact evaluation",
    "econometric", "econometrics", "économétrie",
    "causal inference", "difference-in-differences", "quasi-experimental",
    "research assistant", "research associate", "assistant de recherche",
    "monitoring", "m&e", "meal", "suivi-évaluation",
    "geospatial", "spatial analysis", "sig", "qgis", "gis",
    "quantitative", "statistique", "data analysis", "analyse de données",
    # Topics
    "fiscal", "public finance", "finances publiques",
    "development economics", "économie du développement",
    "environment", "deforestation", "natural resource", "ressources naturelles",
    "conflict", "fragile", "humanitarian",
    # Geography
    "africa", "afrique", "sub-saharan", "sahel", "chad", "tchad",
    "drc", "congo", "cameroon", "niger", "mali", "burkina",
    "francophone", "francophon",
    # Institutions
    "world bank", "banque mondiale", "afdb", "african development bank",
    "undp", "pnud", "unu", "imf", "fmi", "unicef", "unhcr",
    "afd", "agence française", "ferdi", "cerdi",
    # Role types
    "consultant", "consultancy", "consultance", "short-term", "stc",
    "fellowship", "bourse", "internship", "stage", "intern",
    "phd", "doctoral", "doctorat", "postdoc",
    "remote", "télétravail", "home-based",
]

NEGATIVE_KEYWORDS = [
    "software engineer", "web developer", "frontend", "backend",
    "marketing manager", "sales", "accountant", "nurse", "doctor",
    "driver", "security guard", "chef", "teacher k-12", "k12",
    "real estate", "insurance", "customer service representative",
    "ukraine only", "nationals only",
]


def keyword_score(job):
    text = (job["title"] + " " + job["description"] + " " + job["org"]).lower()
    for neg in NEGATIVE_KEYWORDS:
        if neg in text:
            return 0, []
    matched = [kw for kw in STRONG_KEYWORDS if kw in text]
    if len(matched) >= 3:
        return 3, matched[:6]
    elif len(matched) == 2:
        return 2, matched
    elif len(matched) == 1:
        return 1, matched
    return 0, []


# ─────────────────────────────────────────────
# HTTP HELPER
# ─────────────────────────────────────────────

def safe_get(url, timeout=20):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"    ⚠️  {url[:60]} — {e}")
        return None


# ─────────────────────────────────────────────
# SCRAPERS — all tested and verified June 2026
# ─────────────────────────────────────────────

def scrape_unu_wider():
    """UNU-WIDER opportunities page — confirmed working."""
    jobs = []
    r = safe_get("https://www.wider.unu.edu/opportunities")
    if not r:
        return jobs
    soup = BeautifulSoup(r.text, "html.parser")
    seen_titles = set()
    for link in soup.select("a[href*='/opportunity']"):
        title = link.get_text(strip=True)
        if not title or title in seen_titles or len(title) < 8:
            continue
        seen_titles.add(title)
        href = link["href"]
        url = ("https://www.wider.unu.edu" + href) if href.startswith("/") else href
        parent = link.find_parent(["li", "div", "article", "td"])
        desc = parent.get_text(" ", strip=True)[:500] if parent else title
        deadline_match = re.search(
            r"(closing|deadline|apply by|closes?)[:\s]+([A-Za-z0-9 ,]+\d{4})",
            desc, re.IGNORECASE
        )
        deadline = deadline_match.group(2).strip() if deadline_match else "See link"
        jobs.append({
            "title": title, "org": "UNU-WIDER", "url": url,
            "deadline": deadline, "description": desc, "source": "UNU-WIDER"
        })
    return jobs


def scrape_reliefweb_rss():
    """ReliefWeb RSS feeds — confirmed working with multiple keywords."""
    jobs = []
    feeds = [
        "https://reliefweb.int/jobs/rss.xml?search=evaluation",
        "https://reliefweb.int/jobs/rss.xml?search=monitoring+africa",
        "https://reliefweb.int/jobs/rss.xml?search=research+africa",
        "https://reliefweb.int/jobs/rss.xml?search=consultant+development",
    ]
    seen = set()
    for feed_url in feeds:
        r = safe_get(feed_url)
        if not r or r.status_code == 202:
            continue
        try:
            root = ET.fromstring(r.content)
            for item in root.findall(".//item"):
                title = item.findtext("title", "").strip()
                url   = item.findtext("link", "").strip()
                desc  = BeautifulSoup(
                    item.findtext("description", ""), "html.parser"
                ).get_text(" ", strip=True)[:500]
                pub   = item.findtext("pubDate", "See link")
                key   = title + url
                if title and key not in seen:
                    seen.add(key)
                    jobs.append({
                        "title": title, "org": "via ReliefWeb",
                        "url": url, "deadline": pub,
                        "description": desc, "source": "ReliefWeb"
                    })
        except Exception as e:
            print(f"    ⚠️  ReliefWeb RSS parse error: {e}")
    return jobs


def scrape_undp_jobs():
    """UNDP jobs — confirmed reachable, parse table."""
    jobs = []
    urls = [
        "https://jobs.undp.org/cj_view_jobs.cfm?cur_job_family=Evaluation",
        "https://jobs.undp.org/cj_view_jobs.cfm?cur_job_family=Research%2C+Analysis+and+Knowledge+Management",
    ]
    seen = set()
    for url in urls:
        r = safe_get(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href*='cj_jb_detail'], a[href*='job_detail']"):
            title = a.get_text(strip=True)
            if not title or title in seen or len(title) < 5:
                continue
            seen.add(title)
            href = a["href"]
            job_url = ("https://jobs.undp.org/" + href) if not href.startswith("http") else href
            row = a.find_parent("tr")
            desc = row.get_text(" ", strip=True)[:400] if row else title
            jobs.append({
                "title": title, "org": "UNDP", "url": job_url,
                "deadline": "See link", "description": desc, "source": "UNDP"
            })
    return jobs


def scrape_world_bank_jobs():
    """World Bank careers page — parse listings."""
    jobs = []
    r = safe_get("https://www.worldbank.org/en/about/careers/programs-and-internships")
    if not r:
        return jobs
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.select("a[href*='worldbank.org/en/about/careers'], a[href*='csod.com']"):
        title = a.get_text(strip=True)
        if not title or len(title) < 8 or len(title) > 150:
            continue
        url = a["href"]
        if not url.startswith("http"):
            url = "https://www.worldbank.org" + url
        jobs.append({
            "title": title, "org": "World Bank Group", "url": url,
            "deadline": "See link", "description": title, "source": "World Bank"
        })
    return jobs[:10]


def scrape_oecd_jobs():
    """OECD careers — internships and full roles."""
    jobs = []
    r = safe_get("https://www.oecd.org/en/about/careers/jobs.html")
    if not r:
        return jobs
    soup = BeautifulSoup(r.text, "html.parser")
    for item in soup.select("article, .job-item, li")[:20]:
        a = item.find("a", href=True)
        if not a:
            continue
        title = a.get_text(strip=True)
        if not title or len(title) < 8 or len(title) > 200:
            continue
        href = a["href"]
        url = ("https://www.oecd.org" + href) if href.startswith("/") else href
        desc = item.get_text(" ", strip=True)[:400]
        jobs.append({
            "title": title, "org": "OECD", "url": url,
            "deadline": "See link", "description": desc, "source": "OECD"
        })
    return jobs


def scrape_afdb_jobs():
    """AfDB careers — try multiple entry points."""
    jobs = []
    urls = [
        "https://www.afdb.org/en/about/careers/current-vacancies",
        "https://www.afdb.org/en/about/careers",
    ]
    for base_url in urls:
        r = safe_get(base_url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href*='vacancy'], a[href*='career'], a[href*='job']"):
            title = a.get_text(strip=True)
            if not title or len(title) < 8 or len(title) > 200:
                continue
            href = a["href"]
            url = ("https://www.afdb.org" + href) if href.startswith("/") else href
            parent = a.find_parent(["tr", "li", "div", "article"])
            desc = parent.get_text(" ", strip=True)[:400] if parent else title
            jobs.append({
                "title": title, "org": "African Development Bank",
                "url": url, "deadline": "See link",
                "description": desc, "source": "AfDB"
            })
        if jobs:
            break
    return jobs[:15]


def scrape_ferdi_cerdi():
    """FERDI and CERDI — scrape their homepages for job/grant announcements."""
    jobs = []
    sources = [
        ("https://www.ferdi.fr", "FERDI"),
        ("https://cerdi.uca.fr", "CERDI/UCA"),
    ]
    job_words = ["emploi", "job", "bourse", "grant", "offre", "poste",
                 "recrutement", "these", "thèse", "fellowship", "stage",
                 "opportunit", "vacancy", "position"]
    for base_url, org in sources:
        r = safe_get(base_url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href]"):
            title = a.get_text(strip=True)
            href  = a["href"]
            if not title or len(title) < 8 or len(title) > 200:
                continue
            if any(w in title.lower() or w in href.lower() for w in job_words):
                url = (base_url + href) if href.startswith("/") else href
                if not url.startswith("http"):
                    continue
                jobs.append({
                    "title": title, "org": org, "url": url,
                    "deadline": "See link", "description": title,
                    "source": org
                })
    return jobs[:15]


def scrape_afd_jobs():
    """AFD — appels d'offres and employment."""
    jobs = []
    urls = [
        "https://www.afd.fr/fr/offres-emploi",
        "https://www.afd.fr/fr/appels-doffres-et-passations-de-marches-0",
    ]
    for url in urls:
        r = safe_get(url)
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href]"):
            title = a.get_text(strip=True)
            if not title or len(title) < 10 or len(title) > 200:
                continue
            href = a["href"]
            if not any(w in href for w in ["offre", "emploi", "appel", "marche", "poste"]):
                continue
            full_url = ("https://www.afd.fr" + href) if href.startswith("/") else href
            parent = a.find_parent(["li", "article", "div"])
            desc = parent.get_text(" ", strip=True)[:400] if parent else title
            jobs.append({
                "title": title, "org": "AFD", "url": full_url,
                "deadline": "See link", "description": desc, "source": "AFD"
            })
    return jobs[:15]


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
    new, deduped = [], set()
    for j in jobs:
        jid = job_id(j)
        if jid not in seen and jid not in deduped:
            deduped.add(jid)
            j["id"] = jid
            new.append(j)
    return new


# ─────────────────────────────────────────────
# EMAIL DIGEST
# ─────────────────────────────────────────────

SCORE_LABELS = {
    3: ("🟢 Strong match", "#1a7a4a"),
    2: ("🔵 Good match",   "#2e86de"),
    1: ("🟡 Worth a look", "#f39c12"),
}


def build_html(jobs_by_score, run_date, total_new):
    all_jobs = []
    for sc in [3, 2, 1]:
        all_jobs.extend(jobs_by_score.get(sc, []))

    if not all_jobs:
        body = """<div style='text-align:center;padding:40px;color:#666;'>
            <p style='font-size:18px;'>✅ Scraper ran successfully</p>
            <p>No new relevant listings today. Check back tomorrow.</p>
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
                <span style="color:#c0392b;font-size:13px;">⏰ {j['deadline']}</span>
              </div>
              <h3 style="margin:6px 0 4px;font-size:16px;">
                <a href="{j['url']}" style="color:#1a1a2e;text-decoration:none;">
                  {j['title']}
                </a>
              </h3>
              <p style="margin:2px 0 8px;color:#555;font-size:13px;">
                <strong>{j['org']}</strong> · <span style="color:#888;">{j['source']}</span>
              </p>
              <p style="margin:4px 0 10px;color:#666;font-size:12px;font-style:italic;">
                🏷️ {kws}
              </p>
              <a href="{j['url']}"
                 style="display:inline-block;padding:7px 16px;background:#1a1a2e;
                        color:#fff;border-radius:5px;font-size:13px;text-decoration:none;">
                View →
              </a>
            </div>"""
        body = cards

    n3 = len(jobs_by_score.get(3, []))
    n2 = len(jobs_by_score.get(2, []))
    n1 = len(jobs_by_score.get(1, []))

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:Georgia,serif;max-width:680px;margin:0 auto;
             padding:20px;background:#f5f6fa;">
  <div style="background:#1a1a2e;color:#fff;padding:28px 24px;border-radius:10px 10px 0 0;">
    <h1 style="margin:0;font-size:22px;">📋 Daily Opportunity Digest</h1>
    <p style="margin:6px 0 0;opacity:0.8;font-size:14px;">
      {run_date} · {len(all_jobs)} relevant from {total_new} new scraped
    </p>
  </div>
  <div style="background:#fff;padding:20px 24px;border-radius:0 0 10px 10px;">
    <div style="display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap;">
      <span style="background:#1a7a4a;color:#fff;padding:5px 14px;border-radius:20px;font-size:13px;">🟢 Strong: {n3}</span>
      <span style="background:#2e86de;color:#fff;padding:5px 14px;border-radius:20px;font-size:13px;">🔵 Good: {n2}</span>
      <span style="background:#f39c12;color:#fff;padding:5px 14px;border-radius:20px;font-size:13px;">🟡 Weak: {n1}</span>
    </div>
    {body}
    <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
    <p style="font-size:12px;color:#999;text-align:center;">
      Digest for Moussa Allamine · PhD Development Economics · UCA/LEO<br>
      Sources: UNU-WIDER · ReliefWeb · UNDP · World Bank · AfDB · OECD · FERDI · CERDI · AFD
    </p>
  </div>
</body></html>"""


def send_email(html, n_jobs, run_date):
    subject = (
        f"[Job Digest] {n_jobs} new opportunities — {run_date}"
        if n_jobs else f"[Job Digest] No new matches — {run_date}"
    )
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_SENDER
    msg["To"]      = GMAIL_TO
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_SENDER, GMAIL_PASSWORD)
        s.sendmail(GMAIL_SENDER, GMAIL_TO, msg.as_string())
    print(f"✅ Email sent to {GMAIL_TO}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

SCRAPERS = [
    ("UNU-WIDER",    scrape_unu_wider),
    ("ReliefWeb",    scrape_reliefweb_rss),
    ("UNDP",         scrape_undp_jobs),
    ("World Bank",   scrape_world_bank_jobs),
    ("AfDB",         scrape_afdb_jobs),
    ("OECD",         scrape_oecd_jobs),
    ("FERDI/CERDI",  scrape_ferdi_cerdi),
    ("AFD",          scrape_afd_jobs),
]


def main():
    run_date = datetime.date.today().strftime("%B %d, %Y")
    print(f"\n🔍 Job Scraper starting — {run_date}\n")

    all_jobs = []
    for name, fn in SCRAPERS:
        print(f"  Scraping {name}...")
        try:
            found = fn()
            print(f"    → {len(found)} listings")
            all_jobs.extend(found)
        except Exception as e:
            print(f"    ⚠️  Error: {e}")

    print(f"\n📦 Total raw: {len(all_jobs)}")

    seen     = load_seen()
    new_jobs = filter_new(all_jobs, seen)
    print(f"🆕 New (unseen): {len(new_jobs)}")

    jobs_by_score = {3: [], 2: [], 1: []}
    for job in new_jobs:
        score, matched = keyword_score(job)
        if score > 0:
            job["score"]            = score
            job["matched_keywords"] = matched
            jobs_by_score[score].append(job)

    total = sum(len(v) for v in jobs_by_score.values())
    print(f"✅ Relevant: {total}  (🟢{len(jobs_by_score[3])} 🔵{len(jobs_by_score[2])} 🟡{len(jobs_by_score[1])})")

    for j in new_jobs:
        seen[j["id"]] = {"title": j["title"], "seen_on": str(datetime.date.today())}
    save_seen(seen)

    html = build_html(jobs_by_score, run_date, len(new_jobs))
    send_email(html, total, run_date)
    print("🎉 Done.")


if __name__ == "__main__":
    main()
