import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import {
  api,
  formatBytes,
  formatDuration,
  type CaptionStyle,
  type Clip,
  type ClipApproval,
  type ClipMetadata,
  type CropPath,
  type Job,
  type Publication,
  type Word,
} from '../api'
import { CaptionEditor } from '../components/CaptionEditor'
import { ClipPlayer } from '../components/ClipPlayer'
import { ErrorNote } from '../components/ErrorNote'
import { TrimBar } from '../components/TrimBar'

const RATIOS = ['9:16', '1:1', '16:9'] as const

export function Review() {
  const { jobId } = useParams()
  const [job, setJob] = useState<Job | null>(null)
  const [clips, setClips] = useState<Clip[]>([])
  const [styles, setStyles] = useState<CaptionStyle[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [words, setWords] = useState<Word[]>([])
  const [cropPath, setCropPath] = useState<CropPath | null>(null)
  const [wordsDirty, setWordsDirty] = useState(false)
  const [savingWords, setSavingWords] = useState(false)
  const [exporting, setExporting] = useState<Set<string>>(new Set())
  const [error, setError] = useState<Error | null>(null)

  // Step 23: SEO & Publishing Metadata State
  const [metadata, setMetadata] = useState<ClipMetadata | null>(null)
  const [metadataTitle, setMetadataTitle] = useState('')
  const [metadataDesc, setMetadataDesc] = useState('')
  const [metadataHashtags, setMetadataHashtags] = useState('')
  const [metadataMentions, setMetadataMentions] = useState('')
  const [metadataCta, setMetadataCta] = useState('')
  const [savingMetadata, setSavingMetadata] = useState(false)
  const [metadataNotice, setMetadataNotice] = useState<string | null>(null)

  // Step 24: Clip Approval State
  const [approval, setApproval] = useState<ClipApproval | null>(null)
  const [approvalNote, setApprovalNote] = useState('')
  const [approvalLoading, setApprovalLoading] = useState(false)
  const [approvalNotice, setApprovalNotice] = useState<string | null>(null)

  // Step 25: Remote Publishing State
  const [publications, setPublications] = useState<Publication[]>([])
  const [publishingLoading, setPublishingLoading] = useState(false)
  const [publishingNotice, setPublishingNotice] = useState<string | null>(null)
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([
    'youtube',
    'instagram',
    'telegram',
  ])

  useEffect(() => {
    if (!jobId) return
    void Promise.all([api.getJob(jobId), api.listClips(jobId), api.captionStyles()])
      .then(([loadedJob, loadedClips, loadedStyles]) => {
        setJob(loadedJob)
        setClips(loadedClips)
        setStyles(loadedStyles)
        setSelectedId((current) => current ?? loadedClips[0]?.id ?? null)
      })
      .catch((err) => setError(err as Error))
  }, [jobId])

  const selected = useMemo(
    () => clips.find((clip) => clip.id === selectedId) ?? null,
    [clips, selectedId],
  )

  useEffect(() => {
    if (!selected) return
    setWordsDirty(false)
    api
      .getClipWords(selected.id)
      .then(setWords)
      .catch(() => setWords([]))
    // 404 is expected for audio-only sources and jobs that never reframed; the
    // player falls back to a centre crop, matching what the renderer does.
    api
      .getCropPath(selected.id)
      .then(setCropPath)
      .catch(() => setCropPath(null))

    // Fetch Step 23 SEO & Publishing Metadata
    if (jobId) {
      api
        .getClipMetadata(jobId, selected.id)
        .then((m) => {
          setMetadata(m)
          setMetadataTitle(m.final_title)
          setMetadataDesc(m.final_description)
          setMetadataHashtags((m.final_hashtags || []).join(' '))
          setMetadataMentions((m.final_mentions || []).join(' '))
          setMetadataCta(m.final_cta || '')
          setMetadataNotice(null)
        })
        .catch(() => setMetadata(null))

      // Fetch Step 24 Approval
      api
        .getClipApproval(jobId, selected.id)
        .then((a) => {
          setApproval(a)
          setApprovalNote('')
          setApprovalNotice(null)
        })
        .catch(() => setApproval(null))

      // Fetch Step 25 Remote Publications
      api
        .getJobPublications(jobId)
        .then((pubs) => {
          setPublications(pubs)
          setPublishingNotice(null)
        })
        .catch(() => setPublications([]))
    }
  }, [selected?.id, jobId])

  const saveMetadata = async () => {
    if (!jobId || !selected || !metadata) return
    setSavingMetadata(true)
    setMetadataNotice(null)
    try {
      const updated = await api.patchClipMetadata(jobId, selected.id, {
        final_title: metadataTitle,
        final_description: metadataDesc,
        final_hashtags: metadataHashtags.split(/\s+/).filter(Boolean),
        final_mentions: metadataMentions.split(/\s+/).filter(Boolean),
        final_cta: metadataCta,
      })
      setMetadata(updated)
      setMetadataNotice('Metadata saved successfully!')
    } catch (err) {
      setError(err as Error)
    } finally {
      setSavingMetadata(false)
    }
  }

  const resetMetadata = async () => {
    if (!jobId || !selected || !metadata) return
    setSavingMetadata(true)
    setMetadataNotice(null)
    try {
      const updated = await api.patchClipMetadata(jobId, selected.id, {
        action: 'reset_to_generated',
      })
      setMetadata(updated)
      setMetadataTitle(updated.final_title)
      setMetadataDesc(updated.final_description)
      setMetadataHashtags((updated.final_hashtags || []).join(' '))
      setMetadataMentions((updated.final_mentions || []).join(' '))
      setMetadataCta(updated.final_cta || '')
      setMetadataNotice('Reset to original generated metadata.')
    } catch (err) {
      setError(err as Error)
    } finally {
      setSavingMetadata(false)
    }
  }

  // Step 24: Approval action handlers
  const doApprovalAction = async (
    action: 'APPROVE' | 'REJECT' | 'REQUEST_CHANGES' | 'LOCK',
    note: string = ''
  ) => {
    if (!jobId || !selected) return
    setApprovalLoading(true)
    setApprovalNotice(null)
    try {
      const updated = await api.postClipApprovalAction(jobId, selected.id, {
        action,
        operator_note: note,
        expected_version: approval?.version,
      })
      setApproval(updated)
      setApprovalNote('')
      setApprovalNotice(`Clip ${action.toLowerCase().replace('_', ' ')} — status: ${updated.current_status}`)
    } catch (err) {
      setError(err as Error)
    } finally {
      setApprovalLoading(false)
    }
  }

  const resetApproval = async () => {
    if (!jobId || !selected) return
    setApprovalLoading(true)
    setApprovalNotice(null)
    try {
      const updated = await api.resetClipApproval(jobId, selected.id)
      setApproval(updated)
      setApprovalNote('')
      setApprovalNotice('Approval reset to PENDING_REVIEW.')
    } catch (err) {
      setError(err as Error)
    } finally {
      setApprovalLoading(false)
    }
  }

  // Step 25: Remote Publishing handlers
  const handlePublishClip = async (dryRun: boolean = false) => {
    if (!jobId || !selected) return
    setPublishingLoading(true)
    setPublishingNotice(null)
    try {
      const results = await api.publishClip(jobId, selected.id, {
        platforms: selectedPlatforms,
        dry_run: dryRun,
      })
      const allPubs = await api.getJobPublications(jobId)
      setPublications(allPubs)
      const successCount = results.filter((r) => r.status === 'PUBLISHED').length
      setPublishingNotice(
        `Publishing complete: ${successCount}/${results.length} published successfully.`
      )
    } catch (err) {
      setError(err as Error)
    } finally {
      setPublishingLoading(false)
    }
  }

  const handleRetryPublication = async (pubId: string) => {
    if (!jobId) return
    setPublishingLoading(true)
    setPublishingNotice(null)
    try {
      await api.retryPublication(pubId)
      const allPubs = await api.getJobPublications(jobId)
      setPublications(allPubs)
      setPublishingNotice('Publication retry completed.')
    } catch (err) {
      setError(err as Error)
    } finally {
      setPublishingLoading(false)
    }
  }

  const togglePlatform = (p: string) => {
    setSelectedPlatforms((prev) =>
      prev.includes(p) ? prev.filter((item) => item !== p) : [...prev, p]
    )
  }

  const patchClip = useCallback((updated: Clip) => {
    setClips((current) => current.map((clip) => (clip.id === updated.id ? updated : clip)))
  }, [])

  const setStatus = async (clip: Clip, status: Clip['status']) => {
    try {
      patchClip(await api.patchClip(clip.id, { status }))
    } catch (err) {
      setError(err as Error)
    }
  }

  const commitTrim = async (start: number, end: number) => {
    if (!selected) return
    try {
      patchClip(await api.patchClip(selected.id, { start_s: start, end_s: end }))
      setWords(await api.getClipWords(selected.id))
    } catch (err) {
      setError(err as Error)
    }
  }

  const saveWords = async () => {
    if (!selected) return
    setSavingWords(true)
    try {
      patchClip(await api.patchCaptions(selected.id, { words }))
      setWordsDirty(false)
    } catch (err) {
      setError(err as Error)
    } finally {
      setSavingWords(false)
    }
  }

  const setStyle = async (clip: Clip, styleKey: string) => {
    try {
      patchClip(await api.patchCaptions(clip.id, { caption_style: styleKey }))
    } catch (err) {
      setError(err as Error)
    }
  }

  const setRatio = async (clip: Clip, ratio: string) => {
    try {
      patchClip(await api.patchCaptions(clip.id, { ratio }))
    } catch (err) {
      setError(err as Error)
    }
  }

  const exportClip = async (clip: Clip) => {
    setExporting((current) => new Set(current).add(clip.id))
    setError(null)
    try {
      await api.exportClip(clip.id, clip.ratio, clip.caption_style)
      patchClip(await api.getClip(clip.id))
    } catch (err) {
      setError(err as Error)
    } finally {
      setExporting((current) => {
        const next = new Set(current)
        next.delete(clip.id)
        return next
      })
    }
  }

  const exportKept = async () => {
    const targets = clips.filter((clip) => clip.status === 'kept')
    for (const clip of targets) await exportClip(clip)
  }

  const keptCount = clips.filter((clip) => clip.status === 'kept').length
  const activeStyle = styles.find((style) => style.key === selected?.caption_style)

  if (error && clips.length === 0) {
    return (
      <div className="max-w-3xl pt-24">
        <ErrorNote error={error} />
      </div>
    )
  }

  if (!job) return <p className="pt-24 text-sm text-ink-500">Loading…</p>

  return (
    <div className="pt-10">
      <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-ink-800 pb-5">
        <div className="min-w-0">
          <Link to="/" className="eyebrow transition-colors hover:text-sodium-500">
            ← All jobs
          </Link>
          <h1 className="mt-2 max-w-2xl truncate font-display text-[clamp(1.5rem,3vw,2.25rem)] leading-tight text-ink-100">
            {job.source?.title || 'Untitled'}
          </h1>
        </div>

        <div className="flex items-baseline gap-6">
          <span className="numeric text-xs text-ink-500">
            {clips.length} clips · {keptCount} kept
          </span>
          <button onClick={exportKept} disabled={keptCount === 0} className="btn btn-primary">
            Export kept
          </button>
        </div>
      </div>

      {error && (
        <div className="mt-6 max-w-3xl">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {clips.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="mt-8 grid gap-x-10 gap-y-10 lg:grid-cols-[minmax(0,20rem)_minmax(0,26rem)_minmax(0,1fr)]">
          {/* Ranked list */}
          <section className="lg:max-h-[76vh] lg:overflow-y-auto lg:pr-1">
            <p className="eyebrow border-b border-ink-800 pb-2">Ranked</p>
            <ul>
              {clips.map((clip, index) => (
                <li key={clip.id}>
                  <ClipRow
                    clip={clip}
                    index={index}
                    selected={clip.id === selectedId}
                    onSelect={() => setSelectedId(clip.id)}
                    onKeep={() =>
                      setStatus(clip, clip.status === 'kept' ? 'candidate' : 'kept')
                    }
                    onDiscard={() =>
                      setStatus(clip, clip.status === 'discarded' ? 'candidate' : 'discarded')
                    }
                  />
                </li>
              ))}
            </ul>
          </section>

          {/* Player + trim */}
          <section className="space-y-8">
            {selected && jobId && (
              <>
                <ClipPlayer
                  src={api.mediaUrl(jobId)}
                  startS={selected.start_s}
                  endS={selected.end_s}
                  words={words}
                  style={activeStyle}
                  ratio={selected.ratio}
                  cropPath={cropPath}
                />
                <TrimBar
                  words={words}
                  startS={selected.start_s}
                  endS={selected.end_s}
                  originalStart={selected.start_s}
                  originalEnd={selected.end_s}
                  onCommit={commitTrim}
                />
              </>
            )}
          </section>

          {/* Editor + style */}
          <section className="space-y-10">
            {selected && (
              <>
                <div>
                  <p className="eyebrow">Why this clip</p>
                  <p className="mt-2 max-w-prose text-[0.9375rem] leading-relaxed text-ink-300">
                    {selected.reason || 'No rationale returned for this clip.'}
                  </p>
                  {selected.hook && (
                    <p className="mt-3 border-l-2 border-ink-700 pl-3 font-display text-lg italic text-ink-200">
                      “{selected.hook}”
                    </p>
                  )}
                </div>

                <CaptionEditor
                  words={words}
                  onChange={(next) => {
                    setWords(next)
                    setWordsDirty(true)
                  }}
                  onSave={saveWords}
                  saving={savingWords}
                  dirty={wordsDirty}
                />

                <div>
                  <p className="eyebrow border-b border-ink-800 pb-2">Caption style</p>
                  <div className="mt-3 space-y-1">
                    {styles.map((style) => (
                      <button
                        key={style.key}
                        onClick={() => setStyle(selected, style.key)}
                        className={[
                          'block w-full border-l-2 py-2 pl-3 text-left transition-colors duration-200',
                          style.key === selected.caption_style
                            ? 'border-sodium-500 bg-ink-850/60'
                            : 'border-transparent hover:border-ink-700 hover:bg-ink-850/30',
                        ].join(' ')}
                      >
                        <span className="text-sm text-ink-100">{style.label}</span>
                        <span className="mt-0.5 block text-xs leading-snug text-ink-500">
                          {style.description}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <p className="eyebrow border-b border-ink-800 pb-2">Aspect ratio</p>
                  <div className="mt-3 flex gap-2">
                    {RATIOS.map((ratio) => (
                      <button
                        key={ratio}
                        onClick={() => setRatio(selected, ratio)}
                        className={[
                          'numeric btn',
                          ratio === selected.ratio ? 'btn-primary' : 'btn-ghost',
                        ].join(' ')}
                      >
                        {ratio}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="border-t border-ink-800 pt-6">
                  <button
                    onClick={() => exportClip(selected)}
                    disabled={exporting.has(selected.id)}
                    className="btn btn-primary w-full"
                  >
                    {exporting.has(selected.id) ? 'Rendering…' : 'Export this clip'}
                  </button>

                  {selected.exports.length > 0 && (
                    <ul className="mt-4 space-y-2">
                      {selected.exports.map((record) => (
                        <li
                          key={record.id}
                          className="flex items-baseline justify-between gap-3 text-xs"
                        >
                          <div className="flex items-center gap-2 truncate">
                            <a
                              href={api.streamExportUrl(record.id)}
                              target="_blank"
                              rel="noreferrer"
                              className="text-sodium-500 hover:underline"
                              title="Stream inline with range requests"
                            >
                              ▶ Stream
                            </a>
                            <span>·</span>
                            <a
                              href={record.download_url}
                              download
                              className="truncate text-ink-300 underline underline-offset-4"
                            >
                              {record.ratio} · {record.style}
                            </a>
                          </div>
                          <span className="numeric shrink-0 text-ink-600">
                            {formatBytes(record.size_bytes)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                {/* Campaign Evaluation Card */}
                {selected.evaluation && (
                  <div className="border-t border-ink-800 pt-6">
                    <p className="eyebrow text-sodium-400">Campaign Evaluation</p>
                    <div className="mt-3 rounded border border-ink-800 bg-ink-850 p-3 text-xs space-y-2.5">
                      <div className="flex items-center justify-between">
                        <span className="text-ink-400">Composite Score</span>
                        <span className="font-display text-sm text-sodium-400">
                          {selected.evaluation.composite_score ?? selected.evaluation.final_score}/100
                        </span>
                      </div>
                      <div className="grid grid-cols-3 gap-1 text-[11px] text-ink-500 border-y border-ink-800 py-1.5">
                        <div>
                          Align: <span className="text-ink-200">{selected.evaluation.campaign_alignment_score ?? selected.evaluation.density_score ?? 0}</span>
                        </div>
                        <div>
                          Viral: <span className="text-ink-200">{selected.evaluation.viral_potential_score ?? selected.evaluation.viral_score ?? 0}</span>
                        </div>
                        <div>
                          Hook: <span className="text-ink-200">{selected.evaluation.hook_rating_score ?? selected.evaluation.hook_score ?? 0}</span>
                        </div>
                      </div>

                      {selected.evaluation.matched_hooks && selected.evaluation.matched_hooks.length > 0 && (
                        <div>
                          <p className="text-[10px] text-ink-500 uppercase tracking-wider">Matched Hooks</p>
                          <div className="mt-1 flex flex-wrap gap-1">
                            {selected.evaluation.matched_hooks.map((h: string, i: number) => (
                              <span
                                key={i}
                                className="rounded bg-emerald-500/10 border border-emerald-500/20 px-1.5 py-0.5 text-[10px] text-emerald-400"
                              >
                                ✓ {h}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {(selected.evaluation.reasoning || selected.reason) && (
                        <p className="text-[11px] text-ink-400 italic pt-1">
                          "{selected.evaluation.reasoning || selected.reason}"
                        </p>
                      )}
                    </div>
                  </div>
                )}

                {/* Step 23: SEO & Publishing Metadata Card */}
                {metadata && (
                  <div className="border-t border-ink-800 pt-6">
                    <div className="flex items-center justify-between">
                      <p className="eyebrow text-emerald-400">SEO & Publishing Metadata</p>
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] font-mono text-ink-500">v{metadata.version}</span>
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] font-mono font-semibold uppercase border ${
                            metadata.compliance_status === 'SEO_PASS'
                              ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30'
                              : metadata.compliance_status === 'SEO_WARN'
                              ? 'bg-amber-500/20 text-amber-300 border-amber-500/30'
                              : 'bg-rose-500/20 text-rose-300 border-rose-500/30'
                          }`}
                        >
                          {metadata.compliance_status} ({Math.round(metadata.compliance_score)})
                        </span>
                      </div>
                    </div>

                    <div className="mt-3 rounded border border-ink-800 bg-ink-850 p-4 text-xs space-y-3.5">
                      {metadataNotice && (
                        <div className="p-2 rounded bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 text-[11px]">
                          {metadataNotice}
                        </div>
                      )}

                      {metadata.validation_errors && metadata.validation_errors.length > 0 && (
                        <div className="p-2 rounded bg-rose-500/10 border border-rose-500/30 text-rose-300 text-[11px] space-y-1">
                          <p className="font-semibold">Compliance Violations (Publishing Blocked):</p>
                          <ul className="list-disc pl-4 space-y-0.5">
                            {metadata.validation_errors.map((err, i) => (
                              <li key={i}>{err}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {metadata.validation_warnings && metadata.validation_warnings.length > 0 && (
                        <div className="p-2 rounded bg-amber-500/10 border border-amber-500/30 text-amber-300 text-[11px] space-y-1">
                          <p className="font-semibold">Compliance Warnings:</p>
                          <ul className="list-disc pl-4 space-y-0.5">
                            {metadata.validation_warnings.map((warn, i) => (
                              <li key={i}>{warn}</li>
                            ))}
                          </ul>
                        </div>
                      )}

                      <div>
                        <div className="flex justify-between text-[11px] text-ink-400 mb-1">
                          <span>Final Title</span>
                          <span className={metadataTitle.length > 100 ? 'text-rose-400' : 'text-ink-500'}>
                            {metadataTitle.length}/100
                          </span>
                        </div>
                        <input
                          type="text"
                          value={metadataTitle}
                          onChange={(e) => setMetadataTitle(e.target.value)}
                          className="w-full rounded border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-xs text-ink-100 focus:border-emerald-500 focus:outline-none font-sans"
                          placeholder="Publishing title..."
                        />
                      </div>

                      <div>
                        <div className="flex justify-between text-[11px] text-ink-400 mb-1">
                          <span>Final Description</span>
                          <span className={metadataDesc.length > 2000 ? 'text-rose-400' : 'text-ink-500'}>
                            {metadataDesc.length}/2000
                          </span>
                        </div>
                        <textarea
                          rows={3}
                          value={metadataDesc}
                          onChange={(e) => setMetadataDesc(e.target.value)}
                          className="w-full rounded border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-xs text-ink-100 focus:border-emerald-500 focus:outline-none font-sans resize-y"
                          placeholder="Publishing description..."
                        />
                      </div>

                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <span className="text-[11px] text-ink-400 block mb-1">Hashtags</span>
                          <input
                            type="text"
                            value={metadataHashtags}
                            onChange={(e) => setMetadataHashtags(e.target.value)}
                            className="w-full rounded border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-xs text-ink-100 focus:border-emerald-500 focus:outline-none font-mono"
                            placeholder="#ALAMR #Shorts"
                          />
                        </div>
                        <div>
                          <span className="text-[11px] text-ink-400 block mb-1">Mentions</span>
                          <input
                            type="text"
                            value={metadataMentions}
                            onChange={(e) => setMetadataMentions(e.target.value)}
                            className="w-full rounded border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-xs text-ink-100 focus:border-emerald-500 focus:outline-none font-mono"
                            placeholder="@alamr"
                          />
                        </div>
                      </div>

                      <div>
                        <span className="text-[11px] text-ink-400 block mb-1">Call to Action (CTA)</span>
                        <input
                          type="text"
                          value={metadataCta}
                          onChange={(e) => setMetadataCta(e.target.value)}
                          className="w-full rounded border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-xs text-ink-100 focus:border-emerald-500 focus:outline-none font-sans"
                          placeholder="Subscribe for more insights..."
                        />
                      </div>

                      <div className="flex items-center justify-between pt-2 border-t border-ink-800">
                        <button
                          type="button"
                          onClick={resetMetadata}
                          disabled={savingMetadata}
                          className="text-[11px] text-ink-400 hover:text-ink-200 underline"
                        >
                          Reset to generated
                        </button>
                        <button
                          type="button"
                          onClick={saveMetadata}
                          disabled={savingMetadata}
                          className="btn btn-primary text-xs py-1 px-3"
                        >
                          {savingMetadata ? 'Saving…' : 'Save Metadata'}
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </>
            )}
          </section>

          {/* Step 24: Operator Approval Card */}
          {selected && (
            <section className="border-t border-ink-800 pt-6">
              <div className="flex items-center justify-between mb-3">
                <p className="eyebrow text-violet-400">Operator Approval (Step 24)</p>
                {approval && (
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-mono text-ink-500">v{approval.version}</span>
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] font-mono font-semibold uppercase border ${
                        approval.current_status === 'APPROVED'
                          ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30'
                          : approval.current_status === 'REJECTED'
                          ? 'bg-rose-500/20 text-rose-300 border-rose-500/30'
                          : approval.current_status === 'CHANGES_REQUESTED'
                          ? 'bg-amber-500/20 text-amber-300 border-amber-500/30'
                          : approval.current_status === 'PUBLISHING_LOCKED'
                          ? 'bg-orange-500/20 text-orange-300 border-orange-500/30'
                          : 'bg-ink-700/60 text-ink-300 border-ink-600'
                      }`}
                    >
                      {approval.current_status.replace(/_/g, ' ')}
                    </span>
                  </div>
                )}
              </div>

              <div className="rounded border border-ink-800 bg-ink-850 p-4 text-xs space-y-3.5">
                {approvalNotice && (
                  <div className="p-2 rounded bg-violet-500/10 border border-violet-500/20 text-violet-300 text-[11px]">
                    {approvalNotice}
                  </div>
                )}

                {/* Blocking reasons */}
                {approval && approval.blocking_reasons && approval.blocking_reasons.length > 0 && (
                  <div className="p-2 rounded bg-rose-500/10 border border-rose-500/30 text-rose-300 text-[11px] space-y-1">
                    <p className="font-semibold">Publish-Readiness Blocking Conditions:</p>
                    <ul className="list-disc pl-4 space-y-0.5">
                      {approval.blocking_reasons.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Eligibility indicator */}
                {approval && (
                  <div className="flex items-center gap-2 text-[11px]">
                    <span className={`w-2 h-2 rounded-full ${approval.publish_eligible ? 'bg-emerald-400' : 'bg-rose-400'}`} />
                    <span className={approval.publish_eligible ? 'text-emerald-300' : 'text-rose-300'}>
                      {approval.publish_eligible ? 'Publish-eligible (Steps 22+23 passed)' : 'Not publish-eligible'}
                    </span>
                    {approval.is_approved_for_publishing && (
                      <span className="ml-auto text-emerald-400 font-semibold">✓ Ready for Step 25 publishing</span>
                    )}
                  </div>
                )}

                {/* Operator note input */}
                <div>
                  <span className="text-[11px] text-ink-400 block mb-1">
                    Operator Note <span className="text-ink-600">(required for Reject / Request Changes)</span>
                  </span>
                  <textarea
                    rows={2}
                    value={approvalNote}
                    onChange={(e) => setApprovalNote(e.target.value)}
                    disabled={approvalLoading}
                    className="w-full rounded border border-ink-700 bg-ink-900 px-2.5 py-1.5 text-xs text-ink-100 focus:border-violet-500 focus:outline-none font-sans resize-y"
                    placeholder="Reason for rejection or requested changes..."
                  />
                </div>

                {/* Action buttons */}
                <div className="flex flex-wrap gap-2 pt-1 border-t border-ink-800">
                  <button
                    type="button"
                    onClick={() => doApprovalAction('APPROVE', approvalNote)}
                    disabled={approvalLoading || approval?.current_status === 'APPROVED'}
                    className="btn btn-primary text-xs py-1 px-3 disabled:opacity-40"
                  >
                    ✓ Approve
                  </button>
                  <button
                    type="button"
                    onClick={() => doApprovalAction('REJECT', approvalNote)}
                    disabled={approvalLoading || !approvalNote.trim()}
                    className="rounded border border-rose-700 bg-rose-950/60 px-3 py-1 text-xs text-rose-300 hover:bg-rose-900/60 disabled:opacity-40"
                  >
                    ✕ Reject
                  </button>
                  <button
                    type="button"
                    onClick={() => doApprovalAction('REQUEST_CHANGES', approvalNote)}
                    disabled={approvalLoading || !approvalNote.trim()}
                    className="rounded border border-amber-700 bg-amber-950/60 px-3 py-1 text-xs text-amber-300 hover:bg-amber-900/60 disabled:opacity-40"
                  >
                    ⟳ Request Changes
                  </button>
                  <button
                    type="button"
                    onClick={resetApproval}
                    disabled={approvalLoading || approval?.current_status === 'PENDING_REVIEW'}
                    className="ml-auto text-[11px] text-ink-400 hover:text-ink-200 underline disabled:opacity-40"
                  >
                    Reset to Pending
                  </button>
                </div>

                {/* Audit history */}
                {approval && approval.history && approval.history.length > 0 && (
                  <details className="mt-2">
                    <summary className="text-[11px] text-ink-500 cursor-pointer hover:text-ink-300">
                      Approval history ({approval.history.length} entries)
                    </summary>
                    <div className="mt-2 space-y-1.5 max-h-40 overflow-y-auto">
                      {[...approval.history].reverse().map((entry, i) => (
                        <div key={i} className="flex items-start gap-2 text-[10px] font-mono text-ink-400 bg-ink-900/60 rounded px-2 py-1.5">
                          <span className={`font-semibold shrink-0 ${entry.to_status === 'APPROVED' ? 'text-emerald-400' : entry.to_status === 'REJECTED' ? 'text-rose-400' : 'text-amber-400'}`}>
                            {entry.from_status} → {entry.to_status}
                          </span>
                          <span className="text-ink-500 shrink-0">v{entry.version}</span>
                          {entry.operator_note && (
                            <span className="text-ink-400 italic truncate" title={entry.operator_note}>
                              "{entry.operator_note}"
                            </span>
                          )}
                          <span className="ml-auto text-ink-600 shrink-0">{entry.timestamp?.slice(0, 19).replace('T', ' ')}</span>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
              </div>
            </section>
          )}

          {/* Step 25: Remote Publishing Section */}
          {selected && (
            <section className="border-t border-ink-800 pt-6">
              <div className="flex items-center justify-between mb-3">
                <p className="eyebrow text-sky-400">Remote Publishing (Step 25)</p>
                <div className="flex items-center gap-2">
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-mono font-semibold uppercase border ${
                      approval?.current_status === 'APPROVED'
                        ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30'
                        : 'bg-rose-500/20 text-rose-300 border-rose-500/30'
                    }`}
                  >
                    {approval?.current_status === 'APPROVED' ? 'Gate Passed' : 'Approval Gate Required'}
                  </span>
                </div>
              </div>

              <div className="rounded border border-ink-800 bg-ink-850 p-4 text-xs space-y-3.5">
                {publishingNotice && (
                  <div className="p-2 rounded bg-sky-500/10 border border-sky-500/20 text-sky-300 text-[11px]">
                    {publishingNotice}
                  </div>
                )}

                {/* Destination selectors */}
                <div>
                  <span className="text-[11px] text-ink-400 block mb-2 font-medium">
                    Target Publishing Destinations:
                  </span>
                  <div className="flex flex-wrap gap-4">
                    <label className="flex items-center gap-2 cursor-pointer text-ink-200 hover:text-white">
                      <input
                        type="checkbox"
                        checked={selectedPlatforms.includes('youtube')}
                        onChange={() => togglePlatform('youtube')}
                        className="rounded border-ink-700 bg-ink-900 text-sky-500 focus:ring-0"
                      />
                      <span>YouTube Shorts</span>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer text-ink-200 hover:text-white">
                      <input
                        type="checkbox"
                        checked={selectedPlatforms.includes('instagram')}
                        onChange={() => togglePlatform('instagram')}
                        className="rounded border-ink-700 bg-ink-900 text-sky-500 focus:ring-0"
                      />
                      <span>Instagram Reels</span>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer text-ink-200 hover:text-white">
                      <input
                        type="checkbox"
                        checked={selectedPlatforms.includes('telegram')}
                        onChange={() => togglePlatform('telegram')}
                        className="rounded border-ink-700 bg-ink-900 text-sky-500 focus:ring-0"
                      />
                      <span>Telegram Channel</span>
                    </label>
                  </div>
                </div>

                {/* Existing Publications List for this Clip */}
                {(() => {
                  const clipPubs = publications.filter((p) => p.clip_id === selected.id)
                  if (clipPubs.length === 0) return null
                  return (
                    <div className="border-t border-ink-800/80 pt-3 space-y-2">
                      <p className="text-[11px] font-semibold text-ink-300">Publication Status:</p>
                      <div className="space-y-1.5">
                        {clipPubs.map((pub) => (
                          <div
                            key={pub.id}
                            className="flex items-center justify-between bg-ink-900/80 border border-ink-800 rounded p-2.5"
                          >
                            <div className="space-y-0.5">
                              <div className="flex items-center gap-2">
                                <span className="font-semibold text-ink-100 uppercase text-[10px] tracking-wide">
                                  {pub.platform}
                                </span>
                                <span
                                  className={`rounded px-1.5 py-0.5 text-[9px] font-mono font-bold uppercase border ${
                                    pub.status === 'PUBLISHED'
                                      ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30'
                                      : pub.status === 'UPLOADING'
                                      ? 'bg-sky-500/20 text-sky-300 border-sky-500/30'
                                      : pub.status === 'FAILED_RETRYABLE'
                                      ? 'bg-amber-500/20 text-amber-300 border-amber-500/30'
                                      : pub.status === 'FAILED_PERMANENT'
                                      ? 'bg-rose-500/20 text-rose-300 border-rose-500/30'
                                      : 'bg-ink-700 text-ink-300 border-ink-600'
                                  }`}
                                >
                                  {pub.status.replace(/_/g, ' ')}
                                </span>
                                <span className="text-ink-500 text-[10px]">
                                  Att: {pub.attempt_number}
                                </span>
                              </div>
                              {pub.error_message && (
                                <p className="text-[10px] text-rose-400 font-mono line-clamp-1" title={pub.error_message}>
                                  {pub.error_message}
                                </p>
                              )}
                              {pub.remote_post_id && (
                                <p className="text-[10px] text-ink-500 font-mono">
                                  Remote ID: {pub.remote_post_id}
                                </p>
                              )}
                            </div>

                            <div className="flex items-center gap-2">
                              {pub.permalink && (
                                <a
                                  href={pub.permalink}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-[11px] text-sky-400 hover:text-sky-300 underline font-medium"
                                >
                                  View Post ↗
                                </a>
                              )}
                              {pub.is_retryable && (
                                <button
                                  type="button"
                                  onClick={() => handleRetryPublication(pub.id)}
                                  disabled={publishingLoading}
                                  className="rounded border border-amber-600 bg-amber-950/60 px-2 py-0.5 text-[10px] text-amber-300 hover:bg-amber-900/60 disabled:opacity-40"
                                >
                                  Retry
                                </button>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )
                })()}

                {/* Publish actions */}
                <div className="flex items-center justify-between pt-2 border-t border-ink-800">
                  <div className="text-[11px] text-ink-500">
                    {approval?.current_status !== 'APPROVED' ? (
                      <span className="text-rose-400">
                        ⚠ Clip must be APPROVED in Step 24 before publishing.
                      </span>
                    ) : (
                      <span>✓ Quality and operator approval gates satisfied.</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => handlePublishClip(true)}
                      disabled={publishingLoading || approval?.current_status !== 'APPROVED' || selectedPlatforms.length === 0}
                      className="rounded border border-ink-700 bg-ink-800 px-3 py-1 text-xs text-ink-300 hover:bg-ink-700 disabled:opacity-40"
                    >
                      Verify Dry-Run
                    </button>
                    <button
                      type="button"
                      onClick={() => handlePublishClip(false)}
                      disabled={publishingLoading || approval?.current_status !== 'APPROVED' || selectedPlatforms.length === 0}
                      className="btn btn-primary text-xs py-1 px-3 disabled:opacity-40"
                    >
                      {publishingLoading ? 'Publishing…' : 'Publish Selected'}
                    </button>
                  </div>
                </div>
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  )
}

function ClipRow({
  clip,
  index,
  selected,
  onSelect,
  onKeep,
  onDiscard,
}: {
  clip: Clip
  index: number
  selected: boolean
  onSelect: () => void
  onKeep: () => void
  onDiscard: () => void
}) {
  return (
    <div
      className={[
        'rise group grid cursor-pointer grid-cols-[2.25rem_1fr] gap-x-3 border-b border-ink-850 py-3 pl-2 transition-colors duration-200',
        selected ? 'bg-ink-850/70' : 'hover:bg-ink-850/35',
        clip.status === 'discarded' ? 'opacity-40' : '',
      ].join(' ')}
      style={{ animationDelay: `${Math.min(index, 10) * 35}ms` }}
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter') onSelect()
      }}
    >
      {/* The score is the thing you scan by, so it gets the display face. */}
      <span
        className={[
          'numeric self-start font-display text-2xl leading-none',
          clip.score >= 85 ? 'text-sodium-500' : selected ? 'text-ink-200' : 'text-ink-500',
        ].join(' ')}
      >
        {clip.score}
      </span>

      <div className="min-w-0">
        <p className="truncate text-[0.9375rem] leading-snug text-ink-100">
          {clip.title || 'Untitled clip'}
        </p>
        <div className="mt-1 flex items-baseline gap-3">
          <span className="numeric text-xs text-ink-500">{formatDuration(clip.duration_s)}</span>
          {clip.user_trimmed && <span className="text-xs text-ink-600">trimmed</span>}
          {clip.exports.length > 0 && (
            <span className="text-xs text-signal-good">exported</span>
          )}

          {/* Secondary actions appear on hover or when the row is current —
              progressive disclosure keeps the scan column clean. */}
          <span
            className={[
              'ml-auto flex gap-3 pr-2 transition-opacity duration-200',
              selected ? 'opacity-100' : 'opacity-0 group-hover:opacity-100',
            ].join(' ')}
          >
            <button
              onClick={(e) => {
                e.stopPropagation()
                onKeep()
              }}
              className={`text-xs ${clip.status === 'kept' ? 'text-signal-good' : 'text-ink-400 hover:text-ink-100'}`}
            >
              {clip.status === 'kept' ? 'kept' : 'keep'}
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation()
                onDiscard()
              }}
              className="text-xs text-ink-400 hover:text-signal-bad"
            >
              {clip.status === 'discarded' ? 'undo' : 'drop'}
            </button>
          </span>
        </div>
      </div>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="max-w-xl pt-20">
      <h2 className="font-display text-3xl text-ink-200">No clips came back.</h2>
      <p className="mt-4 text-sm leading-relaxed text-ink-400">
        The model found nothing self-contained enough to stand alone — which is a real
        answer for some source material, not necessarily a failure.
      </p>
      <p className="mt-3 text-sm leading-relaxed text-ink-400">
        If you expected clips, try a larger provider model, or widen the length range so
        shorter moments qualify.
      </p>
    </div>
  )
}
