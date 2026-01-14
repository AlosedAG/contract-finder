# Contract Finder

A human-assisted government contract discovery tool using DuckDuckGo, Playwright and BeautifulSoup libraries.

## Design Decisions

**Why Playwright instead of Selenium:**  
Playwright provides faster, more reliable browser automation with modern features like built-in waiting, headless mode, and cross-browser support. Unlike Selenium, it handles dynamic content and modern JavaScript-heavy websites more gracefully, which is crucial for interacting with search engines and government portals that rely on dynamic loading. Playwright also allows for easy human-assisted automation when CAPTCHAs appear.

**Why DuckDuckGo instead of Google or Bing:**  
DuckDuckGo was chosen because it does not require an API key, allows real-time web searching, and avoids the strict anti-bot measures that often block automated scripts on Google or Bing. This makes human-assisted automation faster and simpler while still returning high-quality government domain results.

# Usage
## Follow the prompts:

Company name – The main company you want to search contracts for.
Product name – The specific product or service offered by the company.
Summary/context (optional) – Any additional information or description to give context. This is not required but can help keep track of your searches.
Pages to crawl per search – Default is 5. You can increase this if you want more results, but it will take longer.

The tool will open a browser, type your queries into DuckDuckGo, and extract government links and PDFs. When a CAPTCHA appears, you can solve it manually; the tool will continue after you do so. Results are displayed in the console and can optionally be saved as JSON.

## How it Works

1. The script opens a browser using Playwright.
2. It types the company name into DuckDuckGo.
3. It runs multiple search queries combining the company, product, and contract-related keywords.
4. Results are scraped from the search results page, including titles and URLs.
   
Links are scored based on relevance:
- Government domains (.gov, .us, etc.)
- PDFs or pages containing “contract,” “agreement,” etc.
- Mentions of the company and product in the title or URL
- Duplicates are removed, and the highest-scoring results are shown.
- You can optionally save results to data/results.json for later analysis.

# Installation

```bash
pip install -r requirements.txt
python -m playwright install
