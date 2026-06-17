---
name: nano-pdf
description: Read and extract text content from PDF files using the pdf tool.
---

Use the `pdf` tool to extract readable text from PDF files.

**Basic usage:**
```
pdf(file_path="report.pdf")
```

**Read specific pages:**
```
pdf(file_path="report.pdf", pages="1-5")
pdf(file_path="report.pdf", pages="3")
```

**Workflow for long PDFs:**
1. Read the first few pages to understand structure: `pdf(file_path="...", pages="1-3")`
2. Identify relevant sections from headers/TOC.
3. Read targeted page ranges for deep content.

**If pdf tool is unavailable**, fall back to bash:
```bash
# Using pdftotext (poppler-utils)
pdftotext report.pdf -
pdftotext report.pdf - | grep -A5 "keyword"

# Check if installed
which pdftotext || echo "install: apt install poppler-utils"
```

**Tips:**
- PDF text extraction may lose formatting; tables may appear garbled.
- Scanned PDFs (image-based) will return empty — they need OCR.
- For structured data in PDFs, look for an accompanying CSV or API instead.
