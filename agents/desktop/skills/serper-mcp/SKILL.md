---
name: serper-mcp
description: "Google 系垂直搜索 —— 图片、地图、学术、专利、新闻、购物、评论等。 经 `serper_call(tool, args_json)` 调用，本文档载有全部 13 个工具的参数表。Use when the task needs serper capabilities beyond the always-loaded `serper_google_search`."
category: integration
generated_by: tools/_gen_mcp_skill.py
---

# serper 工具参数表

这些工具由上游 MCP 服务器提供，schema 由它定义。**本文件是生成的** —— 改了缓存请重跑
`python tools/_gen_mcp_skill.py serper`，不要手改（上游改过名，手维护必然漂移）。

回复用中文，除非用户明显在用其他语言。

## 怎么调

```
serper_call(tool="<下表的工具名>", args_json='{"参数": 值}')
```

`args_json` 是 JSON **对象**字符串；不吃参数的工具可省略或传 `{}`。
工具名写全名（带 `serper_` 前缀）或裸名都认。名字写错会被本地拒绝并列出可用名，不会真发请求。

## 这几个是独立工具，直接调，别走 `serper_call`

- `serper_google_search`

## 工具参数表

### `serper_google_search`  ← 独立工具

Search Google for results

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `q` | string | 是 | The query to search for |
| `gl` | string |  | The country to search in, e.g. us, uk, ca, au, etc. |
| `location` | string |  | The location to search in, e.g. San Francisco, CA, USA |
| `hl` | string |  | The language to search in, e.g. en, es, fr, de, etc. |
| `page` | string |  | The page number to return, first page is 1 (integer value as string) |
| `tbs` | string |  | The time period to search in, e.g. d, w, m, y |
| `num` | string |  | The number of results to return, max is 100 (integer value as string) |

### `serper_google_search_autocomplete`

Search Google for results

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `q` | string | 是 | The query to search for |
| `gl` | string |  | The country to search in, e.g. us, uk, ca, au, etc. |
| `location` | string |  | The location to search in, e.g. San Francisco, CA, USA |
| `hl` | string |  | The language to search in, e.g. en, es, fr, de, etc. |
| `page` | string |  | The page number to return, first page is 1 (integer value as string) |
| `autocorrect` | string |  | Automatically correct (boolean value as string: 'true' or 'false') |

### `serper_google_search_images`

Search Google for results

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `q` | string | 是 | The query to search for |
| `gl` | string |  | The country to search in, e.g. us, uk, ca, au, etc. |
| `location` | string |  | The location to search in, e.g. San Francisco, CA, USA |
| `hl` | string |  | The language to search in, e.g. en, es, fr, de, etc. |
| `page` | string |  | The page number to return, first page is 1 (integer value as string) |
| `tbs` | string |  | The time period to search in, e.g. d, w, m, y |
| `num` | string |  | The number of results to return, max is 100 (integer value as string) |

### `serper_google_search_lens`

Search Google for results

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `url` | string | 是 | The url to search |
| `gl` | string |  | The country to search in, e.g. us, uk, ca, au, etc. |
| `hl` | string |  | The language to search in, e.g. en, es, fr, de, etc. |

### `serper_google_search_maps`

Search Google for results

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `q` | string | 是 | The query to search for |
| `ll` | string |  | The GPS position & zoom level |
| `placeId` | string |  | The place ID to search in |
| `cid` | string |  | The CID to search in |
| `gl` | string |  | The country to search in, e.g. us, uk, ca, au, etc. |
| `hl` | string |  | The language to search in, e.g. en, es, fr, de, etc. |
| `page` | string |  | The page number to return, first page is 1 (integer value as string) |

### `serper_google_search_news`

Search Google for results

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `q` | string | 是 | The query to search for |
| `gl` | string |  | The country to search in, e.g. us, uk, ca, au, etc. |
| `location` | string |  | The location to search in, e.g. San Francisco, CA, USA |
| `hl` | string |  | The language to search in, e.g. en, es, fr, de, etc. |
| `page` | string |  | The page number to return, first page is 1 (integer value as string) |
| `tbs` | string |  | The time period to search in, e.g. d, w, m, y |
| `num` | string |  | The number of results to return, max is 100 (integer value as string) |

### `serper_google_search_patents`

Search Google for results

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `q` | string | 是 | The query to search for |
| `num` | string |  | The number of results to return, max is 100 (integer value as string) |
| `page` | string |  | The page number to return, first page is 1 (integer value as string) |

### `serper_google_search_places`

Search Google for results

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `q` | string | 是 | The query to search for |
| `gl` | string |  | The country to search in, e.g. us, uk, ca, au, etc. |
| `location` | string |  | The location to search in, e.g. San Francisco, CA, USA |
| `hl` | string |  | The language to search in, e.g. en, es, fr, de, etc. |
| `page` | string |  | The page number to return, first page is 1 (integer value as string) |
| `autocorrect` | string |  | Automatically correct (boolean value as string: 'true' or 'false') |

### `serper_google_search_reviews`

Search Google for results

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `fid` | string | 是 | The FID |
| `cid` | string |  | The CID to search in |
| `placeId` | string |  | The place ID to search in |
| `sortBy` | string |  | The sort order to use (enum value as string: 'mostRelevant', 'newest', 'highestRating', 'lowestRating') |
| `topicId` | string |  | The topic ID to search in |
| `nextPageToken` | string |  | The next page token to use |
| `gl` | string |  | The country to search in, e.g. us, uk, ca, au, etc. |
| `hl` | string |  | The language to search in, e.g. en, es, fr, de, etc. |

### `serper_google_search_scholar`

Search Google for results

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `q` | string | 是 | The query to search for |
| `gl` | string |  | The country to search in, e.g. us, uk, ca, au, etc. |
| `location` | string |  | The location to search in, e.g. San Francisco, CA, USA |
| `hl` | string |  | The language to search in, e.g. en, es, fr, de, etc. |
| `page` | string |  | The page number to return, first page is 1 (integer value as string) |
| `autocorrect` | string |  | Automatically correct (boolean value as string: 'true' or 'false') |

### `serper_google_search_shopping`

Search Google for results

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `q` | string | 是 | The query to search for |
| `gl` | string |  | The country to search in, e.g. us, uk, ca, au, etc. |
| `location` | string |  | The location to search in, e.g. San Francisco, CA, USA |
| `hl` | string |  | The language to search in, e.g. en, es, fr, de, etc. |
| `page` | string |  | The page number to return, first page is 1 (integer value as string) |
| `autocorrect` | string |  | Automatically correct (boolean value as string: 'true' or 'false') |
| `num` | string |  | The number of results to return, max is 100 (integer value as string) |

### `serper_google_search_videos`

Search Google for results

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `q` | string | 是 | The query to search for |
| `gl` | string |  | The country to search in, e.g. us, uk, ca, au, etc. |
| `location` | string |  | The location to search in, e.g. San Francisco, CA, USA |
| `hl` | string |  | The language to search in, e.g. en, es, fr, de, etc. |
| `page` | string |  | The page number to return, first page is 1 (integer value as string) |
| `tbs` | string |  | The time period to search in, e.g. d, w, m, y |
| `num` | string |  | The number of results to return, max is 100 (integer value as string) |

### `serper_webpage_scrape`

Scrape webpage by url

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `url` | string | 是 | The url to scrape |
| `includeMarkdown` | string |  | Include markdown in the response (boolean value as string: 'true' or 'false') |
