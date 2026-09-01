---
name: browser-mcp
description: "用真实浏览器做交互 —— 点击、填表、滚动、读控制台/网络、处理弹窗、截图。只是取一个页面的文字用 fetch 更快。 经 `browser_call(tool, args_json)` 调用，本文档载有全部 41 个工具的参数表。Use when the task needs browser capabilities beyond the always-loaded `browser_navigate`, `browser_snapshot`, `browser_click`, `browser_tabs`, `browser_take_screenshot`, `browser_type`."
category: integration
generated_by: tools/_gen_mcp_skill.py
---

# browser 工具参数表

这些工具由上游 MCP 服务器提供，schema 由它定义。**本文件是生成的** —— 改了缓存请重跑
`python tools/_gen_mcp_skill.py browser`，不要手改（上游改过名，手维护必然漂移）。

回复用中文，除非用户明显在用其他语言。

## 怎么调

```
browser_call(tool="<下表的工具名>", args_json='{"参数": 值}')
```

`args_json` 是 JSON **对象**字符串；不吃参数的工具可省略或传 `{}`。
名字写错会被本地拒绝并列出可用名，不会真发请求。

## 这几个是独立工具，直接调，别走 `browser_call`

- `browser_navigate`
- `browser_snapshot`
- `browser_click`
- `browser_tabs`
- `browser_take_screenshot`
- `browser_type`

## 工具参数表

### `browser_annotate`

Open the Playwright Dashboard in annotation mode for the current page and wait for the user to draw annotations. Returns the annotated screenshot, ARIA snapshot, and the list of annotations.

无参数。

### `browser_click`  ← 独立工具

Perform click on a web page

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `element` | string |  | Human-readable element description used to obtain permission to interact with the element |
| `target` | string | 是 | Exact target element reference from the page snapshot, or a unique element selector |
| `doubleClick` | boolean |  | Whether to perform a double click instead of a single click |
| `button` | string (left | right | middle) |  | Button to click, defaults to left |
| `modifiers` | array[string (Alt | Control | ControlOrMeta | Meta | Shift)] |  | Modifier keys to press |

### `browser_close`

Close the page

无参数。

### `browser_console_messages`

Returns all console messages

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `level` | string (error | warning | info | debug) | 是 | Level of the console messages to return. Each level includes the messages of more severe levels. Defaults to "info". |
| `all` | boolean |  | Return all console messages since the beginning of the session, not just since the last navigation. Defaults to false. |
| `filename` | string |  | Filename to save the console messages to. If not provided, messages are returned as text. |

### `browser_drag`

Perform drag and drop between two elements

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `startElement` | string |  | Human-readable source element description used to obtain the permission to interact with the element |
| `startTarget` | string | 是 | Exact target element reference from the page snapshot, or a unique element selector |
| `endElement` | string |  | Human-readable target element description used to obtain the permission to interact with the element |
| `endTarget` | string | 是 | Exact target element reference from the page snapshot, or a unique element selector |

### `browser_drop`

Drop files or MIME-typed data onto an element, as if dragged from outside the page. At least one of "paths" or "data" must be provided.

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `element` | string |  | Human-readable element description used to obtain permission to interact with the element |
| `target` | string | 是 | Exact target element reference from the page snapshot, or a unique element selector |
| `paths` | array[string] |  | Absolute paths to files to drop onto the element. |
| `data` | object |  | Data to drop, as a map of MIME type to string value (e.g. {"text/plain": "hello", "text/uri-list": "https://example.com"}). |

### `browser_evaluate`

Evaluate JavaScript expression on page or element

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `element` | string |  | Human-readable element description used to obtain permission to interact with the element |
| `target` | string |  | Exact target element reference from the page snapshot, or a unique element selector |
| `function` | string | 是 | () => { /* code */ } or (element) => { /* code */ } when element is provided |
| `filename` | string |  | Filename to save the result to. If not provided, result is returned as text. |

### `browser_file_upload`

Upload one or multiple files

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `paths` | array[string] |  | The absolute paths to the files to upload. Can be single file or multiple files. If omitted, file chooser is cancelled. |

### `browser_fill_form`

Fill multiple form fields

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `fields` | array[object] | 是 | Fields to fill in |

### `browser_find`

