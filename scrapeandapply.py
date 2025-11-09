"""
Dice Auto Apply Bot
-------------------
Automates job searches and Easy Apply submissions on Dice.com
using Playwright and BeautifulSoup.

Author: Krishna Yalamarthi
License: MIT
"""

import os
import requests
import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeoutError
import time
import random

# ------------ Search configuration ------------
BASE_URL = (
    "https://www.dice.com/jobs"
    "?filters.postedDate=TWO"
    "&filters.employmentType=CONTRACTS%7CTHIRD_PARTY"
    "&radius=30"
    "&countryCode=US"
    "&language=en"
    "&q=AI%2FML"
    "&radiusUnit=mi"
    "&page="
)
# 💡 Tip: Modify this BASE_URL to match your search preferences —
# e.g. change 'postedDate', 'radius', 'employmentType', 'countryCode', 'q' (keywords), or location filters 
# to target specific roles, timeframes, or regions.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    )
}

# ------------ Login & automation settings ------------
DICE_LOGIN_URL = "https://www.dice.com/dashboard/login"

# ⚠️ Replace these placeholders with your own credentials before running
USERNAME = "your_email@example.com"
PASSWORD = "your_secure_password"
LOCAL_RESUME = "path/to/your_resume.docx"

# Wait time (in seconds) between job applications to mimic human behavior
PER_JOB_WAIT_SECONDS = 3

# ------------ File to track already processed jobs ------------
SEEN_FILE = "seen_links.txt"


