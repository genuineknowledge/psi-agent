# Chat File Preview Design

## Goal

Add front-end-only preview support for files shown in chat messages, covering user-uploaded attachments and files returned by the model. The chat layout stays the same: previews expand inline below the existing attachment chip in each message.

## Scope

In scope:

- Chat message attachments stored as `{ name, data }` in `msg.files`.
- Files sent by users and files returned by the model.
- Existing download links remain available.
- Rendering failure falls back to a visible message telling the user the file is too large or cannot be previewed and should be downloaded.
- Large-file partial rendering is allowed where the renderer can do it reliably.

Out of scope:

- Workspace browser file preview.
- Back-end API changes.
- Security hardening.
- Browser memory optimization beyond bounded preview output for renderers that support it.
- Old binary `.ppt` rendering.

## Constraints

- Implementation must only change front-end code and front-end package metadata.
- Only one new source file may be added: `src/psi_agent/gateway/spa/src/components/FilePreview.vue`.
- Other source changes must be precise and necessary. The expected source touch point is `MessageBubble.vue`, which wires preview buttons to `FilePreview.vue`.
- No additional helper modules are created; file classification, decoding, partial-render limits, and renderer orchestration live inside `FilePreview.vue`.
- Existing layout and chat message structure stay intact.

## Dependencies

Use high-quality browser-side renderers:

- DOCX: `docx-preview`
- XLS/XLSX: `xlsx`
- PDF: `pdfjs-dist`
- Text/code/SQL/log/JSON/JSONL: `codemirror`
- CSV: `papaparse`
- PPTX: `pptx-preview`

`pptxjs` is not used because its npm package metadata is weak: the package reports version `0.0.0` and a repository that does not match a maintained PPTX preview project. `pptx-preview` is a more direct front-end PPTX preview dependency.

## UX

Each attachment row keeps the current filename download link. A compact preview toggle is added beside it:

- Closed state: attachment row shows the filename, download link behavior, and a preview button when the extension is known or worth attempting.
- Open state: a preview panel appears directly below that attachment row.
- The preview panel has fixed responsive constraints, uses the existing Material Symbols/icon styling, and does not change the overall three-column or chat layout.
- The panel always keeps a download path available through the existing attachment link.

## Rendering Behavior

`FilePreview.vue` receives a file object with `name` and base64 `data`.

Rendering pipeline:

1. Classify the file by lowercase extension and MIME-like category.
2. Convert base64 to `Blob`, `ArrayBuffer`, or decoded text as needed.
3. Try the strongest renderer for the category.
4. If a renderer throws, report: `文件过大或无法预览，请直接下载。`
5. If partial rendering is used, show: `仅显示部分内容，请下载查看完整文件。`

Supported behavior:

- Images (`png`, `jpg`, `jpeg`, `gif`, `webp`, `svg`): render through `<img>` from a Blob URL.
- Audio/video: render through native `<audio controls>` and `<video controls>`.
- Text/code/SQL/log/JSON/JSONL: decode text and render read-only CodeMirror. Large content is truncated by byte/character and line limits before CodeMirror receives it. JSON is formatted when parsing succeeds; JSONL formats each valid line independently and preserves invalid lines as text.
- CSV: parse with PapaParse and render a table from a bounded number of rows and columns.
- PDF: render with PDF.js. Partial rendering is mandatory for large PDFs by limiting rendered pages, with a notice when page count exceeds the preview limit.
- XLS/XLSX: parse with `xlsx`, then render a bounded number of sheets, rows, and columns as tables.
- DOCX: render with `docx-preview`. It attempts full rendering; reliable partial rendering is not required because the library works at document-layout level.
- PPTX: render with `pptx-preview`. It attempts PPTX rendering; if the library supports limiting slide output, render only the first slides and show a partial notice. If the library only supports full rendering, attempt full rendering and fall back on failure.
- PPT: do not attempt preview; show the fallback message and preserve download.

## Error Handling

The component handles these states:

- Loading while heavy renderers run.
- Unsupported file type.
- Partial preview notice.
- Renderer failure.
- Missing or invalid base64 data.

The visible fallback message is intentionally the same for oversized and failed renderer cases: `文件过大或无法预览，请直接下载。`

## Testing And Verification

Because the SPA currently has no front-end unit test runner, implementation verification uses:

- `npm install` or `npm ci` after dependency changes.
- `npm run build` from `src/psi_agent/gateway/spa`.
- Manual browser verification through Vite or Gateway for representative files:
  - image
  - text/code
  - JSON
  - JSONL
  - CSV
  - PDF with more pages than the preview limit
  - DOCX
  - XLSX
  - PPTX
  - unsupported PPT

If a test runner is introduced later, focused tests should cover classification and fallback decisions, but this implementation will not add a test framework as part of the minimal-change scope.

## Implementation Notes

- `MessageBubble.vue` owns the open/closed state per attachment and imports `FilePreview.vue`.
- `FilePreview.vue` should lazy-run rendering only after the user opens a preview.
- Blob URLs created for media or PDF assets should be revoked on unmount or when the file changes.
- The existing `fileUrl(f)` download behavior remains the source for direct download links.
