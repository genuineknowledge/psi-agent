/**
 * Streaming chunk semantics for the chat SSE loop.
 *
 * The session backend emits two very different things as ``reasoning`` chunks
 * (see ``session/agent.py``):
 *
 *   1. The model's own thinking stream (``delta.reasoning``). Reasoning models
 *      like deepseek interleave these token-by-token with the ``content``
 *      stream. They are NOT rendered and must NOT split the answer bubble.
 *   2. Turn-boundary markers around tool use — ``[Tool Call: ...]`` and
 *      ``[Tool Result: ...]``. These mark the end of one assistant turn; the
 *      text the model produces after a tool round should start a fresh bubble.
 *
 * Treating every reasoning chunk as a bubble boundary (the old behaviour) meant
 * that with a reasoning model each interleaved thinking token nulled the current
 * bubble, so every content token landed in its own bubble — the answer rendered
 * as a column of single characters.
 *
 * Only category (2) is a real boundary. These markers are backend-generated,
 * controlled strings with fixed prefixes, so a prefix check is reliable.
 */

const TURN_BOUNDARY_PREFIXES = ['[Tool Call:', '[Tool Result:']

/**
 * Whether a ``reasoning`` chunk marks a turn boundary (tool call/result) that
 * should split the assistant bubble, as opposed to the model's thinking stream.
 * @param {string | undefined | null} text
 * @returns {boolean}
 */
export function isTurnBoundaryReasoning(text) {
  if (typeof text !== 'string') return false
  const t = text.trimStart()
  return TURN_BOUNDARY_PREFIXES.some(p => t.startsWith(p))
}
