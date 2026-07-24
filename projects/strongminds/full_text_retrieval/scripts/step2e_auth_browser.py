"""
Step 2e: Authenticated browser fetch through Oxford's subscriptions.

For paywalled papers (Wiley, Taylor & Francis, Springer, SAGE, LWW, OUP, Karger,
IOS, Liebert, ...) that Oxford subscribes to. It uses a PERSISTENT Chrome profile
so you log in to Oxford SSO (OpenAthens/Shibboleth + Duo) ONCE; the session then
lets the browser download subscribed PDFs.

Two modes:

  1. Log in once (opens a Chrome window; complete SSO, then press Enter here):
         python step2e_auth_browser.py --login

  2. Fetch the still-missing papers using that saved session:
         python step2e_auth_browser.py inventory_{timestamp}.csv
         python step2e_auth_browser.py inventory_{timestamp}.csv --via-solo   # route via SOLO OpenURL

The login profile is stored in .ox_profile/ (git-ignored). Downloads are matched
to references by {zotero_key} in the filename, as with the other steps.
"""

import re
import sys
import time
from datetime import datetime

import pandas as pd
from playwright.sync_api import sync_playwright

import config
from step2_fetch_missing_pdfs import is_pdf_bytes, target_filename

PROFILE_DIR = str(config.ROOT / ".ox_profile")
SOLO_OPENURL = "https://solo.bodleian.ox.ac.uk/openurl/44OXF_INST/44OXF_INST:SOLO"
LOGIN_START = "https://solo.bodleian.ox.ac.uk/discovery/login?vid=44OXF_INST:SOLO"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
CHECKPOINT_EVERY = 5


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}")
    sys.stdout.flush()


def citation_pdf_url(html: str) -> str | None:
    for pat in (
        r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url',
    ):
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1)
    return None


def grab_pdf(page, pdf_url: str, out_path) -> bool:
    # 1) download event
    try:
        with page.expect_download(timeout=30000) as dl:
            try:
                page.goto(pdf_url, timeout=30000)
            except Exception:
                pass
        d = dl.value
        d.save_as(str(out_path))
        with open(out_path, "rb") as f:
            if f.read(5) == b"%PDF-":
                return True
    except Exception:
        pass
    # 2) inline navigation response
    try:
        resp = page.goto(pdf_url, wait_until="commit", timeout=30000)
        body = resp.body()
        if is_pdf_bytes(body):
            with open(out_path, "wb") as f:
                f.write(body)
            return True
    except Exception:
        pass
    return False


def find_libkey_link(page) -> str | None:
    """On a SOLO record page, find the LibKey 'Download PDF' / full-text link."""
    try:
        hrefs = page.eval_on_selector_all(
            "a", "els=>els.map(e=>({t:(e.innerText||'').trim().toLowerCase(), h:e.href}))"
        )
    except Exception:
        return None
    for a in hrefs:
        if "full-text-file" in (a.get("h") or ""):
            return a["h"]
    for a in hrefs:
        if "content-location" in (a.get("h") or ""):
            return a["h"]
    for a in hrefs:
        if a.get("t") in ("download pdf", "read article"):
            return a["h"]
    return None


def clear_sso(page) -> None:
    """If a Microsoft/ADFS SSO page appears, click through the 'Stay signed in?'
    prompt so the SAML handoff completes."""
    for _ in range(8):
        url = page.url
        if not any(s in url for s in ("login.microsoftonline.com", "/adfs/", "okta", "/saml", "/resume")):
            return
        for sel in ("input#idSIButton9", "#idSIButton9", "input[type=submit]"):
            try:
                if page.locator(sel).count():
                    page.click(sel, timeout=3000)
                    break
            except Exception:
                pass
        try:
            page.wait_for_load_state("networkidle", timeout=6000)
        except Exception:
            page.wait_for_timeout(2500)


def derive_pdf_urls(final_url: str, doi: str, html: str) -> list[str]:
    urls = []
    base = "/".join(final_url.split("/")[:3]) if "//" in final_url else ""
    if "wiley.com" in final_url:
        urls += [f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}?download=true",
                 f"https://onlinelibrary.wiley.com/doi/pdf/{doi}?download=true"]
    elif "sagepub.com" in final_url:
        urls.append(f"{base}/doi/pdf/{doi}?download=true")
    elif "tandfonline.com" in final_url:
        urls.append(f"{base}/doi/pdf/{doi}?download=true")
    elif "springer.com" in final_url:
        urls.append(f"https://link.springer.com/content/pdf/{doi}.pdf")
    # generic: citation_pdf_url from the landing page, plus Atypon pattern
    meta = citation_pdf_url(html)
    if meta:
        if meta.startswith("/"):
            meta = base + meta
        urls.append(meta)
    m = re.search(r"/doi/(?:abs|full|epdf)?/?(10\.\d+/[^\s?#\"']+)", final_url)
    if m and base:
        urls.append(f"{base}/doi/pdfdirect/{m.group(1)}?download=true")
    return urls