Search the accessibility snapshot of the current page for text or a regular expression. Returns matching snapshot nodes with a few lines of surrounding context (like search snippets), each shown under its path from the root of the tree, which is cheaper than capturing the whole snapshot when you only need to locate an element and its ref.

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `text` | string |  | Plain text to search for in the page snapshot (case-insensitive substring match). Provide either text or regex, not both. |
| `regex` | string |  | Regular expression to search for in the page snapshot. Matching is case-sensitive by default; wrap the pattern in slashes to add flags, e.g. "/error/i" for case-insensitive. Provide either text or regex, not both. |

### `browser_handle_dialog`

Handle a dialog

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `accept` | boolean | 是 | Whether to accept the dialog. |
| `promptText` | string |  | The text of the prompt in case of a prompt dialog. |

### `browser_hide_highlight`

Remove a highlight overlay previously added for the element.

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `element` | string |  | Human-readable element description used when adding the highlight; must match the value passed to browser_highlight. |
| `target` | string |  | Exact target element reference from the page snapshot, or a unique element selector |

### `browser_highlight`

Show a persistent highlight overlay around the element on the page.

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `element` | string |  | Human-readable element description used to obtain permission to interact with the element |
| `target` | string | 是 | Exact target element reference from the page snapshot, or a unique element selector |
| `style` | string |  | Additional inline CSS applied to the highlight overlay, e.g. "outline: 2px dashed red". |

### `browser_hover`

Hover over element on page

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `element` | string |  | Human-readable element description used to obtain permission to interact with the element |
| `target` | string | 是 | Exact target element reference from the page snapshot, or a unique element selector |

### `browser_mouse_click_xy`

Click mouse button at a given position

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `x` | number | 是 | X coordinate |
| `y` | number | 是 | Y coordinate |
| `button` | string (left | right | middle) |  | Button to click, defaults to left |
| `clickCount` | number |  | Number of clicks, defaults to 1 |
| `delay` | number |  | Time to wait between mouse down and mouse up in milliseconds, defaults to 0 |

### `browser_mouse_down`

Press mouse down

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `button` | string (left | right | middle) |  | Button to press, defaults to left |

### `browser_mouse_drag_xy`

Drag left mouse button to a given position

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `startX` | number | 是 | Start X coordinate |
| `startY` | number | 是 | Start Y coordinate |
| `endX` | number | 是 | End X coordinate |
| `endY` | number | 是 | End Y coordinate |

### `browser_mouse_move_xy`

Move mouse to a given position

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `x` | number | 是 | X coordinate |
| `y` | number | 是 | Y coordinate |

### `browser_mouse_up`

Press mouse up

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `button` | string (left | right | middle) |  | Button to press, defaults to left |

### `browser_mouse_wheel`

Scroll mouse wheel

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `deltaX` | number | 是 | X delta |
| `deltaY` | number | 是 | Y delta |

### `browser_navigate`  ← 独立工具

Navigate to a URL

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `url` | string | 是 | The URL to navigate to |

### `browser_navigate_back`

Go back to the previous page in the history

无参数。

### `browser_network_request`

Returns full details (headers and body) of a single network request, or a single part if `part` is set. Use the number from browser_network_requests.

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `index` | integer | 是 | 1-based index of the request, as printed by browser_network_requests. |
| `part` | string (request-headers | request-body | response-headers | response-body) |  | Return only this part of the request. Omit to return full details. |
| `filename` | string |  | Filename to save the result to. If not provided, output is returned as text. |

### `browser_network_requests`

Returns a numbered list of network requests since loading the page. Use browser_network_request with the number to get full details.

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `static` | boolean | 是 | Whether to include successful static resources like images, fonts, scripts, etc. Defaults to false. |
| `filter` | string |  | Only return requests whose URL matches this regexp (e.g. "/api/.*user"). |
| `filename` | string |  | Filename to save the network requests to. If not provided, requests are returned as text. |

### `browser_press_key`

Press a key on the keyboard

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | string | 是 | Name of the key to press or a character to generate, such as `ArrowLeft` or `a` |

### `browser_resize`

Resize the browser window

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `width` | number | 是 | Width of the browser window |
| `height` | number | 是 | Height of the browser window |

### `browser_resume`

Resume script execution after it was paused. When called with step set to true, execution will pause again before the next action.

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `step` | boolean |  | When true, execution will pause again before the next action, allowing step-by-step debugging. |
| `location` | string |  | Pause execution at a specific <file>:<line>, e.g. "example.spec.ts:42". |

### `browser_run_code_unsafe`

