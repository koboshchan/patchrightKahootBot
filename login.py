"""Log in once and save cookies for the bot.

Run on your PC:  python login.py
A browser opens with Google and Kahoot tabs. Sign into whatever you need
(open more tabs for other sites), then press Enter here. Saves session.json; upload it to the bot with /setlogin.
"""
import asyncio
import os

from patchright.async_api import async_playwright

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session.json")
SITES = ["https://accounts.google.com/", "https://kahoot.it/"]


async def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, channel="chromium")
        context = await browser.new_context(
            storage_state=OUT if os.path.exists(OUT) else None
        )
        for url in SITES:
            page = await context.new_page()
            await page.goto(url)
        await asyncio.to_thread(input, "Log in, then press Enter here to save... ")
        await context.storage_state(path=OUT)
        await browser.close()
    print(f"Saved {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