def load_seen_links(path: str = SEEN_FILE) -> set[str]:
    """Load previously applied job links from file to avoid duplicates."""
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def append_seen_link(link: str, path: str = SEEN_FILE) -> None:
    """Append a processed job link to the tracking file."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(link + "\n")


# ------------ Scraping logic ------------
def get_total_pages(html_text: str) -> int:
    """Extract total page count from the first result page."""
    soup = BeautifulSoup(html_text, "html.parser")
    sec = soup.find("section", {"aria-label": lambda lbl: lbl and "Page" in lbl})
    if not sec:
        return 1
    label = sec.get("aria-label", "")
    try:
        _, total = [int(n) for n in label.replace("Page", "").replace("of", "").split()]
    except ValueError:
        total = 1
    return total


def scrape_job_listings() -> list[dict]:
    """Scrape all job listings matching the search criteria."""
    jobs: list[dict] = []
    try:
        first_res = requests.get(BASE_URL + "1", headers=HEADERS, timeout=15)
        first_res.raise_for_status()
        first_page = first_res.text
    except Exception:
        first_page = ""

    total_pages = get_total_pages(first_page)
    print(f"Detected {total_pages} pages.")

    for p in range(1, total_pages + 1):
        url = BASE_URL + str(p)
        print(f"Scraping job list (page {p}/{total_pages})...")

        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                print(f"  ⚠️ Failed to load page {p} (status {resp.status_code})")
                continue
        except Exception as e:
            print(f"  ⚠️ Failed to load page {p}: {e}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        links = soup.find_all("a", {"data-testid": "job-search-job-detail-link"})
        for a in links:
            title = a.get_text(strip=True)
            href = a.get("href")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://www.dice.com" + href
            jobs.append({"Job Title": title, "Job Link": href})

        # polite pause between requests
        time.sleep(random.uniform(0.2, 0.5))

    # De-duplicate job links
    seen = set()
    deduped = []
    for j in jobs:
        if j["Job Link"] in seen:
            continue
        seen.add(j["Job Link"])
        deduped.append(j)
    print(f"Found {len(deduped)} unique jobs.")
    return deduped


# ------------ Playwright helpers ------------
def login(page: Page):
    """Automate login flow for Dice using Playwright."""
    page.goto(DICE_LOGIN_URL)
    page.fill('input[name="email"]', USERNAME)
    page.get_by_test_id("sign-in-button").click()

    # Wait for password field
    page.wait_for_selector('input[name="password"]', timeout=60_000)
    page.fill('input[name="password"]', PASSWORD)
    page.get_by_test_id("submit-password").click()

    # Wait until fully logged in
    page.wait_for_load_state("networkidle", timeout=120_000)
    print("Logged in successfully.")


def has_easy_apply(page: Page) -> bool:
    """Check whether a job listing supports 'Easy Apply'."""
    try:
        page.wait_for_selector("apply-button-wc", timeout=30_000)
    except PWTimeoutError:
        return False

    # Give web component time to load
    page.wait_for_timeout(1000)

    host = page.locator("apply-button-wc")
    easy = host.locator("button.btn-primary", has_text="Easy apply")
    if easy.count() > 0 and easy.first.is_visible():
        return True

    # fallback: any apply button inside the host
    fallback = host.locator("button.btn-primary")
    for i in range(min(3, fallback.count())):
        try:
            t = fallback.nth(i).inner_text(timeout=1000).lower()
            if "apply" in t:
                return True
        except Exception:
            pass
    return False


def easy_apply_on_job(page: Page, job_url: str) -> bool:
    """Open a job link and complete the Easy Apply process if available."""
    try:
        page.goto(job_url, wait_until="domcontentloaded")
        if not has_easy_apply(page):
            print("  Skipping (no Easy apply):", job_url)
            return False

        host = page.locator("apply-button-wc")
        easy_btn = host.locator("button.btn-primary", has_text="Easy apply")
        if easy_btn.count() == 0:
            easy_btn = host.locator("button.btn-primary").first

        print("  Clicking Easy apply on:", job_url)
        easy_btn.click()

        # Replace resume
        page.wait_for_selector('button.file-remove', timeout=10_000)
        page.click('button.file-remove:has-text("Replace")')
        page.wait_for_selector('input#fsp-fileUpload', timeout=10_000)
        page.set_input_files('input#fsp-fileUpload', LOCAL_RESUME)
        page.wait_for_timeout(1200)

        # Upload the file
        page.wait_for_selector('span[data-e2e="upload"]', timeout=10_000)
        page.click('span[data-e2e="upload"]')
        page.wait_for_timeout(1200)

        # Navigate through steps until submission
        for _ in range(6):
            submit_btn = page.locator('button.btn-next:has-text("Submit")')
            if submit_btn.is_visible():
                submit_btn.click()
                page.wait_for_timeout(1200)
                print("  Submitted ✔")
                return True
            next_btn = page.locator('button.btn-next')
            if next_btn.is_visible():
                next_btn.click()
                page.wait_for_timeout(1000)
            else:
                break

        print("  Could not reach Submit step; skipping.")
        return False

    except PWTimeoutError as te:
        print("  Timeout:", te)
        return False
    except Exception as e:
        print("  Error:", e)
        return False


# ------------ Main orchestrator ------------
def main():
    """Entry point for the automation."""
    jobs = scrape_job_listings()
    links = [j["Job Link"] for j in jobs]

    seen_links = load_seen_links()
    new_links = [lnk for lnk in links if lnk not in seen_links]
    print(f"{len(new_links)} new links to process; {len(seen_links)} already seen.")

    if not new_links:
        print("Nothing new. Exiting.")
        return

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        page = browser.new_page()

        login(page)

        submitted = 0
        for i, link in enumerate(new_links, start=1):
            print(f"\n[{i}/{len(new_links)}] {link}")
            applied = easy_apply_on_job(page, link)

            # Log this job link to prevent reapplying
            append_seen_link(link)
            seen_links.add(link)

            if applied:
                submitted += 1

            time.sleep(PER_JOB_WAIT_SECONDS)

        print(f"\nDone. Submitted: {submitted} / Attempted: {len(new_links)}")
        page.wait_for_timeout(2000)
        browser.close()


if __name__ == "__main__":
    main()
