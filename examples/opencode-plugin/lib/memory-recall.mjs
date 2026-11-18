import { buildRecallBlock, isRecallEnabled } from "./shared/recall-core.mjs"
import { isBypassed } from "./shared/session-model.mjs"
import { effectivePeerId, fetchJSON, log } from "./utils.mjs"

export function createMemoryRecall({ config, sessionManager }) {
  let compressor = null
  async function buildRelevantMemories(input, parts, { deadline, signal } = {}) {
    if (!isRecallEnabled(config) || signal?.aborted) return
    const query = extractCurrentUserText(parts ?? [])
    if (!query) return
    if (query.length < config.minQueryLength) return
    const sessionID = input.sessionID
    if (isBypassed(config, {
      sessionId: sessionID,
      cwd: input.directory ?? input.cwd,
    })) return

    const compression = sessionID ? compressor?.forSession(sessionID, { deadline, signal }) : null
    const signals = [signal, compression?.signal].filter(Boolean)
    const requestSignal = signals.length ? AbortSignal.any(signals) : undefined
    try {
      const health = await fetchJSON(config, "/health", { signal: requestSignal }, { timeoutMs: 5000 })
      if (!health.ok || requestSignal?.aborted) return

      const block = await buildRecallBlock(
        // 5s is this hook's own budget for a bare retrieval; when the request
        // also spends a server fuse the helper hands down a longer deadline, and
        // overriding it here would abort a request the server was still inside.
        (path, init = {}, options = {}) =>
          requestSignal?.aborted ? { ok: false } :
            fetchJSON(config, path, { ...init, signal: requestSignal ?? init.signal }, { ...options, timeoutMs: options.timeoutMs ?? 5000 }),
        config,
        query,
        {
          actorPeerId: effectivePeerId(config),
          legacyPeerId: config.effectivePeer?.legacyPeerId ?? "",
          // The mapped OV session is what turns on server-side query expansion
          // and the cross-turn dedup ledger.
          sessionId: sessionID ? sessionManager.getMappedSessionId(sessionID) : "",
          excludeUris: config.recallExcludeUris,
          runCompressor: compression?.run,
          localCompressorAvailable: Boolean(compression),
          digestCachePath: compression ? compressor.cachePath(sessionID) : undefined,
          log: (stage, data) => log("DEBUG", "recall", stage, data),
        },
      )
      if (!requestSignal?.aborted) return block || undefined
    } finally {
      compression?.close()
    }
  }

  async function injectRelevantMemories(input, output) {
    const block = await buildRelevantMemories({
      ...input,
      sessionID: input.sessionID ?? output.message?.sessionID,
    }, output.parts)
    if (block && prependSyntheticRecallPart(input, output, block)) {
      log("INFO", "recall", "Injected OpenViking context")
    }
  }

  return { buildRelevantMemories, injectRelevantMemories, setCompressor: (value) => { compressor = value } }
}

export function extractCurrentUserText(parts) {
  const texts = []
  for (const part of parts) {
    if (part.type !== "text" || typeof part.text !== "string") continue
    if (part.synthetic || part.ignored) continue
    if (part.text.includes("<openviking-context")) return null
    texts.push(part.text)
  }
  const joined = texts.join(" ").trim()
  return joined || null
}

function prependSyntheticRecallPart(input, output, injection) {
  const sessionID = input.sessionID ?? output.message?.sessionID
  // messageID is optional in opencode's chat.message API (messageID?: string).
  // When absent, generate a fallback so recall context is always injected.
  const messageID = input.messageID ?? output.message?.id ?? `ov-recall-${Date.now()}`
  if (!sessionID) return false

  output.parts.unshift({
    id: `prt-ov-recall-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    type: "text",
    text: injection,
    synthetic: true,
    sessionID,
    messageID,
  })
  return true
}