Run a Playwright code snippet. Unsafe: executes arbitrary JavaScript in the Playwright server process and is RCE-equivalent.

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | string |  | A JavaScript function containing Playwright code to execute. It will be invoked with a single argument, page, which you can use for any page interaction. For example: `async (page) => { await page.getByRole('button', { name: 'Submit' }).click(); return await page.title(); }` |
| `filename` | string |  | Load code from the specified file. If both code and filename are provided, code will be ignored. |

### `browser_select_option`

Select an option in a dropdown

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `element` | string |  | Human-readable element description used to obtain permission to interact with the element |
| `target` | string | 是 | Exact target element reference from the page snapshot, or a unique element selector |
| `values` | array[string] | 是 | Array of values to select in the dropdown. This can be a single value or multiple values. |

### `browser_snapshot`  ← 独立工具

Capture accessibility snapshot of the current page, this is better than screenshot

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `target` | string |  | Exact target element reference from the page snapshot, or a unique element selector |
| `filename` | string |  | Save snapshot to markdown file instead of returning it in the response. |
| `depth` | number |  | Limit the depth of the snapshot tree |
| `boxes` | boolean |  | Include each element's bounding box as [box=x,y,width,height] in the snapshot. Coordinates are viewport-relative, in CSS pixels (Element.getBoundingClientRect) |

### `browser_start_tracing`

Start trace recording

无参数。

### `browser_start_video`

Start video recording

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `filename` | string |  | Filename to save the video. |
| `size` | object |  | Video size |

### `browser_stop_tracing`

Stop trace recording

无参数。

### `browser_stop_video`

Stop video recording

无参数。

### `browser_tabs`  ← 独立工具

List, create, close, or select a browser tab.

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `action` | string (list | new | close | select) | 是 | Operation to perform |
| `index` | number |  | Tab index, used for close/select. If omitted for close, current tab is closed. |
| `url` | string |  | URL to navigate to in the new tab, used for new. |

### `browser_take_screenshot`  ← 独立工具

Take a screenshot of the current page. You can't perform actions based on the screenshot, use browser_snapshot for actions.

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `element` | string |  | Human-readable element description used to obtain permission to interact with the element |
| `target` | string |  | Exact target element reference from the page snapshot, or a unique element selector |
| `type` | string (png | jpeg) | 是 | Image format for the screenshot. Default is png. |
| `filename` | string |  | File name to save the screenshot to. Defaults to `page-{timestamp}.{png\|jpeg}` if not specified. Prefer relative file names to stay within the output directory. |
| `fullPage` | boolean |  | When true, takes a screenshot of the full scrollable page, instead of the currently visible viewport. Cannot be used with element screenshots. |
| `scale` | string (css | device) | 是 | Image resolution scale. "css" produces a screenshot sized in CSS pixels (smaller, consistent across devices). "device" produces a high-resolution screenshot using device pixels (larger, accounts for the device pixel ratio). Default is css. |

### `browser_type`  ← 独立工具

Type text into editable element

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `element` | string |  | Human-readable element description used to obtain permission to interact with the element |
| `target` | string | 是 | Exact target element reference from the page snapshot, or a unique element selector |
| `text` | string | 是 | Text to type into the element |
| `submit` | boolean |  | Whether to submit entered text (press Enter after) |
| `slowly` | boolean |  | Whether to type one character at a time. Useful for triggering key handlers in the page. By default entire text is filled in at once. |

### `browser_video_chapter`

Add a chapter marker to the video recording. Shows a full-screen chapter card with blurred backdrop.

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | string | 是 | Chapter title |
| `description` | string |  | Chapter description |
| `duration` | number |  | Duration in milliseconds to show the chapter card |

### `browser_video_hide_actions`

Stop annotating actions performed on the page.

无参数。

### `browser_video_show_actions`

Annotate subsequent actions performed on the page with a callout that names the action and highlights the target element. Useful while video recording or screencasting.

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `duration` | number |  | How long each action annotation stays on screen, in milliseconds. Defaults to 500. |
| `position` | string (top-left | top | top-right | bottom-left | bottom | bottom-right) |  | Where to place the action title relative to the page. Defaults to top-right. |
| `cursor` | string (none | pointer) |  | Cursor decoration for pointer actions. "pointer" (default) animates a mouse pointer from the previous action point to the next one; "none" disables the cursor decoration. |

### `browser_wait_for`

Wait for text to appear or disappear or a specified time to pass

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `time` | number |  | The time to wait in seconds |
| `text` | string |  | The text to wait for |
| `textGone` | string |  | The text to wait for to disappear |
