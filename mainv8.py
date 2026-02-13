"""
GOVERNMENT CONTRACT SEARCH v7.5

Required libraries (install before running):
    pip install playwright
    pip install requests
    pip install pdfplumber

Optional (faster PDF processing):
    pip install pymupdf

First-time setup for Playwright:
    playwright install chromium
"""

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
# PDF LIBRARY DETECTION (checked at runtime)
# =============================================================================

def check_pdf_libraries() -> Tuple[bool, bool]:
    """Check which PDF libraries are available."""
    has_pdfplumber = False
    has_pymupdf = False
    
    try:
        import pdfplumber
        has_pdfplumber = True
    except ImportError:
        pass
    
    try:
        import fitz  # PyMuPDF
        has_pymupdf = True
    except ImportError:
        pass
    
    return has_pdfplumber, has_pymupdf


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
        'exhibit a': 1.0,
        'exhibit b': 1.0,
    }


# =============================================================================
# SIMPLIFIED DOCUMENT TYPES (6 types)
# =============================================================================

class DocumentType:
    """Simplified document type classification."""
    
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
            'pricing_likely': False,
            'description': 'Government summary - may reference pricing'
        },
        'RFP/Proposal': {
            'patterns': ['rfp', 'request for proposal', 'bid', 'solicitation', 'proposal', 'response'],
            'priority': 5,
            'pricing_likely': False,
            'description': 'Proposed/estimated pricing'
        },
        'Other Government Document': {
            'patterns': [],
            'priority': 6,
            'pricing_likely': False,
            'description': 'Unknown - needs review'
        }
    }
    
    @classmethod
    def classify(cls, url: str, title: str) -> Tuple[str, Dict]:
        """Classify document and return (type_name, type_info)."""
        text = (url + " " + title).lower()
        
        for type_name, type_info in cls.TYPES.items():
            if type_name == 'Other Government Document':
                continue
            
            if 'exclude_patterns' in type_info:
                if any(re.search(p, text) for p in type_info['exclude_patterns']):
                    continue
            
            if any(re.search(p, text) for p in type_info['patterns']):
                return type_name, type_info
        
        return 'Other Government Document', cls.TYPES['Other Government Document']


# =============================================================================
# PDF CONTENT EXTRACTION
# =============================================================================

