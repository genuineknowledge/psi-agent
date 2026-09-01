---
name: canvas-mcp
description: "在共享的 Excalidraw 画布上绘图与查看 —— 架构图、流程图、思维导图、线框图。需要空间布局而非文字或代码时用它。 经 `canvas_call(tool, args_json)` 调用，本文档载有全部 26 个工具的参数表。Use when the task needs canvas capabilities beyond prose or code."
category: integration
generated_by: tools/_gen_mcp_skill.py
---

# canvas 工具参数表

这些工具由上游 MCP 服务器提供，schema 由它定义。**本文件是生成的** —— 改了缓存请重跑
`python tools/_gen_mcp_skill.py canvas`，不要手改（上游改过名，手维护必然漂移）。

回复用中文，除非用户明显在用其他语言。

## 怎么调

```
canvas_call(tool="<下表的工具名>", args_json='{"参数": 值}')
```

`args_json` 是 JSON **对象**字符串；不吃参数的工具可省略或传 `{}`。
工具名写全名（带 `canvas_` 前缀）或裸名都认。名字写错会被本地拒绝并列出可用名，不会真发请求。

## 工具参数表

### `canvas_align_elements`

Align elements to a specific position

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `elementIds` | array[string] | 是 |  |
| `alignment` | string (left | center | right | top | middle | bottom) | 是 |  |

### `canvas_batch_create_elements`

Create multiple Excalidraw elements at once. For arrows, use startElementId/endElementId to bind arrows to shapes — Excalidraw auto-routes to element edges. Assign custom id to shapes so arrows can reference them.

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `elements` | array[object] | 是 |  |

### `canvas_clear_canvas`

Clear all elements from the canvas

无参数。

### `canvas_create_element`

Create a new Excalidraw element. For arrows, use startElementId/endElementId to bind to shapes (auto-routes to edges).

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string |  | Custom element ID (optional, auto-generated if omitted). Use with startElementId/endElementId in batch_create_elements. |
| `type` | string (rectangle | ellipse | diamond | arrow | text | freedraw | line | image) | 是 |  |
| `x` | number | 是 |  |
| `y` | number | 是 |  |
| `width` | number |  |  |
| `height` | number |  |  |
| `backgroundColor` | string |  |  |
| `strokeColor` | string |  |  |
| `strokeWidth` | number |  |  |
| `strokeStyle` | string |  | Stroke style: solid, dashed, dotted |
| `roughness` | number |  |  |
| `opacity` | number |  |  |
| `text` | string |  |  |
| `fontSize` | number |  |  |
| `fontFamily` | string/number |  | Font family: virgil/hand/handwritten (1), helvetica/sans/sans-serif (2), cascadia/mono/monospace (3), excalifont (5), nunito (6), lilita/lilita one (7), comic shanns/comic (8), or numeric ID |
| `startElementId` | string |  | For arrows: ID of the element to bind the arrow start to. Arrow auto-routes to element edge. |
| `endElementId` | string |  | For arrows: ID of the element to bind the arrow end to. Arrow auto-routes to element edge. |
| `endArrowhead` | string |  | Arrowhead style at end: arrow, bar, dot, triangle, or null |
| `startArrowhead` | string |  | Arrowhead style at start: arrow, bar, dot, triangle, or null |

### `canvas_create_from_mermaid`

Convert a Mermaid diagram to Excalidraw elements and render them on the canvas

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `mermaidDiagram` | string | 是 | The Mermaid diagram definition (e.g., "graph TD; A-->B; B-->C;") |
| `config` | object |  | Optional Mermaid configuration |

### `canvas_delete_element`

Delete an Excalidraw element

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 |  |

### `canvas_describe_scene`

Get an AI-readable description of the current canvas: element types, positions, connections, labels, spatial layout, and bounding box. Use this to understand what is on the canvas before making changes.

无参数。

### `canvas_distribute_elements`

Distribute elements evenly

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `elementIds` | array[string] | 是 |  |
| `direction` | string (horizontal | vertical) | 是 |  |

### `canvas_duplicate_elements`

Duplicate elements with a configurable offset

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `elementIds` | array[string] | 是 | IDs of elements to duplicate |
| `offsetX` | number |  | Horizontal offset (default: 20) |
| `offsetY` | number |  | Vertical offset (default: 20) |

