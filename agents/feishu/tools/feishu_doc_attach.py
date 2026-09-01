"""Feishu/Lark docx — put a local image or file into a document's body.

Neither is a request with arguments. Both are the same three-call dance, and the order
matters:

  1. create an **empty** block in the body (image = block_type 27, file = 23) → block_id
  2. upload the local file *into that block* (``parent_type`` ``docx_image`` /
     ``docx_file``, ``parent_node`` = the block_id)
  3. PATCH the block with ``replace_image`` / ``replace_file`` = the returned file_token

Step 3 is what makes the block render: without it the upload is attached and the block
still shows a placeholder. So a failure at step 2 or 3 removes the empty block it
created — an orphaned placeholder in someone's document is worse than no attachment.

``feishu_doc_append_content`` writes Markdown, including image *links*; these two are for
when the picture or file exists locally and has to be uploaded. To attach a rendered
chart, ``feishu_chart`` already does all of this.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_doc_append_image(
    document_id: str, image_path: str, caption: str = "", user_key: str = "", identity: str = ""
) -> str:
    """Append a local image to a Feishu/Lark document as a real image block.

    Returns the new block's ``block_id`` and the uploaded ``file_token``. The image is
    added at the end of the body; move it with the block tools if it belongs elsewhere.

    Args:
        document_id: The docx's document_id (the token in a ``feishu.cn/docx/<token>``
            URL). For a doc in a wiki, resolve the node's ``obj_token`` first.
        image_path: Local path of the image to upload (up to 20MB — larger needs
            Feishu's chunked upload, which this tool does not implement).
        caption: Optional caption written as a paragraph below the image. Feishu's image
            blocks carry no caption field of their own, so it becomes its own block; a
            caption that fails to write is reported without invalidating the image.
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who owns the result. Omit to use the choice
            remembered for this ``user_key``.
    """
    return _f.dumps_result(await _f.append_doc_image_impl(document_id, image_path, caption, user_key, identity))


async def feishu_doc_append_file(
    document_id: str, file_path: str, caption: str = "", user_key: str = "", identity: str = ""
) -> str:
    """Attach a local file to a Feishu/Lark document as a real file (附件) block.

    Same three-step flow as ``feishu_doc_append_image`` with the file block's own
    constants; returns the new ``block_id`` and ``file_token``. The reader gets a
    downloadable attachment inside the document rather than a link.

    Args:
        document_id: The docx's document_id (the token in a ``feishu.cn/docx/<token>``
            URL). For a doc in a wiki, resolve the node's ``obj_token`` first.
        file_path: Local path of the file to attach (up to 20MB — larger needs Feishu's
            chunked upload, which this tool does not implement).
        caption: Optional caption written as a paragraph below the attachment, e.g. what
            the file is for.
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` — who owns the result. Omit to use the choice
            remembered for this ``user_key``.
    """
    return _f.dumps_result(await _f.append_doc_file_impl(document_id, file_path, caption, user_key, identity))
