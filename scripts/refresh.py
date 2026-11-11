"""Build docs/leaderboard.json from VoterVoice.

Pulls advocacy responses, rolls each advocate up to their company, and writes company-level
totals only. No names, emails, zip codes or message text are written to disk or printed:
logs show counts only, because this runs in a public repository.

Usage: python scripts/refresh.py
"""
import collections
import datetime
import hashlib
import json
import re
import sys

import vv

CONFIG = json.loads((vv.ROOT / "config.json").read_text(encoding="utf-8"))
OUT = vv.ROOT / "docs" / "leaderboard.json"
PAGE = 500
SUFFIXES = re.compile(r"[\s,]+(inc|incorporated|llc|l\.l\.c|corp|corporation|co|company|ltd|lp|llp|plc)\.?$", re.I)


def fetch_responses(assoc, since):
    """All advocacy responses since a date, paging defensively (stops when a page adds nothing new)."""
    seen, rows, offset = set(), [], 0
    for _ in range(200):
        status, data = vv.get("/advocacy/responses", {"association": assoc, "limit": PAGE, "offset": offset}, raw_query=f"date=>{since}")
        if status not in (200, 206):
            raise SystemExit(f"advocacy/responses failed: HTTP {status} {str(data)[:200]}")
        page = data if isinstance(data, list) else (data or {}).get("items") or []
        fresh = [r for r in page if r.get("id") not in seen]
        if not fresh:
            break
        for r in fresh:
            seen.add(r.get("id"))
        rows.extend(fresh)
        if len(page) < PAGE:
            break
        offset += len(page)
    return rows


def find_key(obj, key):
    """First non-empty value for a key anywhere in a nested JSON structure."""
    if isinstance(obj, dict):
        if obj.get(key):
            return obj[key]
        for v in obj.values():
            hit = find_key(v, key)
            if hit:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = find_key(v, key)
            if hit:
                return hit
    return None


def company_key(name):
    name = re.sub(r"\s+", " ", (name or "").strip())
    prev = None
    while prev != name:
        prev, name = name, SUFFIXES.sub("", name)
    return name.casefold()


CACHE_FILE = vv.ROOT / ".cache" / "companies.json"
CACHE_HOURS = 24


class Companies:
    """Looks up each advocate's company, remembering the answer between runs.

    The memory file maps an opaque fingerprint of the advocate (a salted hash, not their id,
    name or email) to the company text on their contact record. It lives in .cache/, which is
    git-ignored and kept in GitHub's workflow cache, never in the repository. Entries expire
    after CACHE_HOURS so a company corrected in VoterVoice comes through within a day.
    """

    def __init__(self, assoc):
        self.assoc, self.cache, self.lookups, self.misses, self.remembered = assoc, {}, 0, 0, 0
        self.aliases = {company_key(k): v for k, v in CONFIG.get("companyAliases", {}).items()}
        self.display = collections.defaultdict(collections.Counter)
        self.salt = hashlib.sha256(vv.load_key().encode()).hexdigest()
        self.now = datetime.datetime.now(datetime.timezone.utc)
        try:
            self.store = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.store = {}

    def fingerprint(self, uid):
        return hashlib.sha256(f"{self.salt}:{uid}".encode()).hexdigest()[:20]

    def remembered_raw(self, uid):
        entry = self.store.get(self.fingerprint(uid))
        if not entry:
            return False, None
        try:
            age = self.now - datetime.datetime.fromisoformat(entry["at"])
        except (KeyError, ValueError):
            return False, None
        return (age < datetime.timedelta(hours=CACHE_HOURS)), entry.get("company")

    def save(self):
        cutoff = self.now - datetime.timedelta(days=30)
        keep = {k: v for k, v in self.store.items() if datetime.datetime.fromisoformat(v["at"]) > cutoff}
        CACHE_FILE.parent.mkdir(exist_ok=True)
        CACHE_FILE.write_text(json.dumps(keep), encoding="utf-8")

    def lookup(self, user):
        raw = None
        for params in ({"contactId": user.get("contactId")}, {"email": user.get("email")}):
            if not next(iter(params.values())):
                continue
            self.lookups += 1
            status, data = vv.get("/contacts", {"association": self.assoc, **params})
            if status not in (200, 204, 404):
                # Stop rather than publish: treating a failed lookup as "no company" would move
                # that advocate's letters to the unaffiliated bucket for an hour.
                raise SystemExit(f"contact lookup failed after retries: HTTP {status}")
            if status == 200:
                raw = find_key(data, "businessCompanyName")
                if raw:
                    break
        return raw

    def of(self, user):
        uid = user.get("id") or user.get("contactId") or user.get("email")
        if uid in self.cache:
            return self.cache[uid]
        fresh, raw = self.remembered_raw(uid)
        if fresh:
            self.remembered += 1
        else:
            raw = self.lookup(user)
            self.store[self.fingerprint(uid)] = {"company": raw, "at": self.now.isoformat()}
        if not raw:
            self.misses += 1
            result = None
        else:
            key = company_key(raw)
            if key in self.aliases:
                result = self.aliases[key]
            else:
                self.display[key][re.sub(r"\s+", " ", raw.strip())] += 1
                result = ("key", key)
        self.cache[uid] = result
        return result

    def name(self, ref):
        if isinstance(ref, tuple):
            return self.display[ref[1]].most_common(1)[0][0]
        return ref


