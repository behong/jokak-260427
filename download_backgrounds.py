from __future__ import annotations

import argparse
import itertools
import random
import sys
from typing import Iterable

from backgrounds import (
    BackgroundAssetError,
    count_background_assets,
    list_background_assets,
    save_background_asset,
    search_pexels_videos,
)


DEFAULT_QUERIES = [
    "calm forest vertical",
    "quiet library vertical",
    "rain window vertical",
    "misty mountain vertical",
    "sunset ocean vertical",
    "night city vertical",
    "cozy cafe vertical",
    "autumn leaves vertical",
    "snow forest vertical",
    "green nature vertical",
    "clouds sky vertical",
    "river water vertical",
    "book pages vertical",
    "candle light vertical",
    "old street vertical",
    "garden flowers vertical",
]

PRESET_QUERIES = {
    "default": DEFAULT_QUERIES,
    "sleep": [
        "night rain window vertical slow",
        "candle light close up vertical calm",
        "moonlight forest vertical peaceful",
        "cozy bedroom night vertical",
        "slow clouds night sky vertical",
        "fireplace calm vertical",
        "dark ocean waves vertical slow",
        "stars night sky vertical calm",
        "warm lamp bedroom vertical",
        "rain on glass vertical relaxing",
    ],
    "morning": [
        "morning sunlight window vertical",
        "tea steam morning vertical calm",
        "forest morning light vertical",
        "calm lake sunrise vertical",
        "leaves wind slow vertical",
        "soft clouds sunrise vertical",
        "zen garden morning vertical",
        "coffee steam morning vertical",
        "sunrise ocean waves vertical calm",
        "green nature morning vertical peaceful",
    ],
    "q3": [
        "calm summer sunset ocean vertical",
        "green forest sunlight vertical slow",
        "summer rain window vertical relaxing",
        "quiet lake reflection vertical calm",
        "garden flowers breeze vertical slow",
        "countryside sunset vertical peaceful",
        "blue sky clouds vertical slow calm",
        "lotus pond vertical peaceful",
        "moonlight ocean vertical calm",
        "warm sunset field vertical slow",
        "early autumn leaves vertical peaceful",
        "tea by window rain vertical relaxing",
    ],
}
PRESET_QUERIES["healing"] = PRESET_QUERIES["sleep"] + PRESET_QUERIES["morning"]


def existing_provider_ids() -> set[str]:
    return {
        str(asset["provider_id"])
        for asset in list_background_assets(limit=500)
        if asset.get("provider") == "pexels"
    }


def query_cycle(queries: Iterable[str]) -> Iterable[tuple[str, int]]:
    query_list = [query.strip() for query in queries if query.strip()]
    random.shuffle(query_list)
    for page in itertools.count(1):
        for query in query_list:
            yield query, page


def fill_background_library(
    target: int,
    per_page: int,
    queries: Iterable[str],
    collection: str | None = None,
    min_duration: float = 0.0,
) -> None:
    saved = 0
    seen = existing_provider_ids()
    current_count = count_background_assets()
    print(f"current backgrounds: {current_count}")

    if current_count >= target:
        print(f"target already reached: {current_count}/{target}")
        return

    for query, page in query_cycle(queries):
        if count_background_assets() >= target:
            break

        try:
            candidates = search_pexels_videos(query, per_page=per_page, page=page)
        except BackgroundAssetError as exc:
            print(f"search failed query={query!r} page={page}: {exc}")
            continue

        if not candidates and page > 4:
            continue

        for candidate in candidates:
            if float(candidate.get("duration") or 0) < min_duration:
                continue
            provider_id = str(candidate.get("provider_id") or "")
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            if collection:
                candidate["collection"] = collection
            try:
                asset = save_background_asset(candidate)
            except BackgroundAssetError as exc:
                print(f"download failed provider_id={provider_id}: {exc}")
                continue

            saved += 1
            total = count_background_assets()
            print(f"saved {total}/{target}: #{asset.get('id')} {asset.get('author')} ({asset.get('query')})")
            if total >= target:
                break

    print(f"done. added={saved}, total={count_background_assets()}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Fill the Pexels background video library.")
    parser.add_argument("--target", type=int, default=500, help="total background assets to keep")
    parser.add_argument("--add", type=int, default=0, help="add this many assets beyond the current total")
    parser.add_argument("--per-page", type=int, default=20, help="Pexels results per query page")
    parser.add_argument(
        "--preset",
        choices=sorted(PRESET_QUERIES),
        default="default",
        help="query preset to use",
    )
    parser.add_argument(
        "--query",
        action="append",
        default=[],
        help="extra Pexels query; can be passed more than once",
    )
    parser.add_argument("--collection", default="", help="collection name saved with downloaded assets")
    parser.add_argument("--min-duration", type=float, default=None, help="minimum source clip duration in seconds")
    args = parser.parse_args()
    target = max(1, args.target)
    if args.add > 0:
        target = count_background_assets() + args.add
    queries = [*PRESET_QUERIES[args.preset], *args.query]
    collection = args.collection.strip() or (args.preset if args.preset != "default" else "")
    min_duration = args.min_duration
    if min_duration is None:
        min_duration = 8.0 if args.preset != "default" else 0.0
    fill_background_library(
        target,
        max(1, min(args.per_page, 20)),
        queries,
        collection or None,
        max(0.0, min_duration),
    )


if __name__ == "__main__":
    main()
