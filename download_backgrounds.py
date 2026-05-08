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


def fill_background_library(target: int, per_page: int) -> None:
    saved = 0
    seen = existing_provider_ids()
    current_count = count_background_assets()
    print(f"current backgrounds: {current_count}")

    if current_count >= target:
        print(f"target already reached: {current_count}/{target}")
        return

    for query, page in query_cycle(DEFAULT_QUERIES):
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
            provider_id = str(candidate.get("provider_id") or "")
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
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
    parser.add_argument("--per-page", type=int, default=20, help="Pexels results per query page")
    args = parser.parse_args()
    fill_background_library(max(1, args.target), max(1, min(args.per_page, 20)))


if __name__ == "__main__":
    main()
