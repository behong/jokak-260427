from __future__ import annotations

import html
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser

from db import DB_PATH, get_app_setting, save_message


SOURCE_NAME = "사람로"
BASE_URL = "https://saramro.com/quotes"
USER_AGENT = "glbanjang-video-bot/1.0 (+local automation)"


@dataclass(frozen=True)
class SaramroQuote:
    quote_id: int
    text: str
    author: str
    title: str
    page_url: str


class TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)


def setting_bool(name: str, default: str = "0") -> bool:
    value = get_app_setting(name, default).strip().lower()
    return value not in {"0", "false", "no", "off", ""}


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def fetch_page(page: int = 1) -> str:
    url = BASE_URL if page <= 1 else f"{BASE_URL}?page={page}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=15) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def html_to_lines(markup: str) -> list[str]:
    parser = TextHTMLParser()
    parser.feed(markup)
    return [clean_text(part) for part in parser.parts if clean_text(part)]


def parse_quotes(markup: str, page: int = 1) -> list[SaramroQuote]:
    lines = html_to_lines(markup)
    quotes: list[SaramroQuote] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.isdigit():
            index += 1
            continue

        quote_id = int(line)
        title = ""
        quote_text = ""
        author = ""
        cursor = index + 1

        if cursor < len(lines):
            title = clean_text(lines[cursor])
            cursor += 1
        if cursor < len(lines) and lines[cursor] == "명언":
            cursor += 1
        if cursor < len(lines):
            quote_text = clean_text(lines[cursor])
            cursor += 1
        if cursor < len(lines) and lines[cursor].startswith("-"):
            author = clean_text(lines[cursor].strip("- "))
            cursor += 1

        if quote_text and author and len(quote_text) <= 260:
            quotes.append(
                SaramroQuote(
                    quote_id=quote_id,
                    text=quote_text,
                    author=author,
                    title=title,
                    page_url=BASE_URL if page <= 1 else f"{BASE_URL}?page={page}",
                )
            )
        index = max(cursor, index + 1)
    return quotes


def quote_content(quote: SaramroQuote) -> str:
    return "\n".join(
        [
            quote.text,
            f"- {quote.author} -",
            "출처: 사람로",
        ]
    )


def import_saramro_quotes(limit: int = 10, max_pages: int = 5) -> int:
    if not setting_bool("SARAMRO_QUOTES_ENABLED", "0"):
        return 0

    imported = 0
    for page in range(1, max(1, max_pages) + 1):
        if imported >= limit:
            break
        try:
            quotes = parse_quotes(fetch_page(page), page)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logging.warning("Failed to fetch Saramro quotes page=%s: %s", page, exc)
            break

        for quote in quotes:
            if imported >= limit:
                break
            result = save_message(
                DB_PATH,
                SOURCE_NAME,
                quote.quote_id,
                quote_content(quote),
                None,
            )
            if result == "inserted":
                imported += 1
    return imported