### `canvas_export_scene`

Export the current canvas to .excalidraw JSON format. Optionally write to a file.

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `filePath` | string |  | Optional file path to write the .excalidraw JSON file |

### `canvas_export_to_excalidraw_url`

Export the current canvas to a shareable excalidraw.com URL. The diagram is encrypted and uploaded; anyone with the URL can view it. Returns the shareable link.

无参数。

### `canvas_export_to_image`

Export the current canvas to PNG or SVG image. Requires the canvas frontend to be open in a browser.

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `format` | string (png | svg) | 是 | Image format |
| `filePath` | string |  | Optional file path to save the image |
| `background` | boolean |  | Include background in export (default: true) |

### `canvas_get_canvas_screenshot`

Take a screenshot of the current canvas and return it as an image. Requires the canvas frontend to be open in a browser. Use this to visually verify what the diagram looks like.

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `background` | boolean |  | Include background in screenshot (default: true) |

### `canvas_get_element`

Get a single Excalidraw element by ID

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | The element ID |

### `canvas_get_resource`

Get an Excalidraw resource

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `resource` | string (scene | library | theme | elements) | 是 |  |

### `canvas_group_elements`

Group multiple elements together

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `elementIds` | array[string] | 是 |  |

### `canvas_import_scene`

Import elements from a .excalidraw JSON file or raw JSON data

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `filePath` | string |  | Path to a .excalidraw JSON file |
| `data` | string |  | Raw .excalidraw JSON string (alternative to filePath) |
| `mode` | string (replace | merge) | 是 | "replace" clears canvas first, "merge" appends to existing elements |

### `canvas_lock_elements`

Lock elements to prevent modification

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `elementIds` | array[string] | 是 |  |

### `canvas_query_elements`

Query Excalidraw elements with optional filters

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `type` | string (rectangle | ellipse | diamond | arrow | text | freedraw | line | image) |  |  |
| `filter` | object |  |  |
| `bbox` | object |  | Bounding box filter — only return elements whose origin (x, y) falls within the given coordinate range |

### `canvas_read_diagram_guide`

Returns a comprehensive design guide for creating beautiful Excalidraw diagrams: color palette, sizing rules, layout patterns, arrow binding best practices, diagram templates, and anti-patterns. Call this before creating diagrams to produce professional results.

无参数。

### `canvas_restore_snapshot`

Restore the canvas from a previously saved named snapshot

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | Name of the snapshot to restore |

### `canvas_set_viewport`

Control the canvas viewport (camera). Auto-fit all elements, center on a specific element, or set zoom/scroll directly. Requires the canvas frontend open in a browser.

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `scrollToContent` | boolean |  | Auto-fit all elements in view (zoom-to-fit) |
| `scrollToElementId` | string |  | Center the view on a specific element by ID |
| `zoom` | number |  | Zoom level (0.1–10, where 1 = 100%) |
| `offsetX` | number |  | Horizontal scroll offset |
| `offsetY` | number |  | Vertical scroll offset |

### `canvas_snapshot_scene`

Save a named snapshot of the current canvas state for later restoration

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | Name for this snapshot |

### `canvas_ungroup_elements`

Ungroup a group of elements

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `groupId` | string | 是 |  |

### `canvas_unlock_elements`

Unlock elements to allow modification

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `elementIds` | array[string] | 是 |  |

### `canvas_update_element`

Update an existing Excalidraw element

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 |  |
| `type` | string (rectangle | ellipse | diamond | arrow | text | freedraw | line | image) |  |  |
| `x` | number |  |  |
| `y` | number |  |  |
| `width` | number |  |  |
| `height` | number |  |  |
| `backgroundColor` | string |  |  |
| `strokeColor` | string |  |  |
| `strokeWidth` | number |  |  |
| `strokeStyle` | string |  |  |
| `roughness` | number |  |  |
| `opacity` | number |  |  |
| `text` | string |  |  |
| `fontSize` | number |  |  |
| `fontFamily` | string/number |  | Font family: virgil/hand/handwritten (1), helvetica/sans/sans-serif (2), cascadia/mono/monospace (3), excalifont (5), nunito (6), lilita/lilita one (7), comic shanns/comic (8), or numeric ID |