def resolve_and_grab(page, doi: str, out_path, via_solo: bool = True) -> str | None:
    # 1) SOLO record -> LibKey full-text link
    try:
        page.goto(f"{SOLO_OPENURL}?url_ver=Z39.88-2004&rft_id=info:doi/{doi}",
                  wait_until="domcontentloaded", timeout=60000)
    except Exception:
        return None
    page.wait_for_timeout(7000)
    link = find_libkey_link(page)
    if not link:
        return None

    # 2) follow LibKey (brokers Oxford access), clearing any 'stay signed in' SSO prompt
    try:
        page.goto(link, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass
    page.wait_for_timeout(3000)
    clear_sso(page)
    page.wait_for_timeout(2000)

    final = page.url
    try:
        html = page.content()
    except Exception:
        html = ""
    host = final.split("/")[2].replace("www.", "") if "//" in final else "browser"

    # 3) if LibKey already served the PDF inline, save it; else derive publisher PDF
    if final.lower().split("?")[0].endswith(".pdf"):
        try:
            resp = page.goto(final, wait_until="commit", timeout=30000)
            if is_pdf_bytes(resp.body()):
                with open(out_path, "wb") as f:
                    f.write(resp.body())
                return f"oxford:{host}"
        except Exception:
            pass
    for url in derive_pdf_urls(final, doi, html):
        if grab_pdf(page, url, out_path):
            return f"oxford:{host}"
    return None


def do_login(p) -> None:
    ctx = p.chromium.launch_persistent_context(
        PROFILE_DIR, headless=False, channel="chrome", user_agent=UA,
        viewport={"width": 1280, "height": 900}, accept_downloads=True,
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(LOGIN_START, wait_until="domcontentloaded", timeout=60000)
    print("\n" + "=" * 66)
    print("A Chrome window is open. Log in to Oxford SSO (Single Sign-On + Duo).")
    print("When you can see you are signed in to SOLO, come back here and press")
    print("Enter to save the session.")
    print("=" * 66)
    try:
        input(">>> Press Enter once you are logged in... ")
    except EOFError:
        page.wait_for_timeout(120000)  # no stdin: wait 2 min
    ctx.close()
    log("Session saved to .ox_profile/. You can now run the fetch mode.")


def do_fetch(p, csv_path, via_solo: bool, limit: int | None = None) -> None:
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    for col in ("pdf_path", "pdf_source"):
        if col not in df.columns:
            df[col] = ""
    has_pdf = df["has_pdf"].str.lower().isin(["true", "1", "yes"])
    todo = df[(~has_pdf) & (df["pdf_path"].str.strip() == "") & (df["doi"].str.strip() != "")].copy()
    if limit:
        todo = todo.head(limit)
    log(f"Paywalled candidates to try via Oxford session: {len(todo)}")
    if len(todo) == 0:
        return

    ctx = p.chromium.launch_persistent_context(
        PROFILE_DIR, headless=False, channel="chrome", user_agent=UA,
        viewport={"width": 1280, "height": 900}, accept_downloads=True,
    )
    found = 0
    for n, (idx, row) in enumerate(todo.iterrows(), start=1):
        doi = row["doi"].strip()
        out_path = config.PDF_DIR / target_filename(row["zotero_key"], doi, row["title"])
        page = ctx.new_page()
        try:
            source = resolve_and_grab(page, doi, out_path, via_solo)
        finally:
            try:
                page.close()
            except Exception:
                pass
        if source:
            df.at[idx, "pdf_path"] = str(out_path.relative_to(config.ROOT))
            df.at[idx, "pdf_source"] = source
            found += 1
            log(f"[{n}/{len(todo)}] {row['zotero_key']}: OK via {source}")
        else:
            log(f"[{n}/{len(todo)}] {row['zotero_key']}: not found ({doi})")
        if n % CHECKPOINT_EVERY == 0:
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            log(f"  ...checkpoint saved ({found} found so far)")
    ctx.close()
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    log("-" * 60)
    log(f"Authenticated PDFs fetched: {found}/{len(todo)}")
    log(f"Updated inventory:          {csv_path}")


def main() -> None:
    args = sys.argv[1:]
    via_solo = "--via-solo" in args
    args = [a for a in args if a != "--via-solo"]
    limit = None
    if "--limit" in args:
        i = args.index("--limit")
        limit = int(args[i + 1])
        args = args[:i] + args[i + 2:]

    with sync_playwright() as p:
        if "--login" in args:
            do_login(p)
            return
        if not args:
            raise SystemExit("Usage: python step2e_auth_browser.py --login | <inventory_csv> [--limit N]")
        csv_arg = args[0]
        csv_path = config.ROOT / csv_arg
        if not csv_path.exists():
            csv_path = config.LOG_DIR / csv_arg
        if not csv_path.exists():
            raise SystemExit(f"Inventory CSV not found: {csv_arg}")
        do_fetch(p, csv_path, via_solo, limit)


if __name__ == "__main__":
    main()
