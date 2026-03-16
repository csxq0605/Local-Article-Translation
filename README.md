# Local Document Translation Studio

## Purpose

This repository is a local browser-based document translation tool for technical papers and structured documents.

Its main use case is:

- upload `pdf`, `docx`, or `txt` files locally
- preserve the original reading structure as much as possible
- translate the full document with the DeepSeek API
- review source and translation side by side in the browser

This project is especially aimed at academic and engineering documents that contain figures, tables, formulas, multi-column PDF layouts, and captions.

## What This Repository Does

- Hosts a local web app on your machine
- Parses uploaded documents into structured blocks
- Keeps one independent translation session per document
- Calls DeepSeek for full-document translation
- Renders the source content on the left and the translated content on the right
- Lets you switch between multiple uploaded documents
- Lets you delete old translation records
- Lets you export the translated text as a local `.txt` file

## Current Features

- Multi-file upload with drag and drop
- Per-document translation context isolation
- Side-by-side source and translation view
- Independent scrolling in both panes
- Support for `pdf`, `docx`, and `txt`
- Figure extraction as standalone media blocks
- Table extraction as standalone image-like blocks
- Standalone formula extraction as image blocks
- Caption preservation for figures and tables
- Support for captions above, below, and on the left or right side of figures
- Double-column PDF reading order: left column first, then right column
- Filtering of PDF headers, footers, and marginal artifact text
- Reference section is kept in the original language instead of being translated
- Translation export to `.txt`
- Record deletion together with parsed assets and saved state

## Tech Stack

- Backend: `FastAPI`
- Frontend: plain `HTML`, `CSS`, and `JavaScript`
- DOCX parsing: `python-docx`
- PDF parsing: `PyMuPDF`
- Model API: DeepSeek Chat API

## DeepSeek Settings

Current default settings used by the app:

- `DEEPSEEK_BASE_URL=https://api.deepseek.com`
- `DEEPSEEK_MODEL=deepseek-chat`
- `DEEPSEEK_TEMPERATURE=1.3`

Environment variables are defined in [.env.example](./.env.example).

## Quick Start

1. Create the local environment file:

```bat
copy .env.example .env
```

2. Edit `.env` and set your API key:

```env
DEEPSEEK_API_KEY=your_real_deepseek_api_key
```

3. Start the app:

```bat
.\run_local.bat
```

The startup script is [run_local.bat](./run_local.bat).

4. Open the app in your browser:

```text
http://127.0.0.1:8000
```

## Basic Usage

1. Open the local web app in the browser.
2. Drag one or more documents into the upload area.
3. Select a document from the document list.
4. Set the target language.
5. Start translation.
6. Review the source structure on the left and the translated structure on the right.
7. Download the translated text if needed.
8. Delete old document records when they are no longer needed.

## Parsing Behavior

### PDF

- Figures are extracted as standalone image blocks.
- Tables are exported as standalone image blocks instead of plain text tables.
- Standalone display equations are extracted as image blocks.
- Captions can be recognized above, below, or beside figures.
- Double-column pages are read in left-column-then-right-column order.
- Header and footer noise is filtered when possible.

### DOCX

- Inline and grouped figures are extracted as media blocks.
- Multi-image figures such as `Fig. 2/3` are preserved as grouped layouts.
- Tables are rendered as standalone image-like blocks.
- Standalone equations embedded as Word equation objects are rendered as image blocks.

### TXT

- Plain text is loaded as paragraph blocks and translated directly.

## Output

- The browser view shows the parsed source structure and translated structure side by side.
- Exported `.txt` files contain translated text and translated captions.
- Images are not embedded into the exported `.txt`.

## Limitations

- Stable input support is currently `pdf`, `docx`, and `txt`.
- Legacy `.doc` is not supported in the current setup.
- PDF layout reconstruction is heuristic, not a full desktop publishing renderer.
- If parsing rules change, previously uploaded records should be deleted and uploaded again to regenerate assets.

## Typical Use Cases

- Translating English research papers into Chinese while preserving figures and tables
- Reviewing technical reports in a source-vs-translation layout
- Building local translation drafts for academic reading
- Extracting structured content from mixed-format engineering documents
