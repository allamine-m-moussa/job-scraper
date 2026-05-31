# 📋 Job Scraper + AI Digest System
**Moussa Allamine — Development Economics PhD**

Automatically scrapes 9 job portals daily, scores each listing against your profile using Claude AI, and emails you only the relevant ones.

---

## What it does

1. **Scrapes** World Bank, AfDB, UNDP, UNU-WIDER, FERDI, CERDI, AFD, ReliefWeb, Devex every day at 7 AM Paris time
2. **Deduplicates** — never shows you the same listing twice
3. **Scores** each new listing 1–5 using Claude AI against your exact profile
4. **Emails** you a clean digest with only the relevant listings (score ≥ 3)

---

## Setup — 4 steps, ~15 minutes total

### Step 1: Create a GitHub repository

1. Go to [github.com/new](https://github.com/new)
2. Name it: `job-scraper` (private repository)
3. Upload all files from this folder into the repo

### Step 2: Set up Gmail App Password

Gmail requires a special "App Password" (not your login password) for scripts to send email.

1. Go to your Google Account → **Security**
2. Enable **2-Step Verification** if not already on
3. Go to **Security → 2-Step Verification → App Passwords** (at the bottom)
4. Create a new App Password: name it "Job Scraper"
5. Copy the 16-character password shown — you'll only see it once

### Step 3: Add GitHub Secrets

In your GitHub repository:
1. Go to **Settings → Secrets and variables → Actions**
2. Click **New repository secret** for each of these:

| Secret name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key from [console.anthropic.com](https://console.anthropic.com) |
| `GMAIL_SENDER` | Your Gmail address (e.g. `mssallamine@gmail.com`) |
| `GMAIL_APP_PASSWORD` | The 16-character App Password from Step 2 |
| `GMAIL_TO` | Where to receive the digest (can be same Gmail) |

### Step 4: Test it manually

1. In your GitHub repo, go to **Actions → Daily Job Digest**
2. Click **Run workflow → Run workflow**
3. Watch it run (takes ~2-3 minutes)
4. Check your inbox

---

## Customization

### Add a new portal

In `scraper.py`, add a new function following this pattern:

```python
def scrape_myportal():
    jobs = []
    r = safe_get("https://myportal.org/jobs")
    if not r:
        return jobs
    soup = BeautifulSoup(r.text, "html.parser")
    for item in soup.select(".job-card"):
        title = item.find("h3").get_text(strip=True)
        url = item.find("a")["href"]
        desc = item.get_text(" ", strip=True)[:400]
        jobs.append({"title": title, "org": "My Portal", "url": url,
                     "deadline": "See link", "description": desc, "source": "MyPortal"})
    return jobs
```

Then add `scrape_myportal` to the `SCRAPERS` list at the bottom.

### Change the minimum score

Edit `MIN_SCORE = 3` in `scraper.py`. Set to `4` for fewer, higher-quality results.

### Change the schedule

Edit the cron line in `.github/workflows/daily_digest.yml`:
- `"0 6 * * *"` = every day at 6 AM UTC (7 AM Paris)
- `"0 6 * * 1"` = every Monday only
- `"0 6 * * 1,4"` = Monday and Thursday

---

## Cost estimate

- **GitHub Actions**: Free (2,000 minutes/month included, this uses ~3 min/day = ~90 min/month)
- **Anthropic API**: ~$0.01–0.05 per run depending on number of new listings
- **Total**: essentially free

---

## Portals covered

| Portal | Type |
|---|---|
| UNU-WIDER | Fellowships, vacancies |
| ReliefWeb | Development consulting, NGO jobs |
| Devex | International development |
| World Bank | STC consultancies, staff roles |
| AfDB | African Development Bank |
| UNDP | UN development jobs |
| FERDI | French development research |
| CERDI/UCA | Your own lab's postings |
| AFD | French development agency |

---

## Troubleshooting

**No email received?**
- Check GitHub Actions logs for errors
- Verify your Gmail App Password is correct (no spaces)
- Check spam folder

**"Authentication failed" error?**
- Make sure 2-Step Verification is enabled on your Google account
- Regenerate the App Password

**Scraper finds 0 jobs?**
- Some portals may have changed their HTML structure
- Check the GitHub Actions log for `⚠️` warnings
- The portal scraper may need updating — open an issue or update the selector

---

*Built for Moussa Allamine — PhD in Development Economics, UCA/LEO*
