from playwright.sync_api import sync_playwright
import time
import json
from typing import List, Dict, Tuple, Optional
from datetime import datetime
import re
from urllib.parse import urlparse, urljoin
import concurrent.futures
import requests
from requests.exceptions import RequestException
import tempfile
import os

# =============================================================================
# CONFIGURATION
# =============================================================================

class SearchConfig:
    """Configuration for search behavior."""
    
    # Domains to always exclude
    BLOCKED_DOMAINS = {
        # Company/vendor sites
        'accela.com', 'www.accela.com', 'tyler.com', 'tylertech.com',
        'civicplus.com', 'granicus.com',
        
        # Review/marketing sites
        'govbusinessreview.com', 'govciooutlook.com', 'g2.com', 'capterra.com',
        'softwareadvice.com', 'softwaresuggest.com', 'gartner.com', 'trustradius.com',
        'getapp.com', 'peerspot.com', 'sourceforge.net', 'slashdot.org',
        'f6s.com', 'toolsinfo.com', 'design.toolsinfo.com', 'saascounter.com',
        
        # Partner/reseller marketing sites
        '3sgplus.com', 'vision33.com', 'contentarch.com', 'sewcopy.com',
        'civicdata.com', 'carahsoft.com',
        
        # Cloud marketplace artifacts
        'catalogartifact.azureedge.net', 'marketplace.microsoft.com',
        'aws.amazon.com', 'azure.microsoft.com',
        
        # User manual aggregator sites
        'usermanual.wiki', 'scribd.com',
        
        # News/press
        'prnewswire.com', 'businesswire.com', 'globenewswire.com',
        'prweb.com', 'prbuzz.com',
        
        # Bid platforms without direct PDF access
        'bidnet.com', 'www.bidnet.com', 'bidnetdirect.com', 'bonfirehub.com',
        'www.bonfirehub.com', 'publicpurchase.com', 'govwin.com', 'bidsync.com',
        'planetbids.com', 'bidexpress.com', 'demandstar.com', 'negometrix.com',
        'ionwave.net', 'bidsandawards.com', 'highergov.com', 'bidbanana.thebidlab.com',
        
        # PDF aggregators (not original sources)
        'pdffiller.com', 'documentcloud.org',
        
        # Social media & generic
        'linkedin.com', 'twitter.com', 'facebook.com', 'youtube.com',
        'wikipedia.org', 'reddit.com',
    }
    
    # Domains that are always good (government)
    TRUSTED_DOMAINS = {'.gov', '.us', '.state.', 'civicweb', 'legistar.com'}
    
    # URL patterns for document repositories
    GOOD_URL_PATTERNS = [
        r'/documents?/', r'/files?/', r'/attachments?/', r'/contracts?/',
        r'/purchasing/', r'/procurement/', r'/bids?/', r'/rfp/', r'/agenda/',
        r'/minutes/', r'/resolutions?/', r'/ordinances?/', r'documentcenter',
        r'weblink', r'edoc', r'civicweb', r'questys', r'laserfiche', r'/archive/',
        r'agendacenter', r'boardagenda', r'boardpacket',
    ]
    
    # User documentation patterns (to filter out)
    USER_DOC_PATTERNS = [
        r'user[-_]?guide', r'how[-_]?to', r'instructions', r'tutorial',
        r'help[-_]?doc', r'getting[-_]?started', r'quick[-_]?start',
        r'admin[-_]?guide', r'administrator[-_]?guide', r'scripting[-_]?guide',
        r'planning[-_]?guide', r'system[-_]?planning', r'concepts[-_]?guide',
        r'training', r'glossary', r'faq',
    ]
    
    USER_DOC_TITLE_PATTERNS = [
        r'user guide', r'user\'s guide', r'how to', r'instructions for',
        r'submission guide', r'submittal guide', r'online permitting system',
        r'getting started', r'tutorial', r'admin guide', r'administrator guide',
        r'scripting guide', r'concepts guide', r'gis administration',
        r'system planning', r'view and manage', r'glossary', r'faq',
    ]
    
    # HIGH-VALUE title patterns - these indicate best pricing documents
    HIGH_VALUE_TITLE_PATTERNS = {
        'order form': 2.5,
        'renewal order form': 2.5,
        'subscription services agreement': 2.0,
        'master services agreement': 2.0,
        'software license agreement': 2.0,
        'license agreement': 1.5,
        'pricing schedule': 2.0,
        'fee schedule': 2.0,
        'cost proposal': 1.5,
        'price proposal': 1.5,
        'cost exhibit': 2.0,
        'pricing exhibit': 2.0,
        'exhibit a': 1.0,  # Often pricing
        'exhibit b': 1.0,
    }


# =============================================================================
# SIMPLIFIED DOCUMENT TYPES (reduced from 13 to 6)
# =============================================================================

