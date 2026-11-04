#!/usr/bin/env python3
"""Builds conf2.json: the configs of the subscriptions in subs.txt whose server answers a TCP connection.

Every subscription is downloaded (plain links or base64), its links are read, the host and port of each link is
tried with a TCP connection (a "tcping"), and the links that answered are written to conf2.json, which the app
reads next to conf.json. Only the standard library is used.

Settings come from environment variables, all optional:
  SUBS_FILE      list of subscription links, one per line   (subs.txt)
  OUTPUT         the file that is written                    (conf2.json)
  TIMEOUT        seconds to wait for one connection          (4)
  ATTEMPTS       connections tried per server                (3)
  MIN_OK         how many of them must answer                (2)
  CONCURRENCY    servers tested at the same time             (200)
  MAX_PER_SUB    most links kept from one subscription       (100)
  INCLUDE_UDP    keep hysteria2/hy2/wireguard links, which a TCP test cannot check (false)
"""
import asyncio
import base64
import binascii
import json
import os
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SUBS_FILE = Path(os.environ.get("SUBS_FILE", "subs.txt"))
OUTPUT = Path(os.environ.get("OUTPUT", "conf2.json"))
TIMEOUT = float(os.environ.get("TIMEOUT", "4"))
ATTEMPTS = int(os.environ.get("ATTEMPTS", "3"))
MIN_OK = int(os.environ.get("MIN_OK", "2"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "200"))
MAX_PER_SUB = int(os.environ.get("MAX_PER_SUB", "100"))
INCLUDE_UDP = os.environ.get("INCLUDE_UDP", "false").lower() in ("1", "true", "yes")

TCP_SCHEMES = ("vless", "vmess", "trojan", "ss")
UDP_SCHEMES = ("hysteria2", "hy2", "wireguard")
LINK = re.compile(
    r"(?<![A-Za-z0-9+.-])(?:" + "|".join(TCP_SCHEMES + UDP_SCHEMES) + r")://[^\s\"'<>]+",
    re.IGNORECASE,
)


def log(message):
    print(message, flush=True)


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (conf2-builder)"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as error:  # network trouble: try again, then give up on this subscription
            last = error
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def b64decode(text):
    cleaned = re.sub(r"\s+", "", text).replace("-", "+").replace("_", "/")
    cleaned += "=" * (-len(cleaned) % 4)
    return base64.b64decode(cleaned).decode("utf-8", errors="replace")


def extract_links(content):
    """The links of a subscription's body: plain text first, base64 when the body holds no link."""
    text = content
    if "://" not in text:
        try:
            text = b64decode(text)
        except (binascii.Error, ValueError):
            return []
    links = []
    for match in LINK.finditer(text):
        link = match.group(0).rstrip(".;)")
        # The app cuts a link at a comma, a quote or a space; percent-encode them so nothing is lost.
        if not link.lower().startswith("vmess://"):
            link = link.replace(",", "%2C")
        links.append(link)
    return links


def endpoint(link):
    """(host, port) a link connects to, or None when it cannot be read."""
    scheme = link.split("://", 1)[0].lower()
    rest = link.split("://", 1)[1]
    try:
        if scheme == "vmess":
            payload = re.split(r"[?#]", rest, maxsplit=1)[0]
            data = json.loads(b64decode(payload))
            return str(data["add"]).strip(), int(data["port"])
        if scheme == "ss" and "@" not in rest.split("#", 1)[0]:
            decoded = b64decode(re.split(r"[?#]", rest, maxsplit=1)[0])
            parts = urllib.parse.urlsplit("//" + decoded.rsplit("@", 1)[1])
            return parts.hostname, parts.port
        parts = urllib.parse.urlsplit(link)
        if not parts.hostname or not parts.port:
            return None
        return parts.hostname, parts.port
    except Exception:
        return None


def key_of(link):
    """Two links that differ only in their name are one server."""
    return link.split("#", 1)[0]


async def probe(host, port):
    started = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), TIMEOUT)
    except Exception:
        return None
    elapsed = (time.perf_counter() - started) * 1000
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return elapsed


async def tcping(host, port, gate):
    async with gate:
        answers = []
        for _ in range(ATTEMPTS):
            ms = await probe(host, port)
            if ms is not None:
                answers.append(ms)
            await asyncio.sleep(0.2)
        return statistics.median(answers) if len(answers) >= MIN_OK else None


async def test_all(endpoints):
    gate = asyncio.Semaphore(CONCURRENCY)
    ordered = list(endpoints)
    results = await asyncio.gather(*(tcping(h, p, gate) for h, p in ordered))
    return dict(zip(ordered, results))


def read_subs():
    if not SUBS_FILE.exists():
        sys.exit(f"{SUBS_FILE} not found")
    urls = []
    for line in SUBS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def main():
    subs = read_subs()
    if not subs:
        sys.exit("subs.txt holds no subscription link")

    seen = set()
    per_sub = []  # (url, [(link, endpoint)])
    for url in subs:
        try:
            links = extract_links(fetch(url))
        except Exception as error:
            log(f"! download failed: {error}")
            per_sub.append((url, []))
            continue
        fresh = []
        for link in links:
            scheme = link.split("://", 1)[0].lower()
            if scheme in UDP_SCHEMES and not INCLUDE_UDP:
                continue
            key = key_of(link)
            if key in seen:
                continue
            ep = endpoint(link)
            if ep is None:
                continue
            seen.add(key)
            fresh.append((link, ep))
        log(f"{url}: {len(links)} links read, {len(fresh)} new")
        per_sub.append((url, fresh))

    # UDP links cannot be checked with a TCP connection: they are kept as they are when INCLUDE_UDP is on.
    tcp_endpoints = {
        ep for _, items in per_sub for link, ep in items if link.split("://", 1)[0].lower() in TCP_SCHEMES
    }
    log(f"testing {len(tcp_endpoints)} servers ({ATTEMPTS} tries each, {MIN_OK} must answer)...")
    started = time.time()
    results = asyncio.run(test_all(tcp_endpoints))
    log(f"tested in {time.time() - started:.0f}s, {sum(v is not None for v in results.values())} answered")

    kept = []
    for url, items in per_sub:
        alive = [
            link
            for link, ep in items
            if link.split("://", 1)[0].lower() in UDP_SCHEMES or results.get(ep) is not None
        ][:MAX_PER_SUB]
        log(f"{url}: {len(alive)} of {len(items)} kept")
        kept.extend(alive)

    if not kept:
        sys.exit("no server answered; conf2.json is left as it was")

    previous = []
    if OUTPUT.exists():
        try:
            previous = json.loads(OUTPUT.read_text(encoding="utf-8")).get("configs", [])
        except Exception:
            previous = []
    if previous == kept:
        log("nothing changed")
        return
    document = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(kept),
        "configs": kept,
    }
    # ensure_ascii off, so names in any language stay readable to the app (it undoes no \u escapes)
    OUTPUT.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(f"wrote {len(kept)} configs to {OUTPUT}")


if __name__ == "__main__":
    main()
