from playwright.sync_api import sync_playwright
import time
import json
from typing import List, Dict


def generate_search_queries(company: str, product: str, context: str) -> List[str]:
    """Generate multiple targeted search queries based on company, product, and context."""
    queries = [
        f'"{company}" "{product}" site:.gov filetype:pdf',
        f'"{company}" "{product}" government contract',
        f'"{company}" city contract pdf "{product}"',
        f'"{company}" "{product}" procurement site:.gov',
        f'"{company}" RFP "{product}" site:.gov',
        f'"{company}" agreement "{product}" filetype:pdf',
    ]
    
    # Add context-based queries if context is provided
    if context and context.strip():
        context_keywords = extract_keywords(context)
        for keyword in context_keywords[:2]:  # Use top 2 keywords
            queries.append(f'"{company}" "{product}" {keyword} site:.gov')
    
    return queries


def extract_keywords(text: str) -> List[str]:
    """Extract meaningful keywords from context text."""
    # Remove common words
    stopwords = {'a', 'an', 'the', 'and', 'or', 'but', 'for', 'with', 'that', 'this', 
                 'from', 'to', 'in', 'on', 'at', 'by', 'is', 'are', 'was', 'were',
                 'help', 'helps', 'provide', 'provides', 'support', 'supports'}
    
    words = text.lower().split()
    keywords = [w.strip('.,!?;:') for w in words if w.lower() not in stopwords and len(w) > 3]
    return keywords


def duckduckgo_search(query: str, max_results: int = 20) -> List[Dict[str, str]]:
    """Perform a DuckDuckGo search and return results."""
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        try:
            page.goto("https://duckduckgo.com/", timeout=60000)
            page.wait_for_selector("input[name='q']", timeout=10000)

            # Mimic human typing
            page.type("input[name='q']", query, delay=80)
            page.keyboard.press("Enter")

            page.wait_for_selector("a[data-testid='result-title-a']", timeout=60000)

            # Scroll to load more results
            for _ in range(3):
                page.mouse.wheel(0, 3000)
                time.sleep(1.5)

            links = page.query_selector_all("a[data-testid='result-title-a']")

            for link in links[:max_results]:
                title = link.inner_text()
                url = link.get_attribute("href")
                if title and url:
                    results.append({
                        "title": title,
                        "url": url
                    })

        except Exception as e:
            print(f"Error during search: {e}")
        finally:
            browser.close()

    return results


def save_results(results: Dict[str, List[Dict]], filename: str = "search_results.json"):
    """Save search results to a JSON file."""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n✓ Results saved to {filename}")


def main():
    """Main execution function with interactive prompts."""
    print("=" * 60)
    print("DuckDuckGo Government Contract Search Tool")
    print("=" * 60)
    
    # Get user input
    company = input("\nEnter company name: ").strip()
    product = input("Enter product name: ").strip()
    context = input("Enter product summary/context (optional): ").strip()
    
    if not company or not product:
        print("\n❌ Error: Company and product names are required!")
        return
    
    # Ask for max results
    try:
        max_results_input = input("\nMax results per query (default 20): ").strip()
        max_results = int(max_results_input) if max_results_input else 20
    except ValueError:
        max_results = 20
        print("Invalid input, using default: 20")
    
    # Ask how many queries to run
    print("\nGenerated search queries:")
    queries = generate_search_queries(company, product, context)
    for i, q in enumerate(queries, 1):
        print(f"  {i}. {q}")
    
    try:
        num_queries_input = input(f"\nHow many queries to run? (1-{len(queries)}, default: 3): ").strip()
        num_queries = int(num_queries_input) if num_queries_input else 3
        num_queries = min(max(1, num_queries), len(queries))
    except ValueError:
        num_queries = 3
        print("Invalid input, using default: 3")
    
    # Execute searches
    all_results = {}
    
    for i, query in enumerate(queries[:num_queries], 1):
        print(f"\n{'=' * 60}")
        print(f"Search {i}/{num_queries}: {query}")
        print('=' * 60)
        
        results = duckduckgo_search(query, max_results)
        all_results[query] = results
        
        if results:
            print(f"\nFound {len(results)} results:")
            for j, r in enumerate(results, 1):
                print(f"\n{j}. {r['title']}")
                print(f"   {r['url']}")
        else:
            print("No results found.")
        
        # Wait between searches to avoid rate limiting
        if i < num_queries:
            print("\nWaiting 3 seconds before next search...")
            time.sleep(3)
    
    # Summary
    print(f"\n{'=' * 60}")
    print("SEARCH SUMMARY")
    print('=' * 60)
    total_results = sum(len(results) for results in all_results.values())
    print(f"Total queries executed: {num_queries}")
    print(f"Total results found: {total_results}")
    
    # Save results
    save_choice = input("\nSave results to JSON file? (y/n): ").strip().lower()
    if save_choice == 'y':
        filename = input("Enter filename (default: search_results.json): ").strip()
        if not filename:
            filename = "search_results.json"
        elif not filename.endswith('.json'):
            filename += '.json'
        
        save_results(all_results, filename)


if __name__ == "__main__":
    main()