from playwright.sync_api import sync_playwright
import time

def duckduckgo_search(query, max_results=20):
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # visible = safer
        page = browser.new_page()

        page.goto("https://duckduckgo.com/", timeout=60000)
        page.wait_for_selector("input[name='q']")

        # Mimic human typing
        page.type("input[name='q']", query, delay=80)
        page.keyboard.press("Enter")

        page.wait_for_selector("a[data-testid='result-title-a']", timeout=60000)

        # Scroll to load more results
        for _ in range(3):
            page.mouse.wheel(0, 3000)
            time.sleep(1)

        links = page.query_selector_all("a[data-testid='result-title-a']")

        for link in links[:max_results]:
            results.append({
                "title": link.inner_text(),
                "url": link.get_attribute("href")
            })

        browser.close()

    return results


if __name__ == "__main__":
    q = '"Tyler Technologies" site:.gov filetype:pdf'
    results = duckduckgo_search(q)

    for r in results:
        print(r["title"])
        print(r["url"])
        print("-" * 40)

            