class DocumentType:
    """Simplified document type classification."""
    
    # Priority order (higher = better for pricing)
    TYPES = {
        'Order Form': {
            'patterns': ['order form', 'renewal order', 'purchase order'],
            'priority': 1,
            'pricing_likely': True,
            'description': 'Specific pricing and quantities'
        },
        'Contract/Agreement': {
            'patterns': ['agreement', 'contract', 'master service'],
            'exclude_patterns': ['item \\d+', 'agenda', 'staff report', 'memo'],
            'priority': 2,
            'pricing_likely': True,
            'description': 'Full contract terms with pricing'
        },
        'Pricing Document': {
            'patterns': ['pricing', 'fee schedule', 'cost exhibit', 'cost proposal', 'price list'],
            'priority': 3,
            'pricing_likely': True,
            'description': 'Detailed pricing breakdown'
        },
        'Staff Report/Memo': {
            'patterns': ['staff report', 'council report', 'agenda report', 'memo', 'memorandum', 
                        'item \\d+', 'board agenda', 'council agenda'],
            'priority': 4,
            'pricing_likely': False,  # Summary only, not detailed pricing
            'description': 'Government summary - may reference pricing'
        },
        'RFP/Proposal': {
            'patterns': ['rfp', 'request for proposal', 'bid', 'solicitation', 'proposal', 'response'],
            'priority': 5,
            'pricing_likely': False,
            'description': 'Proposed/estimated pricing'
        },
        'Other Government Document': {
            'patterns': [],  # Fallback
            'priority': 6,
            'pricing_likely': False,
            'description': 'Unknown - needs review'
        }
    }
    
    @classmethod
    def classify(cls, url: str, title: str) -> Tuple[str, Dict]:
        """Classify document and return (type_name, type_info)."""
        text = (url + " " + title).lower()
        
        # Check in priority order
        for type_name, type_info in cls.TYPES.items():
            if type_name == 'Other Government Document':
                continue  # Skip fallback
            
            # Check exclude patterns first (for Contract/Agreement)
            if 'exclude_patterns' in type_info:
                if any(re.search(p, text) for p in type_info['exclude_patterns']):
                    continue  # Skip this type, it's actually a staff report
            
            # Check include patterns
            if any(re.search(p, text) for p in type_info['patterns']):
                return type_name, type_info
        
        # Fallback
        return 'Other Government Document', cls.TYPES['Other Government Document']


# =============================================================================
# URL VALIDATION & LINK CHECKING
# =============================================================================

