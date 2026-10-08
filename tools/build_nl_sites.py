"""Build a free database of Dutch/Belgian websites from Common Crawl open data.

Stage 1 (domains):  python tools/build_nl_sites.py domains <vertices.txt.gz>
    Reads a Common Crawl hyperlink-graph *domain vertices* file and stores
    every .nl / .be domain in sites.db.
    Download one release from https://commoncrawl.org/web-graphs, e.g.
    .../projects/hyperlinkgraph/<release>/domain/<release>-domain-vertices.txt.gz

Stage 2 (enrich):   python tools/build_nl_sites.py enrich [--limit N] [--workers 50]
    Fetches each homepage (respecting robots.txt) and records the CMS and
    affiliate networks found in the HTML.

Only needs the standard library.
"""
import argparse
import gzip
import re
import sqlite3
import urllib.request
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor

DB = "sites.db"
TLDS = ("nl", "be")
UA = "CopySparkBot/0.1 (+contact: pcjansen09@gmail.com)"

CMS = {
    "wordpress": r"wp-content|wp-includes",
    "shopify": r"cdn\.shopify\.com",
    "woocommerce": r"woocommerce",
    "magento": r"mage/|magento",
    "lightspeed": r"seoshop|lightspeed",
    "webflow": r"webflow",
    "wix": r"wixstatic\.com",
    "joomla": r"joomla",
}
AFFILIATE = {
    "tradetracker": r"tradetracker",
    "daisycon": r"daisycon|ds1\.nl",
    "awin": r"awin1\.com|zenaps\.com",
    "partnerize": r"prf\.hn|partnerize",
    "bol": r"partnerprogramma\.bol\.com|bol\.com/nl/.*partner",
    "amazon": r"amzn\.to|tag=[\w-]+-21",
    "tradedoubler": r"tradedoubler",
    "cj": r"anrdoezrs|dpbolvw|jdoqocy",
}


def detect(html: str) -> tuple[str, str]:
    low = html.lower()
    cms = ",".join(k for k, p in CMS.items() if re.search(p, low))
    aff = ",".join(k for k, p in AFFILIATE.items() if re.search(p, low))
    return cms, aff


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS sites(
        domain TEXT PRIMARY KEY, tld TEXT, cms TEXT, affiliate TEXT,
        status INTEGER, checked INTEGER DEFAULT 0)""")
    return con


def load_domains(path: str) -> None:
    con = connect()
    n = 0
    with gzip.open(path, "rt", encoding="utf-8", errors="ignore") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            rev = parts[-1]  # reversed notation: nl.example
            labels = rev.split(".")
            if labels[0] not in TLDS or len(labels) < 2:
                continue
            domain = ".".join(reversed(labels))
            con.execute("INSERT OR IGNORE INTO sites(domain,tld) VALUES(?,?)",
                        (domain, labels[0]))
            n += 1
            if n % 100000 == 0:
                con.commit()
                print(n, "domains")
    con.commit()
    print("done:", n)


def fetch(domain: str):
    base = f"https://{domain}"
    try:
        rp = urllib.robotparser.RobotFileParser(base + "/robots.txt")
        rp.read()
        if not rp.can_fetch(UA, base + "/"):
            return domain, None, "", "", 0
        req = urllib.request.Request(base + "/", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read(500_000).decode("utf-8", "ignore")
            cms, aff = detect(html)
            return domain, r.status, cms, aff, 1
    except Exception:
        return domain, None, "", "", 1


def enrich(limit: int, workers: int) -> None:
    con = connect()
    rows = [r[0] for r in con.execute(
        "SELECT domain FROM sites WHERE checked=0 LIMIT ?", (limit,))]
    with ThreadPoolExecutor(workers) as ex:
        for i, (d, st, cms, aff, chk) in enumerate(ex.map(fetch, rows), 1):
            con.execute("UPDATE sites SET status=?,cms=?,affiliate=?,checked=1 "
                        "WHERE domain=?", (st, cms, aff, d))
            if i % 500 == 0:
                con.commit()
                print(i, "checked")
    con.commit()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("domains").add_argument("path")
    e = sub.add_parser("enrich")
    e.add_argument("--limit", type=int, default=10000)
    e.add_argument("--workers", type=int, default=50)
    a = ap.parse_args()
    if a.cmd == "domains":
        load_domains(a.path)
    else:
        enrich(a.limit, a.workers)