def chamber(delivery):
    office = ((delivery.get("recipient") or {}).get("office") or {})
    body = (office.get("electedBody") or "").lower()
    if "senate" in body:
        return "senate"
    if "house" in body or "representatives" in body:
        return "house"
    return None


def main():
    assoc = CONFIG["association"]
    camp = CONFIG["campaign"]
    if assoc.startswith("REPLACE"):
        raise SystemExit("Set \"association\" in config.json to your VoterVoice ID Name.")

    campaign_id = camp.get("id")
    if not campaign_id:
        status, summaries = vv.get("/advocacy/campaignsummaries", {"association": assoc})
        if status != 200:
            raise SystemExit(f"campaignsummaries failed: HTTP {status} {str(summaries)[:200]}")
        matches = [c for c in summaries or [] if camp["nameContains"].lower() in (c.get("name") or "").lower()]
        if len(matches) != 1:
            raise SystemExit(f"Expected one campaign matching {camp['nameContains']!r}, found {len(matches)}. Set campaign.id in config.json.")
        campaign_id = matches[0]["id"]

    since = min(camp["since"], CONFIG["badgesSince"])
    rows = fetch_responses(assoc, since)
    print(f"responses fetched: {len(rows)}")

    companies = Companies(assoc)
    stats = collections.defaultdict(lambda: {"letters": 0, "advocates": set(), "campaigns": set(), "chambers": set()})
    activity = []
    by_deliveries = CONFIG.get("countLettersBy") == "deliveries"
    excluded = {company_key(n) for n in CONFIG.get("excludeCompanies", [])}

    for r in rows:
        user = r.get("user") or {}
        ref = companies.of(user)
        if ref is None:
            if not CONFIG.get("includeUnaffiliated"):
                continue
            ref = CONFIG["unaffiliatedLabel"]
        s = stats[ref]
        s["campaigns"].add(r.get("campaignId"))
        if r.get("campaignId") != campaign_id or (r.get("dateCreated") or "") < camp["since"]:
            continue
        deliveries = [d for m in r.get("messages") or [] for d in m.get("deliveries") or []]
        s["letters"] += len(deliveries) if by_deliveries else 1
        s["advocates"].add(user.get("id") or user.get("contactId") or user.get("email"))
        chambers = {c for c in map(chamber, deliveries) if c}
        s["chambers"] |= chambers
        target = "both chambers of Congress" if len(chambers) == 2 else "the U.S. Senate" if "senate" in chambers else "the U.S. House" if "house" in chambers else "their elected officials"
        activity.append({"ref": ref, "target": target, "at": r.get("dateCreated")})

    out_companies = []
    for ref, s in stats.items():
        name = companies.name(ref)
        if company_key(name) in excluded or not s["advocates"]:
            continue
        out_companies.append({
            "name": name,
            "letters": s["letters"],
            "advocates": len(s["advocates"]),
            "campaigns": len(s["campaigns"]),
            "bothChambers": len(s["chambers"]) == 2,
        })
    out_companies.sort(key=lambda c: (-c["letters"], c["name"]))

    activity.sort(key=lambda a: a["at"] or "", reverse=True)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {
        # "checked" moves on every successful run; "updated" only when the numbers change.
        "checked": now,
        "updated": now,
        "campaign": {"name": camp["displayName"], "goal": camp["goal"]},
        "scoring": CONFIG["scoring"],
        "finishScore": CONFIG["finishScore"],
        "companies": out_companies,
        # company is null for advocates with no company on file; the board says "An independent advocate"
        "activity": [{"company": None if a["ref"] == CONFIG["unaffiliatedLabel"] else companies.name(a["ref"]), "target": a["target"], "at": a["at"]} for a in activity[:6]],
    }
    try:
        previous = json.loads(OUT.read_text(encoding="utf-8"))
        same = all(previous.get(k) == out[k] for k in ("campaign", "scoring", "finishScore", "companies", "activity"))
        if same and previous.get("updated"):
            out["updated"] = previous["updated"]
    except (OSError, ValueError):
        pass
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    total = sum(c["letters"] for c in out_companies)
    companies.save()
    print(f"companies: {len(out_companies)}  letters: {total}  contact lookups: {companies.lookups}  remembered: {companies.remembered}  advocates without a company: {companies.misses}")


if __name__ == "__main__":
    sys.exit(main())