def check_link_validity(url: str, timeout: int = 10) -> Tuple[bool, int, str]:
    """Check if a URL is accessible."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.head(url, timeout=timeout, allow_redirects=True, headers=headers)
        
        if response.status_code == 405:
            response = requests.get(url, timeout=timeout, allow_redirects=True, 
                                   headers=headers, stream=True)
        
        if response.status_code == 200:
            return True, 200, "OK"
        elif response.status_code in [301, 302, 303, 307, 308]:
            return True, response.status_code, "Redirect"
        elif response.status_code == 403:
            return True, 403, "Forbidden (may still work)"
        elif response.status_code == 404:
            return False, 404, "Not Found"
        else:
            return False, response.status_code, f"HTTP {response.status_code}"
            
    except requests.exceptions.Timeout:
        return False, 0, "Timeout"
    except requests.exceptions.SSLError:
        return False, 0, "SSL Error"
    except requests.exceptions.ConnectionError:
        return False, 0, "Connection Error"
    except Exception as e:
        return False, 0, str(e)[:50]


def batch_check_links(urls: List[str], max_workers: int = 5) -> Dict[str, Tuple[bool, int, str]]:
    """Check multiple URLs in parallel."""
    results = {}
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(check_link_validity, url): url for url in urls}
        
        for future in concurrent.futures.as_completed(future_to_url):
            url = future_to_url[future]
            try:
                results[url] = future.result()
            except Exception as e:
                results[url] = (False, 0, str(e)[:50])
    
    return results


def validate_links_batch(results: List[Dict], max_to_check: int = 30) -> List[Dict]:
    """Add link validity to results."""
    urls_to_check = [r['url'] for r in results[:max_to_check]]
    
    print(f"\n  Validating {len(urls_to_check)} links...")
    validity = batch_check_links(urls_to_check)
    
    for result in results:
        if result['url'] in validity:
            is_valid, status, reason = validity[result['url']]
            result['link_valid'] = is_valid
            result['link_status'] = status
            result['link_status_reason'] = reason
        else:
            result['link_valid'] = None
            result['link_status'] = None
            result['link_status_reason'] = 'Not checked'
    
    return results


# =============================================================================
# DOMAIN & URL ANALYSIS
# =============================================================================

def get_domain(url: str) -> str:
    """Extract domain from URL."""
    try:
        return urlparse(url).netloc.lower()
    except:
        return ""


def is_blocked_domain(url: str) -> Tuple[bool, str]:
    """Check if URL is from a blocked domain."""
    domain = get_domain(url)
    for blocked in SearchConfig.BLOCKED_DOMAINS:
        if blocked in domain:
            return True, blocked
    return False, ""


def is_trusted_domain(url: str) -> bool:
    """Check if URL is from a trusted domain."""
    domain = get_domain(url)
    url_lower = url.lower()
    for trusted in SearchConfig.TRUSTED_DOMAINS:
        if trusted in domain or trusted in url_lower:
            return True
    return False


def has_good_url_pattern(url: str) -> bool:
    """Check if URL has document repository patterns."""
    url_lower = url.lower()
    for pattern in SearchConfig.GOOD_URL_PATTERNS:
        if re.search(pattern, url_lower):
            return True
    return False


def is_user_documentation(url: str, title: str) -> bool:
    """Check if this is end-user documentation."""
    url_lower = url.lower()
    title_lower = title.lower()
    
    for pattern in SearchConfig.USER_DOC_PATTERNS:
        if re.search(pattern, url_lower):
            return True
    
    for pattern in SearchConfig.USER_DOC_TITLE_PATTERNS:
        if re.search(pattern, title_lower):
            return True
    
    return False


def is_pdf_url(url: str) -> bool:
    """Check if URL points to a PDF."""
    return '.pdf' in url.lower()


# =============================================================================
# LOCATION EXTRACTION
# =============================================================================

def extract_location(url: str, title: str) -> str:
    """
    Extract location (city/county/state) from URL and title.
    Returns formatted string like "Hillsboro, OR" or "San Francisco County, CA"
    """
    url_lower = url.lower()
    title_lower = title.lower()
    text = url_lower + " " + title_lower
    
    # US State abbreviations and full names
    states = {
        'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR',
        'california': 'CA', 'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE',
        'florida': 'FL', 'georgia': 'GA', 'hawaii': 'HI', 'idaho': 'ID',
        'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA', 'kansas': 'KS',
        'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
        'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS',
        'missouri': 'MO', 'montana': 'MT', 'nebraska': 'NE', 'nevada': 'NV',
        'new hampshire': 'NH', 'new jersey': 'NJ', 'new mexico': 'NM', 'new york': 'NY',
        'north carolina': 'NC', 'north dakota': 'ND', 'ohio': 'OH', 'oklahoma': 'OK',
        'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
        'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT',
        'vermont': 'VT', 'virginia': 'VA', 'washington': 'WA', 'west virginia': 'WV',
        'wisconsin': 'WI', 'wyoming': 'WY', 'district of columbia': 'DC'
    }
    
    abbrev_to_full = {v: k.title() for k, v in states.items()}
    
    found_state_abbrev = None
    found_city = None
    found_county = None
    is_state_level = False
    
    domain = get_domain(url)
    
    # Pattern: city-state in domain (hillsboro-oregon)
    domain_match = re.search(r'([a-z]+)-([a-z]+)\.(?:civicweb|legistar)', domain)
    if domain_match:
        potential_city = domain_match.group(1)
        potential_state = domain_match.group(2)
        if potential_state in states:
            found_city = potential_city.title()
            found_state_abbrev = states[potential_state]
    
    # Pattern: citySTATE.gov (berkeleyca.gov, galvestontx.gov)
    if not found_city:
        domain_match = re.search(r'(?:www\.)?([a-z]+)(ca|tx|ny|fl|wa|or|az|co|il|oh|pa|ga|nc|nj|va|ma|mi|md|mn|mo|wi|tn|in|ks|ne|nv)\.gov', domain)
        if domain_match:
            found_city = domain_match.group(1).title()
            found_state_abbrev = domain_match.group(2).upper()
    
    # Pattern: city.STATE.us
    if not found_city:
        domain_match = re.search(r'(?:www\.)?([a-z]+)\.([a-z]{2})\.us', domain)
        if domain_match:
            found_city = domain_match.group(1).title()
            found_state_abbrev = domain_match.group(2).upper()
    
    # Pattern: co.COUNTY.STATE.us
    if not found_city:
        domain_match = re.search(r'co\.([a-z]+)\.([a-z]{2})\.us', domain)
        if domain_match:
            found_county = domain_match.group(1).title()
            found_state_abbrev = domain_match.group(2).upper()
    
    # Pattern: COUNTYcounty.gov
    if not found_city and not found_county:
        domain_match = re.search(r'(?:www\.)?([a-z]+)county\.(?:gov|org|us)', domain)
        if domain_match:
            found_county = domain_match.group(1).title()
    
    # Pattern: state-level sites
    if not found_city and not found_county:
        domain_match = re.search(r'(?:state|das|dgs|doa)\.([a-z]{2})\.(?:us|gov)', domain)
        if domain_match:
            found_state_abbrev = domain_match.group(1).upper()
            is_state_level = True
    
    # Known cities database
    known_cities = {
        'anaheim': 'CA', 'berkeley': 'CA', 'san diego': 'CA', 'san francisco': 'CA',
        'los angeles': 'CA', 'oakland': 'CA', 'sacramento': 'CA', 'fresno': 'CA',
        'long beach': 'CA', 'santa ana': 'CA', 'palo alto': 'CA', 'santa barbara': 'CA',
        'galveston': 'TX', 'houston': 'TX', 'dallas': 'TX', 'austin': 'TX',
        'san antonio': 'TX', 'fort worth': 'TX', 'arlington': 'TX', 'plano': 'TX',
        'tacoma': 'WA', 'seattle': 'WA', 'spokane': 'WA', 'bellevue': 'WA',
        'hillsboro': 'OR', 'portland': 'OR', 'salem': 'OR', 'eugene': 'OR',
        'denver': 'CO', 'colorado springs': 'CO', 'aurora': 'CO', 'boulder': 'CO',
        'phoenix': 'AZ', 'tucson': 'AZ', 'mesa': 'AZ', 'scottsdale': 'AZ', 'goodyear': 'AZ',
        'charlotte': 'NC', 'raleigh': 'NC', 'durham': 'NC', 'greensboro': 'NC',
        'tampa': 'FL', 'miami': 'FL', 'orlando': 'FL', 'jacksonville': 'FL', 'brevard': 'FL',
        'papillion': 'NE', 'omaha': 'NE', 'lincoln': 'NE',
        'andover': 'KS', 'wichita': 'KS', 'kansas city': 'KS',
        'moreno valley': 'CA', 'moval': 'CA', 'merced': 'CA', 'stanislaus': 'CA',
        'stockton': 'CA', 'modesto': 'CA', 'fontana': 'CA', 'mendocino': 'CA',
        'columbus': 'OH', 'cleveland': 'OH', 'cincinnati': 'OH',
        'watertown': 'NY', 'buffalo': 'NY', 'rochester': 'NY', 'albany': 'NY',
        'butte': 'MT', 'silver bow': 'MT', 'silverbow': 'MT',
        'washoe': 'NV', 'kern': 'CA', 'evanston': 'IL', 'mulberry': 'FL',
    }
    
    if not found_city and not found_county:
        for city, state in known_cities.items():
            if city in text:
                if 'county' in text:
                    found_county = city.title()
                else:
                    found_city = city.title()
                if not found_state_abbrev:
                    found_state_abbrev = state
                break
    
    # "City of X" pattern
    city_of_match = re.search(r'city of ([a-z\s]+?)(?:,|\s*-|\s*\||$)', text)
    if city_of_match and not found_city:
        found_city = city_of_match.group(1).strip().title()
    
    # "County of X" pattern
    county_of_match = re.search(r'county of ([a-z\s]+?)(?:,|\s*-|\s*\||$)', text)
    if county_of_match and not found_county:
        found_county = county_of_match.group(1).strip().title()
    
    # State in text
    if not found_state_abbrev:
        for state_name, abbrev in states.items():
            if state_name in text:
                found_state_abbrev = abbrev
                break
    
    # Build final location string
    if is_state_level and found_state_abbrev:
        state_full = abbrev_to_full.get(found_state_abbrev, found_state_abbrev)
        return f"State of {state_full}"
    
    if found_county and found_state_abbrev:
        county_name = found_county.replace(' County', '').replace(' county', '')
        return f"{county_name} County, {found_state_abbrev}"
    
    if found_city and found_state_abbrev:
        return f"{found_city}, {found_state_abbrev}"
    
    if found_city:
        return found_city
    
    if found_county:
        return f"{found_county} County"
    
    if found_state_abbrev:
        return found_state_abbrev
    
    return "Unknown"


# =============================================================================
# SEARCH QUERY GENERATION
# =============================================================================

def generate_search_queries(company: str, product: str, context: str, 
                           search_type: str = 'software') -> List[Dict[str, str]]:
    """Generate search queries with metadata."""
    queries = []
    
    # HIGH PRIORITY - Order forms and direct agreements (best for pricing)
    order_form_queries = [
        (f'"{company}" "order form" pdf', 'order_form'),
        (f'"{company}" "renewal order form" pdf', 'order_form'),
        (f'"{company}" "subscription services agreement" pdf', 'agreement'),
        (f'"{company}" "master services agreement" pdf', 'agreement'),
        (f'"{company}" "{product}" "order form" pdf', 'order_form'),
    ]
    
    # Contract/Agreement queries
    contract_queries = [
        (f'"{company}" "{product}" contract pdf', 'contract'),
        (f'"{company}" "{product}" agreement pdf', 'agreement'),
        (f'"{company}" "{product}" city contract pdf', 'contract'),
        (f'"{company}" "{product}" county contract pdf', 'contract'),
        (f'"{company}" contract renewal pdf', 'renewal'),
    ]
    
    # Pricing-specific queries
    pricing_queries = [
        (f'"{company}" "{product}" pricing schedule pdf', 'pricing'),
        (f'"{company}" "{product}" fee schedule pdf', 'pricing'),
        (f'"{company}" "{product}" cost proposal pdf', 'pricing'),
        (f'"{company}" "exhibit" pricing pdf', 'pricing'),
    ]
    
    # Software license queries
    software_queries = [
        (f'"{company}" "{product}" software license agreement pdf', 'software_license'),
        (f'"{company}" "{product}" software subscription pdf', 'software_license'),
        (f'"{company}" "{product}" SaaS agreement pdf', 'software_license'),
    ]
    
    # Government document queries (lower priority - summaries, not full contracts)
    gov_doc_queries = [
        (f'"{company}" "{product}" staff report pdf', 'staff_report'),
        (f'"{company}" "{product}" council agenda pdf', 'agenda'),
        (f'"{company}" civicweb contract', 'civicweb'),
        (f'site:civicweb.net "{company}" contract', 'civicweb'),
    ]
    
    # Build query list with priorities
    for q, cat in order_form_queries:
        queries.append({'query': q, 'category': cat, 'priority': 'highest'})
    
    for q, cat in pricing_queries:
        queries.append({'query': q, 'category': cat, 'priority': 'high'})
    
    for q, cat in contract_queries:
        queries.append({'query': q, 'category': cat, 'priority': 'high'})
    
    if search_type in ['software', 'both']:
        for q, cat in software_queries:
            queries.append({'query': q, 'category': cat, 'priority': 'high'})
    
    for q, cat in gov_doc_queries:
        queries.append({'query': q, 'category': cat, 'priority': 'medium'})
    
    return queries


# =============================================================================
# DUCKDUCKGO SEARCH
# =============================================================================

def duckduckgo_search(query: str, max_results: int = 30) -> List[Dict[str, str]]:
    """Perform DuckDuckGo search."""
    results = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = context.new_page()
        
        try:
            page.goto("https://duckduckgo.com/", timeout=60000)
            page.wait_for_selector("input[name='q']", timeout=10000)
            page.fill("input[name='q']", query)
            page.keyboard.press("Enter")
            page.wait_for_selector("article[data-testid='result']", timeout=15000)
            
            for scroll in range(8):
                page.mouse.wheel(0, 1500)
                time.sleep(0.8)
                try:
                    more_btn = page.query_selector("button:has-text('More results')")
                    if more_btn:
                        more_btn.click()
                        time.sleep(1)
                except:
                    pass
            
            time.sleep(1)
            
            # Get results
            result_links = page.query_selector_all("a[data-testid='result-title-a']")
            for link in result_links:
                try:
                    title = link.inner_text()
                    url = link.get_attribute("href")
                    if title and url and url.startswith('http'):
                        results.append({'title': title.strip(), 'url': url})
                except:
                    continue
            
        except Exception as e:
            print(f"  ⚠ Search error: {e}")
        finally:
            browser.close()
    
    # Deduplicate
    seen = set()
    unique = []
    for r in results:
        if r['url'] not in seen:
            seen.add(r['url'])
            unique.append(r)
    
    return unique[:max_results]


# =============================================================================
# RESULT FILTERING & SCORING
# =============================================================================

def filter_result(result: Dict, company: str, product: str, 
                 search_type: str = 'software') -> Tuple[bool, str]:
    """Determine if a result should be included."""
    url = result['url']
    title = result.get('title', '')
    text = (title + " " + url).lower()
    
    # Check blocked domains
    is_blocked, blocked_domain = is_blocked_domain(url)
    if is_blocked:
        return False, f"Blocked domain: {blocked_domain}"
    
    company_lower = company.lower()
    product_lower = product.lower()
    
    has_company = company_lower in text
    has_product = product_lower in text
    
    # Check user documentation
    if is_user_documentation(url, title):
        return False, "User documentation"
    
    # Direct PDF from trusted domain
    if is_pdf_url(url) and is_trusted_domain(url):
        return True, "PDF from trusted domain"
    
    # PDF with good URL pattern
    if is_pdf_url(url) and has_good_url_pattern(url):
        return True, "PDF from document repository"
    
    # Need company or product match
    if not has_company and not has_product:
        return False, "No company/product match"
    
    # Contract keywords
    contract_keywords = [
        'contract', 'agreement', 'procurement', 'purchasing', 'rfp', 'bid',
        'proposal', 'memo', 'resolution', 'agenda', 'ordinance', 'staff report',
        'sow', 'statement of work', 'award', 'amendment', 'renewal', 'pricing',
        'order form', 'fee schedule',
    ]
    
    if any(kw in text for kw in contract_keywords):
        return True, "Has contract keyword"
    
    if is_trusted_domain(url) and (has_company or has_product):
        return True, "Trusted domain with match"
    
    if has_good_url_pattern(url):
        return True, "Document repository pattern"
    
    return False, "No contract signals"


def score_result(result: Dict, company: str, product: str) -> Tuple[float, List[str]]:
    """
    Score result on 0-10 scale.
    
    SCORING BREAKDOWN:
    - Entity matching: max 3.0 (company +1.5, product +1.5)
    - Document format: max 1.5 (PDF +1.5)
    - Domain trust: max 2.5 (.gov +2.5, trusted +2.0, doc repo +1.0)
    - Document type: max 2.5 (Order Form +2.5, Contract +2.0, Pricing +2.0, Staff Report +1.0)
    - High-value title: max 2.5 (based on title patterns)
    - Recency: max 1.0 (2024+ = +1.0, 2022+ = +0.5)
    - Penalties: user doc -3.0, login page -2.0
    """
    score = 0.0
    reasons = []
    url = result['url'].lower()
    title = result.get('title', '').lower()
    text = title + " " + url
    
    company_lower = company.lower()
    product_lower = product.lower()
    
    # === ENTITY MATCHING (max 3.0) ===
    if company_lower in text:
        score += 1.5
        reasons.append("+1.5 company")
    if product_lower in text:
        score += 1.5
        reasons.append("+1.5 product")
    
    # === DOCUMENT FORMAT (max 1.5) ===
    if is_pdf_url(url):
        score += 1.5
        reasons.append("+1.5 PDF")
    
    # === DOMAIN TRUST (max 2.5) ===
    if '.gov' in url:
        score += 2.5
        reasons.append("+2.5 .gov")
    elif is_trusted_domain(url):
        score += 2.0
        reasons.append("+2.0 trusted")
    elif has_good_url_pattern(url):
        score += 1.0
        reasons.append("+1.0 doc repo")
    
    # === DOCUMENT TYPE BONUS (max 2.5) ===
    doc_type, doc_info = DocumentType.classify(url, title)
    
    if doc_type == 'Order Form':
        score += 2.5
        reasons.append("+2.5 order form")
    elif doc_type == 'Contract/Agreement':
        score += 2.0
        reasons.append("+2.0 contract")
    elif doc_type == 'Pricing Document':
        score += 2.0
        reasons.append("+2.0 pricing doc")
    elif doc_type == 'Staff Report/Memo':
        score += 1.0
        reasons.append("+1.0 staff report")
    elif doc_type == 'RFP/Proposal':
        score += 0.5
        reasons.append("+0.5 rfp/proposal")
    
    # === HIGH-VALUE TITLE PATTERNS (max 2.5) ===
    title_bonus = 0.0
    for pattern, bonus in SearchConfig.HIGH_VALUE_TITLE_PATTERNS.items():
        if pattern in title:
            title_bonus = max(title_bonus, bonus)  # Take highest match
    
    if title_bonus > 0:
        score += title_bonus
        reasons.append(f"+{title_bonus} title pattern")
    
    # === RECENCY (max 1.0) ===
    years = re.findall(r'20(2[0-6]|1[9])', text)
    if years:
        latest = max(int('20' + y) for y in years)
        if latest >= 2024:
            score += 1.0
            reasons.append(f"+1.0 {latest}")
        elif latest >= 2022:
            score += 0.5
            reasons.append(f"+0.5 {latest}")
    
    # === PENALTIES ===
    if is_user_documentation(url, title):
        score -= 3.0
        reasons.append("-3.0 user doc")
    
    if re.search(r'(login|signin|welcome|default)\.aspx?$', url):
        score -= 2.0
        reasons.append("-2.0 login page")
    
    # Store document type for later use
    result['document_type'] = doc_type
    result['pricing_likely'] = doc_info['pricing_likely']
    
    return max(round(score, 1), 0.0), reasons


def process_results(results: List[Dict], company: str, product: str,
                   search_type: str = 'software', verbose: bool = False) -> List[Dict]:
    """Filter and score all results."""
    processed = []
    
    for result in results:
        include, reason = filter_result(result, company, product, search_type)
        
        if include:
            score, score_reasons = score_result(result, company, product)
            result['relevance_score'] = score
            result['score_breakdown'] = score_reasons
            result['include_reason'] = reason
            processed.append(result)
        elif verbose:
            print(f"      Excluded: {reason} - {result.get('title', '')[:40]}")
    
    processed.sort(key=lambda x: x['relevance_score'], reverse=True)
    return processed


# =============================================================================
# DEDUPLICATION & LOCATION DIVERSITY
# =============================================================================

def normalize_url(url: str) -> str:
    """Normalize URL for deduplication."""
    url = url.lower().split('#')[0].rstrip('/')
    if '?' in url:
        base, params = url.split('?', 1)
        keep = [p for p in params.split('&') if any(k in p for k in ['id=', 'doc=', 'file='])]
        url = base + ('?' + '&'.join(keep) if keep else '')
    return url


def deduplicate_results(results: List[Dict]) -> List[Dict]:
    """Remove duplicate URLs."""
    seen = {}
    for result in results:
        norm = normalize_url(result['url'])
        if norm not in seen or result.get('relevance_score', 0) > seen[norm].get('relevance_score', 0):
            seen[norm] = result
    return list(seen.values())


def apply_location_diversity(results: List[Dict], penalty_per_duplicate: float = 1.5, 
                            max_penalty: float = 4.0) -> List[Dict]:
    """
    Apply diversity penalty to results from the same location.
    First result from each location gets no penalty, subsequent results get increasing penalties.
    """
    location_counts = {}
    
    for result in results:
        location = extract_location(result['url'], result.get('title', ''))
        result['location'] = location
        
        if location == 'Unknown':
            continue  # Don't penalize unknown locations
        
        count = location_counts.get(location, 0)
        if count > 0:
            # Apply penalty for duplicate locations
            penalty = min(count * penalty_per_duplicate, max_penalty)
            original_score = result['relevance_score']
            result['relevance_score'] = max(result['relevance_score'] - penalty, 0)
            result['score_breakdown'].append(f"-{penalty:.1f} location #{count+1}")
            result['diversity_penalty'] = penalty
        
        location_counts[location] = count + 1
    
    # Re-sort after applying penalties
    results.sort(key=lambda x: x['relevance_score'], reverse=True)
    return results


# =============================================================================
# OUTPUT & REPORTING
# =============================================================================

def display_results(results: List[Dict], company: str, product: str, show_breakdown: bool = False):
    """Display formatted results."""
    print(f"\n{'=' * 80}")
    print("TOP RESULTS (sorted by relevance with location diversity)")
    print('=' * 80)
    
    for i, r in enumerate(results[:20], 1):
        score = r.get('relevance_score', 0)
        valid = r.get('link_valid')
        
        # Status indicator
        if valid is True:
            status = "✓"
        elif valid is False:
            status = "✗"
        else:
            status = "?"
        
        bar = '█' * int(score) + '░' * (10 - int(score))
        cat = "HIGH" if score >= 7 else ("MED " if score >= 5 else "LOW ")
        
        # Get location
        location = r.get('location') or extract_location(r['url'], r.get('title', ''))
        
        # Get document type
        doc_type = r.get('document_type', 'Unknown')
        pricing_likely = r.get('pricing_likely', False)
        pricing_indicator = "💰" if pricing_likely else "📄"
        
        print(f"\n{i:2}. [{bar}] {score:4.1f}/10 {cat} {status}")
        print(f"    📍 {location}")
        print(f"    {pricing_indicator} [{doc_type}] {r['title'][:65]}")
        print(f"    {r['url'][:100]}")
        
        if show_breakdown and r.get('score_breakdown'):
            print(f"    Scoring: {'; '.join(r['score_breakdown'][:5])}")
        
        if valid is False:
            print(f"    ⚠ Link issue: {r.get('link_status_reason', 'Unknown')}")


def save_results(results: List[Dict], company: str, product: str, 
                context: str, search_type: str, filename: str):
    """Save results to JSON and CSV."""
    
    # === SAVE JSON ===
    output = {
        'metadata': {
            'company': company,
            'product': product,
            'search_type': search_type,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_results': len(results),
            'version': '7.0',
        },
        'results': []
    }
    
    for i, r in enumerate(results):
        location = r.get('location') or extract_location(r['url'], r.get('title', ''))
        doc_type = r.get('document_type', 'Unknown')
        
        output['results'].append({
            'rank': i + 1,
            'score': r['relevance_score'],
            'title': r['title'],
            'url': r['url'],
            'location': location,
            'document_type': doc_type,
            'pricing_likely': r.get('pricing_likely', False),
            'link_valid': r.get('link_valid'),
            'score_breakdown': r.get('score_breakdown', []),
        })
    
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"✓ Saved JSON to {filename}")
    
    # === SAVE CSV ===
    csv_filename = filename.replace('.json', '.csv')
    if csv_filename == filename:
        csv_filename = filename + '.csv'
    
    with open(csv_filename, 'w', encoding='utf-8') as f:
        # Header - simplified and cleaner
        f.write('Rank,Score,Category,Link,Location,Document_Type,Pricing_Likely,Title,URL\n')
        
        for i, r in enumerate(results, 1):
            score = r.get('relevance_score', 0)
            category = "HIGH" if score >= 7 else ("MEDIUM" if score >= 5 else "LOW")
            
            # Link validity
            valid = r.get('link_valid')
            if valid is True:
                link_status = "✓"
            elif valid is False:
                link_status = "✗"
            else:
                link_status = "?"
            
            location = r.get('location') or extract_location(r['url'], r.get('title', ''))
            doc_type = r.get('document_type', 'Unknown')
            pricing_likely = "Yes" if r.get('pricing_likely', False) else "No"
            
            # Escape for CSV
            title = r.get('title', '').replace('"', '""').replace('\n', ' ')
            url = r.get('url', '')
            
            f.write(f'{i},{score},{category},{link_status},"{location}","{doc_type}",{pricing_likely},"{title}","{url}"\n')
    
    print(f"✓ Saved CSV to {csv_filename}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 80)
    print("GOVERNMENT CONTRACT SEARCH v7.0")
    print("=" * 80)
    print("\nImprovements in v7:")
    print("  ✓ Location diversity (avoids duplicate locations at top)")
    print("  ✓ Simplified document types (6 types, focused on pricing)")
    print("  ✓ Better title pattern matching (Order Form, Agreement, etc.)")
    print("  ✓ Fixed: Staff reports no longer classified as contracts")
    print("  ✓ Order Forms ranked higher (best for pricing)")
    
    # Get inputs
    company = input("\nCompany name: ").strip()
    product = input("Product name: ").strip()
    
    if not company or not product:
        print("❌ Company and product required!")
        return
    
    print("\nSearch type:")
    print("  1. Software licenses/subscriptions")
    print("  2. Implementation/services")
    print("  3. Both")
    search_type = {'1': 'software', '2': 'implementation', '3': 'both'}.get(
        input("Choice (1-3, default 3): ").strip() or "3", 'both')
    
    # Generate and run queries
    queries = generate_search_queries(company, product, "", search_type)
    
    try:
        num_queries = int(input(f"\nQueries to run (1-{len(queries)}, default 10): ").strip() or "10")
        num_queries = min(max(1, num_queries), len(queries))
    except:
        num_queries = 10
    
    all_results = []
    
    print(f"\n{'=' * 80}")
    print(f"RUNNING {num_queries} SEARCHES")
    print('=' * 80)
    
    for i, q in enumerate(queries[:num_queries], 1):
        print(f"\n[{i}/{num_queries}] {q['query']}")
        raw = duckduckgo_search(q['query'], max_results=25)
        print(f"  Found: {len(raw)} results")
        
        processed = process_results(raw, company, product, search_type, verbose=False)
        print(f"  Kept: {len(processed)} relevant")
        
        all_results.extend(processed)
        
        if i < num_queries:
            time.sleep(2)
    
    # Deduplicate and sort
    all_results = deduplicate_results(all_results)
    all_results.sort(key=lambda x: x['relevance_score'], reverse=True)
    print(f"\n{'=' * 80}")
    print(f"UNIQUE RESULTS: {len(all_results)}")
    print('=' * 80)
    
    # Link validation
    check_links = input("\nValidate links? (y/n, default y): ").strip().lower() != 'n'
    if check_links:
        all_results = validate_links_batch(all_results)
        
        valid_count = sum(1 for r in all_results if r.get('link_valid') is True)
        invalid_count = sum(1 for r in all_results if r.get('link_valid') is False)
        print(f"  Valid: {valid_count}, Broken: {invalid_count}")
        
        remove_broken = input("Remove broken links? (y/n, default n): ").strip().lower() == 'y'
        if remove_broken:
            all_results = [r for r in all_results if r.get('link_valid') is not False]
            print(f"  Kept {len(all_results)} results")
    
    # Apply location diversity
    print("\nApplying location diversity...")
    all_results = apply_location_diversity(all_results)
    
    # Display
    display_results(all_results, company, product, show_breakdown=True)
    
    # Save
    if input("\nSave results? (y/n, default y): ").strip().lower() != 'n':
        default_name = f"{company.lower().replace(' ', '_')}_contracts.json"
        filename = input(f"Filename (default: {default_name}): ").strip() or default_name
        if not filename.endswith('.json'):
            filename += '.json'
        save_results(all_results, company, product, "", search_type, filename)
    
    print("\n✓ Done!")


if __name__ == "__main__":
    main()
    