def download_pdf(url: str, timeout: int = 30) -> Optional[bytes]:
    """Download PDF content from URL."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, timeout=timeout, headers=headers, stream=True)
        
        if response.status_code == 200:
            content_type = response.headers.get('content-type', '').lower()
            if 'pdf' in content_type or 'octet-stream' in content_type or url.lower().endswith('.pdf'):
                return response.content
        return None
    except Exception as e:
        return None


def extract_pdf_text(pdf_bytes: bytes, max_pages: int = 10) -> str:
    """Extract text from PDF using available library."""
    text = ""
    
    # Try PyMuPDF first (faster)
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text_parts = []
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            text_parts.append(page.get_text())
        doc.close()
        text = "\n".join(text_parts)
        if text.strip():
            return text
    except:
        pass
    
    # Fall back to pdfplumber
    try:
        import pdfplumber
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name
        
        text_parts = []
        with pdfplumber.open(tmp_path) as pdf:
            for i, page in enumerate(pdf.pages[:max_pages]):
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        
        os.unlink(tmp_path)
        text = "\n".join(text_parts)
    except:
        pass
    
    return text


# =============================================================================
# DOCUMENT CONTENT ANALYSIS
# =============================================================================

class ContentAnalyzer:
    """Analyze document content and generate summaries."""
    
    # Price patterns
    PRICE_PATTERNS = [
        (r'(?:total|contract|agreement)\s*(?:amount|value|price|cost)[:\s]*\$?([\d,]+(?:\.\d{2})?)', 'Total Value'),
        (r'not[- ]to[- ]exceed[:\s]*\$?([\d,]+(?:\.\d{2})?)', 'Not to Exceed'),
        (r'(?:annual|yearly)\s*(?:fee|cost|subscription|amount)[:\s]*\$?([\d,]+(?:\.\d{2})?)', 'Annual Fee'),
        (r'(?:monthly)\s*(?:fee|cost|subscription)[:\s]*\$?([\d,]+(?:\.\d{2})?)', 'Monthly Fee'),
        (r'(?:one[- ]time|implementation|setup)\s*(?:fee|cost)[:\s]*\$?([\d,]+(?:\.\d{2})?)', 'One-time Fee'),
        (r'(?:license|licensing)\s*(?:fee|cost)[:\s]*\$?([\d,]+(?:\.\d{2})?)', 'License Fee'),
        (r'(?:maintenance|support)\s*(?:fee|cost)[:\s]*\$?([\d,]+(?:\.\d{2})?)', 'Maintenance Fee'),
        (r'(?:professional services|consulting|implementation services)[:\s]*\$?([\d,]+(?:\.\d{2})?)', 'Services Fee'),
        (r'\$([\d,]+(?:\.\d{2})?)\s*(?:per year|annually|/year)', 'Per Year'),
        (r'\$([\d,]+(?:\.\d{2})?)\s*(?:per month|monthly|/month)', 'Per Month'),
    ]
    
    # Contract date patterns
    DATE_PATTERNS = [
        (r'(?:effective|start|commencement)\s*date[:\s]*([A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{4})', 'Effective Date'),
        (r'(?:end|expiration|termination|expiry)\s*date[:\s]*([A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{4})', 'End Date'),
        (r'(?:expire|expires|expiring|terminates?)\s*(?:on\s+)?([A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{4})', 'Expiration'),
        (r'(?:through|until|ending)\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{4})', 'Valid Through'),
    ]
    
    # Contract term patterns
    TERM_PATTERNS = [
        (r'(?:initial\s+)?term\s+(?:of\s+)?(\d+)\s*(?:year|yr)s?', 'Term'),
        (r'(\d+)[- ]year\s+(?:term|agreement|contract)', 'Term'),
        (r'(?:initial\s+)?term\s+(?:of\s+)?(\d+)\s*months?', 'Term (months)'),
    ]
    
    # Pricing model indicators
    PRICING_MODEL_KEYWORDS = {
        'subscription': ['subscription', 'saas', 'annual fee', 'recurring'],
        'perpetual': ['perpetual license', 'one-time license', 'permanent license'],
        'per_user': ['per user', 'per seat', 'named user', 'concurrent user'],
        'tiered': ['tiered pricing', 'volume discount', 'tier 1', 'tier 2'],
        'population_based': ['population-based', 'per capita', 'based on population'],
    }
    
    # What's included indicators
    INCLUDED_KEYWORDS = {
        'Software License': ['software license', 'license grant', 'right to use'],
        'Maintenance/Support': ['maintenance', 'support services', 'technical support', 'help desk'],
        'Implementation': ['implementation', 'configuration', 'setup', 'installation'],
        'Training': ['training', 'user training', 'admin training'],
        'Hosting': ['hosting', 'cloud hosting', 'saas', 'data center'],
        'Data Migration': ['data migration', 'data conversion', 'import'],
        'Customization': ['customization', 'custom development', 'modifications'],
        'Integrations': ['integration', 'api', 'interface', 'third-party'],
    }
    
    @classmethod
    def analyze(cls, text: str, company: str, product: str) -> Dict:
        """
        Analyze document text and return structured findings.
        """
        text_lower = text.lower()
        
        results = {
            'prices_found': [],
            'dates_found': [],
            'term': None,
            'pricing_model': [],
            'included_items': [],
            'has_company': company.lower() in text_lower,
            'has_product': product.lower() in text_lower,
            'summary': '',
            'key_findings': [],
        }
        
        # === EXTRACT PRICES ===
        for pattern, label in cls.PRICE_PATTERNS:
            matches = re.finditer(pattern, text_lower)
            for match in matches:
                try:
                    amount_str = match.group(1).replace(',', '')
                    amount = float(amount_str)
                    if amount >= 100:  # Filter tiny amounts
                        results['prices_found'].append({
                            'amount': amount,
                            'formatted': f"${amount:,.0f}",
                            'type': label
                        })
                except:
                    pass
        
        # Deduplicate prices
        seen_amounts = set()
        unique_prices = []
        for p in sorted(results['prices_found'], key=lambda x: x['amount'], reverse=True):
            if p['amount'] not in seen_amounts:
                seen_amounts.add(p['amount'])
                unique_prices.append(p)
        results['prices_found'] = unique_prices[:8]
        
        # === EXTRACT DATES ===
        for pattern, label in cls.DATE_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                results['dates_found'].append({
                    'date': match.group(1),
                    'type': label
                })
        
        # === EXTRACT TERM ===
        for pattern, label in cls.TERM_PATTERNS:
            match = re.search(pattern, text_lower)
            if match:
                term_value = int(match.group(1))
                if 'month' in label:
                    results['term'] = f"{term_value} months"
                else:
                    results['term'] = f"{term_value} year{'s' if term_value > 1 else ''}"
                break
        
        # === DETECT PRICING MODEL ===
        for model, keywords in cls.PRICING_MODEL_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                results['pricing_model'].append(model)
        
        # === DETECT WHAT'S INCLUDED ===
        for item, keywords in cls.INCLUDED_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                results['included_items'].append(item)
        
        # === BUILD KEY FINDINGS ===
        if results['prices_found']:
            top_price = results['prices_found'][0]
            results['key_findings'].append(f"💰 {top_price['type']}: {top_price['formatted']}")
        
        if results['term']:
            results['key_findings'].append(f"📅 {results['term']} term")
        
        if results['dates_found']:
            for d in results['dates_found'][:2]:
                results['key_findings'].append(f"📅 {d['type']}: {d['date']}")
        
        if results['pricing_model']:
            models = ', '.join(results['pricing_model'][:2])
            results['key_findings'].append(f"💳 Pricing: {models}")
        
        if results['included_items']:
            items = ', '.join(results['included_items'][:4])
            results['key_findings'].append(f"📦 Includes: {items}")
        
        # === BUILD SUMMARY ===
        summary_parts = []
        
        # Product/Company mention (IMPORTANT)
        if results['has_company'] and results['has_product']:
            summary_parts.append(f"✓ MENTIONS: {company} + {product}")
        elif results['has_company']:
            summary_parts.append(f"✓ MENTIONS: {company} only (no {product})")
        elif results['has_product']:
            summary_parts.append(f"✓ MENTIONS: {product} only (no {company})")
        else:
            summary_parts.append(f"⚠ WARNING: Neither {company} nor {product} mentioned")
        
        if results['prices_found']:
            prices_str = '; '.join([f"{p['type']}: {p['formatted']}" for p in results['prices_found'][:3]])
            summary_parts.append(f"PRICING: {prices_str}")
        else:
            summary_parts.append("PRICING: Not found in document")
        
        if results['term']:
            summary_parts.append(f"TERM: {results['term']}")
        
        if results['dates_found']:
            dates_str = '; '.join([f"{d['type']}: {d['date']}" for d in results['dates_found'][:2]])
            summary_parts.append(f"DATES: {dates_str}")
        
        if results['pricing_model']:
            summary_parts.append(f"MODEL: {', '.join(results['pricing_model'])}")
        
        if results['included_items']:
            summary_parts.append(f"INCLUDES: {', '.join(results['included_items'][:5])}")
        
        results['summary'] = ' | '.join(summary_parts)
        
        return results


def analyze_pdf_document(url: str, company: str, product: str, timeout: int = 30) -> Dict:
    """
    Download and analyze a PDF document.
    Returns analysis results or error status.
    If direct download fails, tries browser-based download (Chrome with VPN).
    """
    result = {
        'status': 'unknown',
        'analysis': None,
        'error': None,
        'download_method': None,
    }
    
    # Try direct download first
    pdf_bytes = download_pdf(url, timeout)
    
    if pdf_bytes:
        result['download_method'] = 'direct'
    else:
        # Try browser-based download (uses Chrome which may have VPN)
        pdf_bytes = download_pdf_via_browser(url, timeout)
        if pdf_bytes:
            result['download_method'] = 'browser'
    
    if not pdf_bytes:
        result['status'] = 'download_failed'
        result['error'] = 'Could not download PDF (tried direct + browser)'
        return result
    
    # Extract text
    text = extract_pdf_text(pdf_bytes, max_pages=15)
    if not text.strip():
        result['status'] = 'no_text'
        result['error'] = 'Could not extract text (may be scanned/image PDF)'
        return result
    
    # Analyze content
    analysis = ContentAnalyzer.analyze(text, company, product)
    result['status'] = 'analyzed'
    result['analysis'] = analysis
    result['text_length'] = len(text)
    
    return result


def download_pdf_via_browser(url: str, timeout: int = 30) -> Optional[bytes]:
    """
    Download PDF using Playwright browser (for sites that block direct requests).
    Uses Chrome channel which may have VPN extensions enabled.
    """
    try:
        with sync_playwright() as p:
            # Try to use Chrome (may have VPN) instead of Chromium
            try:
                browser = p.chromium.launch(headless=True, channel="chrome")
            except:
                # Fall back to Chromium if Chrome not available
                browser = p.chromium.launch(headless=True)
            
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = context.new_page()
            
            # Navigate to PDF
            response = page.goto(url, timeout=timeout * 1000, wait_until='networkidle')
            
            if response and response.status == 200:
                content = response.body()
                browser.close()
                return content
            
            browser.close()
            return None
    except Exception as e:
        return None


def batch_analyze_documents(results: List[Dict], company: str, product: str,
                           max_to_analyze: int = 15) -> List[Dict]:
    """
    Analyze PDF documents and add analysis to results.
    After analysis, re-scores results based on content findings.
    """
    has_pdfplumber, has_pymupdf = check_pdf_libraries()
    
    if not has_pdfplumber and not has_pymupdf:
        print("\n  ⚠️ No PDF library available!")
        print("  Install with: pip install pdfplumber")
        print("  Or: pip install pymupdf")
        return results
    
    # Filter to PDFs only
    pdf_results = [r for r in results if '.pdf' in r['url'].lower()][:max_to_analyze]
    
    if not pdf_results:
        print("\n  No PDFs found to analyze")
        return results
    
    print(f"\n  Analyzing {len(pdf_results)} PDFs for pricing and contract details...")
    print(f"  (This may take 1-2 minutes)\n")
    
    for i, result in enumerate(pdf_results, 1):
        title_short = result.get('title', '')[:45]
        print(f"  [{i}/{len(pdf_results)}] {title_short}...")
        
        try:
            analysis_result = analyze_pdf_document(result['url'], company, product)
            result['content_analysis'] = analysis_result
            
            if analysis_result['status'] == 'analyzed':
                analysis = analysis_result['analysis']
                
                # Show download method if browser was used
                if analysis_result.get('download_method') == 'browser':
                    print(f"           🌐 Downloaded via browser (VPN)")
                
                # Show key findings
                if analysis['prices_found']:
                    top_price = analysis['prices_found'][0]
                    print(f"           💰 {top_price['type']}: {top_price['formatted']}")
                else:
                    print(f"           📄 No pricing found")
                
                if analysis['term']:
                    print(f"           📅 Term: {analysis['term']}")
                
                # Show product/company mention
                if analysis.get('has_product') and analysis.get('has_company'):
                    print(f"           ✓ Mentions {company} + {product}")
                elif analysis.get('has_company'):
                    print(f"           ⚠️ Mentions {company} only (no {product})")
                elif analysis.get('has_product'):
                    print(f"           ⚠️ Mentions {product} only (no {company})")
                else:
                    print(f"           ❌ Neither {company} nor {product} mentioned")
                    
            elif analysis_result['status'] == 'download_failed':
                print(f"           ❌ Download failed (direct + browser)")
            elif analysis_result['status'] == 'no_text':
                print(f"           ⚠️ Could not extract text (scanned PDF?)")
                
        except Exception as e:
            print(f"           ❌ Error: {str(e)[:40]}")
            result['content_analysis'] = {
                'status': 'error',
                'error': str(e)
            }
        
        time.sleep(0.5)  # Be nice to servers
    
    # Summary
    analyzed = sum(1 for r in results if r.get('content_analysis', {}).get('status') == 'analyzed')
    with_pricing = sum(1 for r in results 
                      if r.get('content_analysis', {}).get('status') == 'analyzed'
                      and r.get('content_analysis', {}).get('analysis')
                      and r['content_analysis']['analysis'].get('prices_found'))
    
    print(f"\n  ✓ Analyzed {analyzed} documents, {with_pricing} contain pricing information")
    
    # === RE-SCORE BASED ON CONTENT ANALYSIS ===
    print("\n  Re-scoring based on content analysis...")
    results = rescore_after_analysis(results, company, product)
    
    return results


def rescore_after_analysis(results: List[Dict], company: str, product: str) -> List[Dict]:
    """
    Re-score results based on PDF content analysis findings.
    Adjusts scores based on:
    - Whether pricing was found (+2.0)
    - Whether product is mentioned (+1.5) or not mentioned (-2.0)
    - Whether company is mentioned (+1.0) or not mentioned (-1.5)
    - Contract term found (+0.5)
    """
    for result in results:
        ca = result.get('content_analysis', {})
        
        if ca.get('status') != 'analyzed':
            continue
        
        analysis = ca.get('analysis')
        if not analysis:
            continue
        
        score_adjustments = []
        original_score = result.get('relevance_score', 0)
        
        # Pricing found bonus
        if analysis.get('prices_found'):
            result['relevance_score'] += 2.0
            score_adjustments.append("+2.0 pricing found")
        
        # Product mention check
        if analysis.get('has_product'):
            result['relevance_score'] += 1.5
            score_adjustments.append(f"+1.5 {product} mentioned")
        else:
            result['relevance_score'] -= 2.0
            score_adjustments.append(f"-2.0 {product} NOT mentioned")
        
        # Company mention check
        if analysis.get('has_company'):
            result['relevance_score'] += 1.0
            score_adjustments.append(f"+1.0 {company} mentioned")
        else:
            result['relevance_score'] -= 1.5
            score_adjustments.append(f"-1.5 {company} NOT mentioned")
        
        # Term found bonus
        if analysis.get('term'):
            result['relevance_score'] += 0.5
            score_adjustments.append("+0.5 term found")
        
        # Ensure score doesn't go negative
        result['relevance_score'] = max(result['relevance_score'], 0)
        
        # Add adjustments to breakdown
        if score_adjustments:
            if 'score_breakdown' not in result:
                result['score_breakdown'] = []
            result['score_breakdown'].extend(score_adjustments)
            result['content_score_adjustment'] = result['relevance_score'] - original_score
    
    # Re-sort by new scores
    results.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
    
    # Show score changes
    changes = [(r['title'][:30], r.get('content_score_adjustment', 0)) 
               for r in results if r.get('content_score_adjustment', 0) != 0]
    
    if changes:
        print(f"  Score adjustments made to {len(changes)} results")
        for title, adj in changes[:5]:
            sign = "+" if adj > 0 else ""
            print(f"    {sign}{adj:.1f}: {title}...")
    
    return results


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
    """Extract location from URL and title."""
    url_lower = url.lower()
    title_lower = title.lower()
    text = url_lower + " " + title_lower
    
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
    
    # Pattern: city-state in domain
    domain_match = re.search(r'([a-z]+)-([a-z]+)\.(?:civicweb|legistar)', domain)
    if domain_match:
        potential_city = domain_match.group(1)
        potential_state = domain_match.group(2)
        if potential_state in states:
            found_city = potential_city.title()
            found_state_abbrev = states[potential_state]
    
    # Pattern: citySTATE.gov
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
    
    # Pattern: state-level sites
    if not found_city and not found_county:
        domain_match = re.search(r'(?:state|das|dgs|doa)\.([a-z]{2})\.(?:us|gov)', domain)
        if domain_match:
            found_state_abbrev = domain_match.group(1).upper()
            is_state_level = True
    
    # Known cities
    known_cities = {
        'anaheim': 'CA', 'berkeley': 'CA', 'san diego': 'CA', 'san francisco': 'CA',
        'los angeles': 'CA', 'oakland': 'CA', 'sacramento': 'CA', 'fresno': 'CA',
        'galveston': 'TX', 'houston': 'TX', 'dallas': 'TX', 'austin': 'TX',
        'tacoma': 'WA', 'seattle': 'WA', 'spokane': 'WA', 'bellevue': 'WA',
        'hillsboro': 'OR', 'portland': 'OR', 'salem': 'OR', 'eugene': 'OR',
        'denver': 'CO', 'phoenix': 'AZ', 'goodyear': 'AZ',
        'charlotte': 'NC', 'tampa': 'FL', 'miami': 'FL', 'brevard': 'FL',
        'papillion': 'NE', 'omaha': 'NE', 'andover': 'KS',
        'moreno valley': 'CA', 'moval': 'CA', 'merced': 'CA',
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
    
    # Build location string
    if is_state_level and found_state_abbrev:
        return f"State of {abbrev_to_full.get(found_state_abbrev, found_state_abbrev)}"
    
    if found_county and found_state_abbrev:
        return f"{found_county} County, {found_state_abbrev}"
    
    if found_city and found_state_abbrev:
        return f"{found_city}, {found_state_abbrev}"
    
    if found_city:
        return found_city
    
    if found_state_abbrev:
        return found_state_abbrev
    
    return "Unknown"


# =============================================================================
# SEARCH QUERY GENERATION
# =============================================================================

def generate_search_queries(company: str, product: str, search_type: str = 'software') -> List[Dict]:
    """Generate search queries."""
    queries = []
    
    # Order forms (best for pricing)
    order_form_queries = [
        (f'"{company}" order form pdf', 'order_form'),
        (f'"{company}" renewal order form pdf', 'order_form'),
        (f'"{company}" subscription services agreement pdf', 'agreement'),
        (f'"{company}" master services agreement pdf', 'agreement'),
    ]
    
    # Contract queries
    contract_queries = [
        (f'"{company}" "{product}" contract pdf', 'contract'),
        (f'"{company}" "{product}" agreement pdf', 'agreement'),
        (f'"{company}" "{product}" city contract pdf', 'contract'),
        (f'"{company}" "{product}" county contract pdf', 'contract'),
        (f'"{company}" contract renewal pdf', 'renewal'),
    ]
    
    # Pricing queries
    pricing_queries = [
        (f'"{company}" "{product}" pricing schedule pdf', 'pricing'),
        (f'"{company}" "{product}" fee schedule pdf', 'pricing'),
        (f'"{company}" "{product}" cost proposal pdf', 'pricing'),
    ]
    
    # Software license queries
    software_queries = [
        (f'"{company}" "{product}" software license agreement pdf', 'software_license'),
        (f'"{company}" "{product}" SaaS agreement pdf', 'software_license'),
    ]
    
    # Gov document queries
    gov_queries = [
        (f'"{company}" "{product}" staff report pdf', 'staff_report'),
        (f'"{company}" civicweb contract', 'civicweb'),
        (f'site:civicweb.net "{company}" contract', 'civicweb'),
    ]
    
    for q, cat in order_form_queries:
        queries.append({'query': q, 'category': cat, 'priority': 'highest'})
    
    for q, cat in pricing_queries:
        queries.append({'query': q, 'category': cat, 'priority': 'high'})
    
    for q, cat in contract_queries:
        queries.append({'query': q, 'category': cat, 'priority': 'high'})
    
    if search_type in ['software', 'both']:
        for q, cat in software_queries:
            queries.append({'query': q, 'category': cat, 'priority': 'high'})
    
    for q, cat in gov_queries:
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

def filter_result(result: Dict, company: str, product: str) -> Tuple[bool, str]:
    """Determine if result should be included."""
    url = result['url']
    title = result.get('title', '')
    text = (title + " " + url).lower()
    
    is_blocked, blocked_domain = is_blocked_domain(url)
    if is_blocked:
        return False, f"Blocked domain: {blocked_domain}"
    
    if is_user_documentation(url, title):
        return False, "User documentation"
    
    company_lower = company.lower()
    product_lower = product.lower()
    has_company = company_lower in text
    has_product = product_lower in text
    
    if is_pdf_url(url) and is_trusted_domain(url):
        return True, "PDF from trusted domain"
    
    if is_pdf_url(url) and has_good_url_pattern(url):
        return True, "PDF from document repository"
    
    if not has_company and not has_product:
        return False, "No company/product match"
    
    contract_keywords = [
        'contract', 'agreement', 'procurement', 'purchasing', 'rfp', 'bid',
        'proposal', 'memo', 'resolution', 'agenda', 'ordinance', 'staff report',
        'award', 'amendment', 'renewal', 'pricing', 'order form', 'fee schedule',
    ]
    
    if any(kw in text for kw in contract_keywords):
        return True, "Has contract keyword"
    
    if is_trusted_domain(url) and (has_company or has_product):
        return True, "Trusted domain with match"
    
    if has_good_url_pattern(url):
        return True, "Document repository pattern"
    
    return False, "No contract signals"


def score_result(result: Dict, company: str, product: str) -> Tuple[float, List[str]]:
    """Score result on 0-10 scale."""
    score = 0.0
    reasons = []
    url = result['url'].lower()
    title = result.get('title', '').lower()
    text = title + " " + url
    
    company_lower = company.lower()
    product_lower = product.lower()
    
    # Entity matching (max 3.0)
    if company_lower in text:
        score += 1.5
        reasons.append("+1.5 company")
    if product_lower in text:
        score += 1.5
        reasons.append("+1.5 product")
    
    # Document format (max 1.5)
    if is_pdf_url(url):
        score += 1.5
        reasons.append("+1.5 PDF")
    
    # Domain trust (max 2.5)
    if '.gov' in url:
        score += 2.5
        reasons.append("+2.5 .gov")
    elif is_trusted_domain(url):
        score += 2.0
        reasons.append("+2.0 trusted")
    elif has_good_url_pattern(url):
        score += 1.0
        reasons.append("+1.0 doc repo")
    
    # Document type bonus (max 2.5)
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
    
    # High-value title patterns (max 2.5)
    title_bonus = 0.0
    for pattern, bonus in SearchConfig.HIGH_VALUE_TITLE_PATTERNS.items():
        if pattern in title:
            title_bonus = max(title_bonus, bonus)
    
    if title_bonus > 0:
        score += title_bonus
        reasons.append(f"+{title_bonus} title pattern")
    
    # Recency (max 1.0)
    years = re.findall(r'20(2[0-6]|1[9])', text)
    if years:
        latest = max(int('20' + y) for y in years)
        if latest >= 2024:
            score += 1.0
            reasons.append(f"+1.0 {latest}")
        elif latest >= 2022:
            score += 0.5
            reasons.append(f"+0.5 {latest}")
    
    # Penalties
    if is_user_documentation(url, title):
        score -= 3.0
        reasons.append("-3.0 user doc")
    
    if re.search(r'(login|signin|welcome|default)\.aspx?$', url):
        score -= 2.0
        reasons.append("-2.0 login page")
    
    result['document_type'] = doc_type
    result['pricing_likely'] = doc_info['pricing_likely']
    
    return max(round(score, 1), 0.0), reasons


def process_results(results: List[Dict], company: str, product: str) -> List[Dict]:
    """Filter and score all results."""
    processed = []
    
    for result in results:
        include, reason = filter_result(result, company, product)
        
        if include:
            score, score_reasons = score_result(result, company, product)
            result['relevance_score'] = score
            result['score_breakdown'] = score_reasons
            result['include_reason'] = reason
            processed.append(result)
    
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
    """Apply diversity penalty to results from same location."""
    location_counts = {}
    
    for result in results:
        location = extract_location(result['url'], result.get('title', ''))
        result['location'] = location
        
        if location == 'Unknown':
            continue
        
        count = location_counts.get(location, 0)
        if count > 0:
            penalty = min(count * penalty_per_duplicate, max_penalty)
            result['relevance_score'] = max(result['relevance_score'] - penalty, 0)
            result['score_breakdown'].append(f"-{penalty:.1f} location #{count+1}")
            result['diversity_penalty'] = penalty
        
        location_counts[location] = count + 1
    
    results.sort(key=lambda x: x['relevance_score'], reverse=True)
    return results


# =============================================================================
# OUTPUT & REPORTING
# =============================================================================

def display_results(results: List[Dict], company: str, product: str, show_breakdown: bool = False):
    """Display formatted results."""
    print(f"\n{'=' * 80}")
    print("TOP RESULTS")
    print('=' * 80)
    
    for i, r in enumerate(results[:20], 1):
        score = r.get('relevance_score', 0)
        valid = r.get('link_valid')
        
        if valid is True:
            status = "✓"
        elif valid is False:
            status = "✗"
        else:
            status = "?"
        
        bar = '█' * int(score) + '░' * (10 - int(score))
        cat = "HIGH" if score >= 7 else ("MED " if score >= 5 else "LOW ")
        
        location = r.get('location', 'Unknown')
        doc_type = r.get('document_type', 'Unknown')
        pricing_likely = r.get('pricing_likely', False)
        pricing_indicator = "💰" if pricing_likely else "📄"
        
        print(f"\n{i:2}. [{bar}] {score:4.1f}/10 {cat} {status}")
        print(f"    📍 {location}")
        print(f"    {pricing_indicator} [{doc_type}] {r['title'][:65]}")
        print(f"    {r['url'][:100]}")
        
        # Show content analysis if available
        if r.get('content_analysis', {}).get('status') == 'analyzed':
            analysis = r['content_analysis']['analysis']
            if analysis.get('key_findings'):
                for finding in analysis['key_findings'][:3]:
                    print(f"       {finding}")
        
        if show_breakdown and r.get('score_breakdown'):
            print(f"    Scoring: {'; '.join(r['score_breakdown'][:5])}")
        
        if valid is False:
            print(f"    ⚠ Link issue: {r.get('link_status_reason', 'Unknown')}")


def save_results(results: List[Dict], company: str, product: str, filename: str):
    """Save results to JSON and CSV."""
    
    # JSON output
    output = {
        'metadata': {
            'company': company,
            'product': product,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_results': len(results),
            'version': '7.5',
        },
        'results': []
    }
    
    for i, r in enumerate(results):
        result_data = {
            'rank': i + 1,
            'score': r['relevance_score'],
            'title': r['title'],
            'url': r['url'],
            'location': r.get('location', 'Unknown'),
            'document_type': r.get('document_type', 'Unknown'),
            'pricing_likely': r.get('pricing_likely', False),
            'link_valid': r.get('link_valid'),
            'score_breakdown': r.get('score_breakdown', []),
        }
        
        # Add content analysis if available
        if r.get('content_analysis', {}).get('status') == 'analyzed':
            analysis = r['content_analysis']['analysis']
            result_data['content_summary'] = analysis.get('summary', '')
            result_data['prices_found'] = analysis.get('prices_found', [])
            result_data['contract_term'] = analysis.get('term')
            result_data['key_findings'] = analysis.get('key_findings', [])
        
        output['results'].append(result_data)
    
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"✓ Saved JSON to {filename}")
    
    # CSV output
    csv_filename = filename.replace('.json', '.csv')
    
    with open(csv_filename, 'w', encoding='utf-8') as f:
        f.write('Rank,Score,Category,Link,Location,Document_Type,Pricing_Likely,Content_Summary,Title,URL\n')
        
        for i, r in enumerate(results, 1):
            score = r.get('relevance_score', 0)
            category = "HIGH" if score >= 7 else ("MEDIUM" if score >= 5 else "LOW")
            
            valid = r.get('link_valid')
            link_status = "✓" if valid is True else ("✗" if valid is False else "?")
            
            location = r.get('location', 'Unknown')
            doc_type = r.get('document_type', 'Unknown')
            pricing_likely = "Yes" if r.get('pricing_likely', False) else "No"
            
            # Get content summary
            content_summary = ""
            if r.get('content_analysis', {}).get('status') == 'analyzed':
                analysis = r['content_analysis']['analysis']
                content_summary = analysis.get('summary', '')
            elif r.get('content_analysis', {}).get('status') == 'no_text':
                content_summary = "Could not extract text (scanned PDF)"
            elif r.get('content_analysis', {}).get('status') == 'download_failed':
                content_summary = "Download failed"
            else:
                content_summary = "Not analyzed"
            
            # Escape for CSV
            content_summary = content_summary.replace('"', "'").replace(',', ';').replace('\n', ' ')
            title = r.get('title', '').replace('"', '""').replace('\n', ' ')
            url = r.get('url', '')
            
            f.write(f'{i},{score},{category},{link_status},"{location}","{doc_type}",{pricing_likely},"{content_summary}","{title}","{url}"\n')
    
    print(f"✓ Saved CSV to {csv_filename}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 80)
    print("GOVERNMENT CONTRACT SEARCH v7.5")
    print("=" * 80)
    print("\nFeatures:")
    print("  ✓ Location diversity (avoids duplicate locations at top)")
    print("  ✓ Simplified document types (6 types, focused on pricing)")
    print("  ✓ PDF content analysis with pricing extraction")
    print("  ✓ Contract term and date detection")
    print("  ✓ Document summary generation")
    
    # Check PDF libraries
    has_pdfplumber, has_pymupdf = check_pdf_libraries()
    print("\nPDF Libraries:")
    print(f"  PyMuPDF: {'✓ Available' if has_pymupdf else '✗ Not installed'}")
    print(f"  pdfplumber: {'✓ Available' if has_pdfplumber else '✗ Not installed'}")
    
    if not has_pdfplumber and not has_pymupdf:
        print("\n  ⚠️ Install a PDF library for content analysis:")
        print("     pip install pdfplumber")
        print("     pip install pymupdf")
    
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
    
    queries = generate_search_queries(company, product, search_type)
    
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
        
        processed = process_results(raw, company, product)
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
    
    # PDF content analysis
    if has_pdfplumber or has_pymupdf:
        analyze = input("\nAnalyze PDF content for pricing/dates? (y/n, default y): ").strip().lower() != 'n'
        if analyze:
            try:
                max_pdfs = int(input("Max PDFs to analyze (default 15): ").strip() or "15")
            except:
                max_pdfs = 15
            all_results = batch_analyze_documents(all_results, company, product, max_to_analyze=max_pdfs)
    
    # Display
    display_results(all_results, company, product, show_breakdown=True)
    
    # Save
    if input("\nSave results? (y/n, default y): ").strip().lower() != 'n':
        default_name = f"{company.lower().replace(' ', '_')}_contracts.json"
        filename = input(f"Filename (default: {default_name}): ").strip() or default_name
        if not filename.endswith('.json'):
            filename += '.json'
        save_results(all_results, company, product, filename)
    
    print("\n✓ Done!")


if __name__ == "__main__":
    main()
