Government Contract Search Tool v7.5
A Python tool for discovering publicly available government contracts and pricing information for software vendors.

⚠️ Prototype Notice: This is a prototype/proof-of-concept implementation. The codebase consists of a single main file without proper architectural separation (no modules, classes split across files, or formal project structure). It is intended for demonstration and personal use rather than production deployment.


What It Does
This tool automates the process of researching how much government agencies pay for software products by:

Searching the web for publicly available contract documents
Filtering out irrelevant results (marketing sites, user manuals, etc.)
Scoring and ranking results by relevance
Extracting pricing, dates, and contract terms from PDF documents
Identifying which cities, counties, and states have contracts with a vendor


Requirements
Required Dependencies
bashpip install playwright
pip install requests
pip install pdfplumber
Optional (Faster PDF Processing)
bashpip install pymupdf
First-Time Setup
After installing Playwright, you must install the browser:
bashplaywright install chromium

Usage
Run the script directly:
bashpython gov_contract_search.py
The program will prompt you for:

Company name — The software vendor to research (e.g., "Accela", "Tyler Technologies")
Product name — The specific product to search for (e.g., "Civic Platform", "Munis")
Search type — Software licenses, implementation services, or both
Number of queries — How many search variations to run (more = broader results)


Features
FeatureDescriptionSmart FilteringAutomatically excludes vendor marketing sites, review platforms, and user documentationRelevance ScoringRanks results 0-10 based on source trustworthiness, document type, and content signalsLocation DetectionIdentifies the city, county, or state associated with each contractLocation DiversityPrevents results from being dominated by a single jurisdictionLink ValidationTests each URL to verify the document is still accessiblePDF AnalysisDownloads and extracts pricing, dates, and contract terms from documentsContent ScoringRe-ranks results based on whether actual pricing was found in the document

Output
Results are displayed in the terminal with:

Relevance score and visual bar graph
Document location (city/state)
Document type classification
Key findings (pricing amounts, contract terms, dates)
Direct URL to the document

Results can be saved to:

JSON — Structured data with full metadata
CSV — Spreadsheet-compatible format for easy review


Document Types
The tool classifies documents into six categories:
TypeDescriptionLikely to Have Pricing?Order FormSpecific pricing and quantities✅ YesContract/AgreementFull contract terms✅ YesPricing DocumentFee schedules, cost exhibits✅ YesStaff Report/MemoGovernment summary documents⚠️ SometimesRFP/ProposalSolicitations and responses⚠️ Estimated onlyOther Government DocumentUncategorized❓ Unknown

Blocked Domains
The tool automatically filters out results from:

Software vendor websites (marketing content)
Review aggregators (G2, Capterra, Gartner, etc.)
Bid platforms without direct document access
News and press release sites
Social media platforms
User manual aggregator sites


Limitations

Prototype architecture — Single file, no modular structure
Rate limiting — DuckDuckGo may temporarily block requests if too many searches are run
Scanned PDFs — Cannot extract text from image-based/scanned documents
Login-protected documents — Cannot access documents behind authentication
Regional focus — Location detection is optimized for US government entities


Example Output
 1. [████████░░]  8.0/10 HIGH ✓
    📍 San Diego, CA
    💰 [Order Form] Accela Civic Platform Renewal Order Form 2024.pdf
    https://sandiego.gov/procurement/documents/accela-order-2024.pdf
       💰 Annual Fee: $125,000
       📅 3 years term
       ✓ MENTIONS: Accela + Civic Platform

License
This project is provided as-is for research and educational purposes.

Disclaimer
This tool searches for publicly available government documents. Users are responsible for ensuring their use complies with applicable laws and terms of service. The tool does not access private, confidential, or login-protected information.
