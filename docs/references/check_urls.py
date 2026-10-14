"""URL 목록의 응답 코드와 페이지 제목을 확인한다.  python check_urls.py urls.txt"""
import asyncio
import html
import re
import sys

import httpx

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
      "Accept-Language": "ko,en;q=0.8"}
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


async def check(client, sem, url):
    async with sem:
        try:
            r = await client.get(url, follow_redirects=True)
            is_html = "html" in r.headers.get("content-type", "")
            m = TITLE.search(r.text[:200000]) if is_html else None
            title = (html.unescape(re.sub(r"\s+", " ", m.group(1)).strip())[:110] if m
                     else r.headers.get("content-type", "")[:40])
            return url, r.status_code, title
        except Exception as e:
            return url, 0, type(e).__name__


async def main():
    with open(sys.argv[1], encoding="utf-8") as f:
        urls = [u.strip() for u in f if u.strip() and not u.startswith("#")]
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(headers=UA, timeout=25, verify=False) as client:
        for url, code, title in await asyncio.gather(*(check(client, sem, u) for u in urls)):
            print(f"{code}\t{url}\n\t{title}")


sys.stdout.reconfigure(encoding="utf-8")
asyncio.run(main())
