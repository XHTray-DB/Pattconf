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
  MAX_PER_COUNTRY  most links kept from one country; the countries are mixed in the file (10)
  INCLUDE_UDP    keep hysteria2/hy2/wireguard links, which a TCP test cannot check (false)
  GEO_API_URL    batch address-to-country service            (http://ip-api.com/batch?fields=status,countryCode,query)
  V2GET_API_URL, V2GET_ADMIN_SECRET   the panel; servers most accounts could not connect to are left out (skipped when unset)
  GEO_DB_URL     fallback table of address ranges, .csv or .csv.gz (db-ip.com's free country file of this month)

Every run starts from the servers of the previous conf2.json: they are tested again, the ones that still answer are
kept, the dead ones are dropped, and new servers from the subscriptions take their place until TARGET_COUNT is reached.

  TARGET_COUNT   how many links conf2.json should hold (200)

Each kept link is renamed to the flag and the Persian name of the country its server is in, whatever name it had.
"""
import asyncio
import base64
import binascii
import bisect
import gzip
import hashlib
import ipaddress
import json
import os
import re
import socket
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

SUBS_FILE = Path(os.environ.get("SUBS_FILE", "subs.txt"))
OUTPUT = Path(os.environ.get("OUTPUT", "conf2.json"))
TIMEOUT = float(os.environ.get("TIMEOUT", "4"))
ATTEMPTS = int(os.environ.get("ATTEMPTS", "3"))
MIN_OK = int(os.environ.get("MIN_OK", "2"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "200"))
MAX_PER_COUNTRY = int(os.environ.get("MAX_PER_COUNTRY", "15"))
TARGET_COUNT = int(os.environ.get("TARGET_COUNT", "200"))
INCLUDE_UDP = os.environ.get("INCLUDE_UDP", "false").lower() in ("1", "true", "yes")
GEO_API_URL = os.environ.get("GEO_API_URL", "http://ip-api.com/batch?fields=status,countryCode,query")
GEO_DB_URL = os.environ.get("GEO_DB_URL", "")
UNKNOWN_NAME = "\U0001F3F3\ufe0f نامشخص"

COUNTRY_NAMES = dict(
    pair.split(":", 1)
    for pair in """AD:آندورا AE:امارات AF:افغانستان AG:آنتیگوا_و_باربودا AI:آنگویلا AL:آلبانی AM:ارمنستان AO:آنگولا AR:آرژانتین
AS:ساموآی_آمریکا AT:اتریش AU:استرالیا AW:آروبا AX:جزایر_آلند AZ:جمهوری_آذربایجان BA:بوسنی_و_هرزگوین BB:باربادوس
BD:بنگلادش BE:بلژیک BF:بورکینافاسو BG:بلغارستان BH:بحرین BI:بوروندی BJ:بنین BL:سنت_بارتلمی BM:برمودا BN:برونئی
BO:بولیوی BQ:کارائیب_هلند BR:برزیل BS:باهاما BT:بوتان BW:بوتسوانا BY:بلاروس BZ:بلیز CA:کانادا CC:جزایر_کوکوس
CD:کنگو_کینشاسا CF:آفریقای_مرکزی CG:کنگو_برازاویل CH:سوئیس CI:ساحل_عاج CK:جزایر_کوک CL:شیلی CM:کامرون CN:چین
CO:کلمبیا CR:کاستاریکا CU:کوبا CV:کیپ_ورد CW:کوراسائو CX:جزیره_کریسمس CY:قبرس CZ:چک DE:آلمان DJ:جیبوتی DK:دانمارک
DM:دومینیکا DO:جمهوری_دومینیکن DZ:الجزایر EC:اکوادور EE:استونی EG:مصر EH:صحرای_غربی ER:اریتره ES:اسپانیا
ET:اتیوپی FI:فنلاند FJ:فیجی FK:جزایر_فالکلند FM:میکرونزی FO:جزایر_فارو FR:فرانسه GA:گابن GB:بریتانیا GD:گرنادا
GE:گرجستان GF:گویان_فرانسه GG:گرنزی GH:غنا GI:جبل_الطارق GL:گرینلند GM:گامبیا GN:گینه GP:گوادلوپ GQ:گینه_استوایی
GR:یونان GT:گواتمالا GU:گوام GW:گینه_بیسائو GY:گویان HK:هنگ_کنگ HN:هندوراس HR:کرواسی HT:هائیتی HU:مجارستان
ID:اندونزی IE:ایرلند IL:اسرائیل IM:جزیره_من IN:هند IO:قلمرو_اقیانوس_هند_بریتانیا IQ:عراق IR:ایران IS:ایسلند
IT:ایتالیا JE:جرسی JM:جامائیکا JO:اردن JP:ژاپن KE:کنیا KG:قرقیزستان KH:کامبوج KI:کیریباتی KM:کومور
KN:سنت_کیتس_و_نویس KP:کره_شمالی KR:کره_جنوبی KW:کویت KY:جزایر_کیمن KZ:قزاقستان LA:لائوس LB:لبنان LC:سنت_لوسیا
LI:لیختن_اشتاین LK:سری_لانکا LR:لیبریا LS:لسوتو LT:لیتوانی LU:لوکزامبورگ LV:لتونی LY:لیبی MA:مراکش MC:موناکو
MD:مولداوی ME:مونته_نگرو MF:سنت_مارتین MG:ماداگاسکار MH:جزایر_مارشال MK:مقدونیه_شمالی ML:مالی MM:میانمار
MN:مغولستان MO:ماکائو MP:ماریانای_شمالی MQ:مارتینیک MR:موریتانی MS:مونتسرات MT:مالت MU:موریس MV:مالدیو MW:مالاوی
MX:مکزیک MY:مالزی MZ:موزامبیک NA:نامیبیا NC:کالدونیای_جدید NE:نیجر NF:جزیره_نورفولک NG:نیجریه NI:نیکاراگوئه
NL:هلند NO:نروژ NP:نپال NR:نائورو NU:نیوئه NZ:نیوزیلند OM:عمان PA:پاناما PE:پرو PF:پلی_نزی_فرانسه PG:پاپوآ_گینه_نو
PH:فیلیپین PK:پاکستان PL:لهستان PM:سن_پیر_و_میکلن PR:پورتوریکو PS:فلسطین PT:پرتغال PW:پالائو PY:پاراگوئه
QA:قطر RE:رئونیون RO:رومانی RS:صربستان RU:روسیه RW:رواندا SA:عربستان_سعودی SB:جزایر_سلیمان SC:سیشل SD:سودان
SE:سوئد SG:سنگاپور SH:سنت_هلنا SI:اسلوونی SJ:سوالبارد SK:اسلواکی SL:سیرالئون SM:سن_مارینو SN:سنگال SO:سومالی
SR:سورینام SS:سودان_جنوبی ST:سائوتومه_و_پرنسیپ SV:السالوادور SX:سنت_مارتن SY:سوریه SZ:اسواتینی
TC:تورکس_و_کایکوس TD:چاد TG:توگو TH:تایلند TJ:تاجیکستان TK:توکلائو TL:تیمور_شرقی TM:ترکمنستان TN:تونس TO:تونگا
TR:ترکیه TT:ترینیداد_و_توباگو TV:تووالو TW:تایوان TZ:تانزانیا UA:اوکراین UG:اوگاندا US:آمریکا UY:اروگوئه
UZ:ازبکستان VA:واتیکان VC:سنت_وینسنت VE:ونزوئلا VG:ویرجین_بریتانیا VI:ویرجین_آمریکا VN:ویتنام VU:وانواتو
WF:والیس_و_فوتونا WS:ساموآ XK:کوزوو YE:یمن YT:مایوت ZA:آفریقای_جنوبی ZM:زامبیا ZW:زیمبابوه""".split()
)

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
    """Two links that differ only in their name are one server (a vmess link keeps its name inside its body)."""
    if link.lower().startswith("vmess://"):
        try:
            payload = re.split(r"[?#]", link[len("vmess://"):], maxsplit=1)[0]
            data = json.loads(b64decode(payload))
            data.pop("ps", None)
            return "vmess://" + json.dumps(data, sort_keys=True, separators=(",", ":"))
        except Exception:
            pass
    return link.split("#", 1)[0]


async def probe(address, port):
    started = time.perf_counter()
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(address, port), TIMEOUT)
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
    """(median milliseconds, the address that was tested) when the server answers, else None."""
    async with gate:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except Exception:
            return None
        # One address is tested and remembered, so the country below is that of the server that was tested.
        address = sorted(infos, key=lambda info: info[0] != socket.AF_INET)[0][4][0]
        answers = []
        for _ in range(ATTEMPTS):
            ms = await probe(address, port)
            if ms is not None:
                answers.append(ms)
            await asyncio.sleep(0.2)
        return (statistics.median(answers), address) if len(answers) >= MIN_OK else None


async def test_all(endpoints):
    gate = asyncio.Semaphore(CONCURRENCY)
    ordered = list(endpoints)
    results = await asyncio.gather(*(tcping(h, p, gate) for h, p in ordered))
    return dict(zip(ordered, results))


def http_get_bytes(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (conf2-builder)"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def geo_batch(addresses):
    """Country code by address from a batch service (100 addresses a call, 15 calls a minute)."""
    found = {}
    addresses = sorted(addresses)
    for start in range(0, len(addresses), 100):
        chunk = addresses[start:start + 100]
        data = []
        for _ in range(3):
            try:
                request = urllib.request.Request(
                    GEO_API_URL,
                    data=json.dumps(chunk).encode(),
                    headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (conf2-builder)"},
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = json.loads(response.read().decode())
                    if response.headers.get("X-Rl") == "0":
                        time.sleep(int(response.headers.get("X-Ttl") or 5) + 1)
                break
            except urllib.error.HTTPError as error:
                if error.code == 429:
                    time.sleep(int(error.headers.get("X-Ttl") or 60) + 1)
                    continue
                break
            except Exception:
                time.sleep(3)
        for item in data:
            if item.get("status") == "success" and item.get("countryCode"):
                found[item["query"]] = item["countryCode"].upper()
        if start + 100 < len(addresses):
            time.sleep(4.5)
    return found


def geo_table():
    """The fallback table: sorted address ranges with their country, for both address families."""
    if GEO_DB_URL:
        urls = [GEO_DB_URL]
    else:
        now = datetime.now(timezone.utc)
        last = (now.replace(day=1) - timedelta(days=1))
        urls = [f"https://download.db-ip.com/free/dbip-country-lite-{d.year}-{d.month:02d}.csv.gz" for d in (now, last)]
    for url in urls:
        try:
            raw = http_get_bytes(url)
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            tables = {4: ([], [], []), 6: ([], [], [])}
            for line in raw.decode("utf-8", errors="replace").splitlines():
                parts = line.strip().split(",")
                if len(parts) < 3:
                    continue
                first = ipaddress.ip_address(parts[0].strip('"'))
                last_ip = ipaddress.ip_address(parts[1].strip('"'))
                starts, ends, codes = tables[first.version]
                starts.append(int(first))
                ends.append(int(last_ip))
                codes.append(parts[2].strip('"').upper())
            return tables
        except Exception as error:
            log(f"! address table not read from {url}: {error}")
    return None


def country_codes(addresses):
    """Country code by address: the batch service first, the address table for what it did not answer."""
    found = geo_batch(addresses) if addresses else {}
    missing = [a for a in addresses if a not in found]
    if missing:
        table = geo_table()
        for address in missing:
            try:
                value = ipaddress.ip_address(address)
            except ValueError:
                continue
            if table is None:
                break
            starts, ends, codes = table[value.version]
            index = bisect.bisect_right(starts, int(value)) - 1
            if index >= 0 and int(value) <= ends[index] and len(codes[index]) == 2 and codes[index].isalpha():
                found[address] = codes[index]
    return found


def address_of(host):
    """An address of a host that was not tested (the UDP links), or None."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return sorted(infos, key=lambda info: info[0] != socket.AF_INET)[0][4][0]
    except Exception:
        return None


def flag_of(code):
    return "".join(chr(0x1F1E6 + ord(c) - 65) for c in code)


def title_of(code):
    if not code:
        return UNKNOWN_NAME
    return f"{flag_of(code)} {COUNTRY_NAMES.get(code, code).replace('_', ' ')}"


def rename(link, title):
    """The link with its name replaced by [title], whatever name it had."""
    if link.lower().startswith("vmess://"):
        try:
            payload = re.split(r"[?#]", link[len("vmess://"):], maxsplit=1)[0]
            data = json.loads(b64decode(payload))
            data["ps"] = title
            body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            return "vmess://" + base64.b64encode(body).decode()
        except Exception:
            pass
    return link.split("#", 1)[0] + "#" + urllib.parse.quote(title, safe="")


def server_key(link):
    """The key the app and the panel know a server by: the first 12 hex digits of the SHA-256 of its link."""
    return hashlib.sha256(link.encode("utf-8")).hexdigest()[:12]


def bad_keys():
    """Keys of the servers that most accounts could not connect to, from the panel; empty when it cannot be asked."""
    api = os.environ.get("V2GET_API_URL", "").strip().rstrip("/")
    secret = os.environ.get("V2GET_ADMIN_SECRET", "").strip()
    if not api or not secret:
        return set()
    try:
        request = urllib.request.Request(
            api + "/api/v1/servers/bad",
            headers={"X-Admin-Secret": secret, "User-Agent": "Mozilla/5.0 (conf2-builder)"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return set(json.loads(response.read().decode()).get("bad", []))
    except Exception as error:
        log(f"! bad-server list not read: {error}")
        return set()


def select_and_mix(keepers, fresh, target):
    """Up to [target] candidates, (link, address, country, ms): the kept ones first, then the new ones, the fastest
    first, with at most MAX_PER_COUNTRY of a country; when that leaves the list short, the cap is lifted for the
    rest. The result is mixed: one of each country in turn, the countries with the most servers first."""
    groups = {}
    taken = set()
    total = 0

    def add(item, capped):
        nonlocal total
        link, _, country, _ = item
        if total >= target or link in taken:
            return
        group = groups.setdefault(country or "", [])
        if capped and len(group) >= MAX_PER_COUNTRY:
            return
        group.append(item)
        taken.add(link)
        total += 1

    ordered_keepers = sorted(keepers, key=lambda item: item[3])
    ordered_fresh = sorted(fresh, key=lambda item: item[3])
    for item in ordered_keepers + ordered_fresh:
        add(item, capped=True)
    if total < target:
        log(f"only {total} of {target} within {MAX_PER_COUNTRY} per country; lifting the cap for the rest")
        for item in ordered_keepers + ordered_fresh:
            add(item, capped=False)
    order = sorted(groups, key=lambda code: (-len(groups[code]), code))
    log("per country: " + ", ".join(f"{code or '??'}={len(groups[code])}" for code in order))
    mixed = []
    for turn in range(max((len(g) for g in groups.values()), default=0)):
        for code in order:
            if turn < len(groups[code]):
                mixed.append(groups[code][turn])
    return mixed


def read_subs():
    if not SUBS_FILE.exists():
        sys.exit(f"{SUBS_FILE} not found")
    urls = []
    for line in SUBS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def load_previous():
    """The links of the previous OUTPUT, or an empty list."""
    if not OUTPUT.exists():
        return []
    try:
        return json.loads(OUTPUT.read_text(encoding="utf-8")).get("configs", [])
    except Exception:
        return []


def main():
    subs = read_subs()
    if not subs:
        sys.exit("subs.txt holds no subscription link")

    # The servers of the last run are tested again first: the ones that still answer stay.
    previous = load_previous()
    seen = set()
    prev_items = []  # (link, endpoint)
    for link in previous:
        key = key_of(link)
        scheme = link.split("://", 1)[0].lower()
        ep = endpoint(link)
        if key in seen or ep is None or (scheme in UDP_SCHEMES and not INCLUDE_UDP):
            continue
        seen.add(key)
        prev_items.append((link, ep))
    log(f"previous run: {len(previous)} links, {len(prev_items)} to test again")

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
    def is_udp(link):
        return link.split("://", 1)[0].lower() in UDP_SCHEMES

    tcp_endpoints = {ep for link, ep in prev_items if not is_udp(link)}
    tcp_endpoints |= {ep for _, items in per_sub for link, ep in items if not is_udp(link)}
    log(f"testing {len(tcp_endpoints)} servers ({ATTEMPTS} tries each, {MIN_OK} must answer)...")
    started = time.time()
    results = asyncio.run(test_all(tcp_endpoints))
    log(f"tested in {time.time() - started:.0f}s, {sum(v is not None for v in results.values())} answered")

    def alive_of(items):
        out = []  # (link, address, ms)
        for link, ep in items:
            result = results.get(ep)
            if result is not None:
                out.append((link, result[1], result[0]))
            elif is_udp(link):
                out.append((link, address_of(ep[0]), 0.0))
        return out

    kept_old = alive_of(prev_items)
    log(f"previous servers: {len(kept_old)} of {len(prev_items)} still answer, {len(prev_items) - len(kept_old)} dropped")
    new_alive = []
    for url, items in per_sub:
        alive = alive_of(items)
        log(f"{url}: {len(alive)} of {len(items)} answered")
        new_alive.extend(alive)

    if not kept_old and not new_alive:
        sys.exit("no server answered; conf2.json is left as it was")

    addresses = {address for _, address, _ in kept_old + new_alive if address}
    countries = country_codes(sorted(addresses))
    log(f"country found for {len(countries)} of {len(addresses)} addresses")

    bad = bad_keys()
    skipped = 0

    def candidates(alive):
        nonlocal skipped
        out = []
        for link, address, ms in alive:
            country = countries.get(address)
            renamed = rename(link, title_of(country))
            if bad and server_key(renamed) in bad:
                skipped += 1
                continue
            out.append((renamed, address, country, ms))
        return out

    keepers = candidates(kept_old)
    fresh = candidates(new_alive)
    if skipped:
        log(f"{skipped} servers left out: most accounts could not connect to them")

    chosen = select_and_mix(keepers, fresh, TARGET_COUNT)
    kept = [link for link, _, _, _ in chosen]
    if not kept:
        sys.exit("every server was left out; conf2.json is left as it was")
    kept_keys = {key_of(link) for link in kept}
    stayed = sum(1 for link in previous if key_of(link) in kept_keys)
    added = len(kept) - stayed
    log(f"{len(kept)} configs: {stayed} kept from the last run, {added} new, {len(previous) - stayed} of the last run gone")

    document = {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(kept),
        "kept": stayed,
        "added": added,
        "configs": kept,
    }
    # ensure_ascii off, so names in any language stay readable to the app (it undoes no \\u escapes)
    OUTPUT.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(f"wrote {len(kept)} configs to {OUTPUT}")


if __name__ == "__main__":
    main()
