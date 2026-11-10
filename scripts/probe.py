"""Explore what the VoterVoice API returns, printing structure only.

Personal values (names, emails, zip codes, message text) are never printed: every value is shown
as its type, except a short allow-list of non-personal fields such as campaign ids and names.

Usage: python scripts/probe.py <association-id-name> [days-back]
"""
import datetime
import sys

import vv

SAFE = {"id", "campaignId", "name", "headline", "isActive", "isPrivate", "dateCreated", "type", "deliveryMethod",
        "electedBody", "state", "start", "end", "association", "targetsFederal", "targetsState", "priority"}


def shape(value, key=None, depth=0):
    pad = "  " * depth
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (dict, list)):
                print(f"{pad}{k}:")
                shape(v, k, depth + 1)
            else:
                shown = repr(v)[:70] if k in SAFE else type(v).__name__
                print(f"{pad}{k}: {shown}")
    elif isinstance(value, list):
        print(f"{pad}[list of {len(value)}]")
        if value:
            shape(value[0], key, depth + 1)
    else:
        print(f"{pad}{type(value).__name__}")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    assoc = sys.argv[1]
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    status, data = vv.get("/advocacy/campaignsummaries", {"association": assoc})
    print(f"== campaignsummaries: HTTP {status}")
    if status != 200:
        print("  ", str(data)[:300])
        return
    for c in data or []:
        tf = c.get("timeFrame") or {}
        print(f"   id={c.get('id')} active={c.get('isActive')} start={tf.get('start')} end={tf.get('end')} name={c.get('name')!r}")

    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    status, data = vv.get("/advocacy/responses", {"association": assoc}, raw_query=f"date=>{since}")
    print(f"== advocacy/responses since {since}: HTTP {status}")
    if status not in (200, 206):
        print("  ", str(data)[:300])
        return
    rows = data if isinstance(data, list) else (data or {}).get("items", data)
    print(f"   top-level type: {type(data).__name__}; rows: {len(rows) if isinstance(rows, list) else 'n/a'}")
    if isinstance(data, dict):
        print("   top-level keys:", list(data.keys()))
    if isinstance(rows, list) and rows:
        shape(rows[0], depth=2)
        by_campaign = {}
        for r in rows:
            by_campaign[r.get("campaignId")] = by_campaign.get(r.get("campaignId"), 0) + 1
        print("   responses per campaignId:", by_campaign)
        users = {(r.get("user") or {}).get("id") for r in rows}
        print("   distinct users:", len(users))


if __name__ == "__main__":
    main()
