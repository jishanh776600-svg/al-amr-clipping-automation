import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { api, type CampaignPreset, type CampaignBrief } from '../api'
import { ErrorNote } from '../components/ErrorNote'

const DEFAULT_BRIEF: CampaignBrief = {
  name: '',
  description: '',
  topic_context: '',
  target_audience: '',
  required_topics: [],
  required_concepts: [],
  optional_keywords: [],
  banned_words: [],
  banned_topics: [],
  minimum_duration: 20,
  maximum_duration: 90,
  hook_required: true,
  hook_window_seconds: 2.5,
  minimum_hook_score: 6.0,
  hook_types: [],
  output_count: 5,
}

export function CampaignBuilder() {
  const navigate = useNavigate()
  const [campaigns, setCampaigns] = useState<CampaignPreset[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [presetName, setPresetName] = useState('')
  const [brief, setBrief] = useState<CampaignBrief>({ ...DEFAULT_BRIEF })
  const [saving, setSaving] = useState(false)
  const [successMsg, setSuccessMsg] = useState('')

  // Tag inputs
  const [topicInput, setTopicInput] = useState('')
  const [hookTypeInput, setHookTypeInput] = useState('')
  const [bannedTopicInput, setBannedTopicInput] = useState('')
  const [keywordInput, setKeywordInput] = useState('')

  const loadCampaigns = () => {
    setLoading(true)
    api
      .listCampaigns()
      .then((data) => {
        setCampaigns(data)
        setError(null)
      })
      .catch((err) => setError(err as Error))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadCampaigns()
  }, [])

  const startNew = () => {
    setSelectedId(null)
    setPresetName('')
    setBrief({ ...DEFAULT_BRIEF })
    setSuccessMsg('')
    setError(null)
  }

  const selectCampaign = (c: CampaignPreset) => {
    setSelectedId(c.id)
    setPresetName(c.name)
    setBrief({
      ...DEFAULT_BRIEF,
      ...(c.brief || {}),
      name: c.brief?.name || c.name,
    })
    setSuccessMsg('')
    setError(null)
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!presetName.trim()) {
      setError(new Error('Campaign preset name is required.'))
      return
    }

    setSaving(true)
    setError(null)
    try {
      const payloadBrief: CampaignBrief = {
        ...brief,
        name: presetName.trim(),
      }
      const saved = await api.saveCampaign({
        id: selectedId || undefined,
        name: presetName.trim(),
        brief: payloadBrief,
      })
      setSuccessMsg(`Campaign "${saved.name}" saved successfully!`)
      setTimeout(() => setSuccessMsg(''), 3000)
      setSelectedId(saved.id)
      loadCampaigns()
    } catch (err) {
      setError(err as Error)
    } finally {
      setSaving(false)
    }
  }

  const removeCampaign = async (id: string, name: string) => {
    if (!confirm(`Are you sure you want to delete campaign "${name}"?`)) return
    try {
      await api.deleteCampaign(id)
      if (selectedId === id) {
        startNew()
      }
      loadCampaigns()
    } catch (err) {
      setError(err as Error)
    }
  }

  const addTag = (
    field: keyof CampaignBrief,
    val: string,
    setter: (s: string) => void,
  ) => {
    const clean = val.trim()
    if (!clean) return
    const current = (brief[field] as string[]) || []
    if (!current.includes(clean)) {
      setBrief({ ...brief, [field]: [...current, clean] })
    }
    setter('')
  }

  const removeTag = (field: keyof CampaignBrief, index: number) => {
    const current = (brief[field] as string[]) || []
    setBrief({
      ...brief,
      [field]: current.filter((_, i) => i !== index),
    })
  }

  return (
    <div className="pt-10">
      <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-ink-800 pb-5">
        <div>
          <p className="eyebrow">Strategy & Rules</p>
          <h1 className="mt-1 font-display text-[clamp(1.75rem,3vw,2.5rem)] leading-none text-ink-100">
            Campaign Builder
          </h1>
        </div>

        <div className="flex items-center gap-3">
          <button onClick={startNew} className="btn btn-quiet text-xs">
            + New Campaign
          </button>
          {selectedId && (
            <button
              onClick={() => navigate(`/new?campaign=${selectedId}`)}
              className="btn btn-primary text-xs"
            >
              Use in New Ingest →
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="mt-6 max-w-4xl">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {successMsg && (
        <div className="mt-4 max-w-4xl rounded border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs text-emerald-400">
          ✓ {successMsg}
        </div>
      )}

      <div className="mt-8 grid gap-8 lg:grid-cols-[320px_1fr]">
        {/* Left column: Saved campaigns */}
        <div>
          <p className="eyebrow border-b border-ink-800 pb-2">Saved Campaigns</p>
          {loading ? (
            <p className="mt-4 text-xs text-ink-500">Loading presets…</p>
          ) : campaigns.length === 0 ? (
            <p className="mt-4 text-xs text-ink-500">
              No saved campaign presets yet. Create one on the right to enforce custom rules and hooks.
            </p>
          ) : (
            <div className="mt-3 space-y-2">
              {campaigns.map((c) => (
                <div
                  key={c.id}
                  onClick={() => selectCampaign(c)}
                  className={[
                    'cursor-pointer rounded border p-3 transition text-xs',
                    selectedId === c.id
                      ? 'border-sodium-500 bg-ink-850'
                      : 'border-ink-800 bg-ink-900/60 hover:border-ink-700 hover:bg-ink-850/50',
                  ].join(' ')}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="font-semibold text-ink-200 truncate">{c.name}</span>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation()
                        removeCampaign(c.id, c.name)
                      }}
                      className="text-ink-600 hover:text-red-400 px-1"
                      title="Delete Campaign"
                    >
                      ✕
                    </button>
                  </div>
                  {c.brief?.target_audience && (
                    <p className="mt-1 text-ink-500 text-[11px] truncate">
                      Audience: {c.brief.target_audience}
                    </p>
                  )}
                  <div className="mt-2 flex items-center gap-2 text-[10px] text-ink-600">
                    <span>{c.brief?.hook_types?.length || 0} hook types</span>
                    <span>·</span>
                    <span>{c.brief?.required_topics?.length || 0} topics</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right column: Editor */}
        <div className="rounded-lg border border-ink-800 bg-ink-850/40 p-6">
          <form onSubmit={save} className="space-y-6">
            <div>
              <label className="eyebrow">Campaign Preset Name *</label>
              <input
                type="text"
                value={presetName}
                onChange={(e) => setPresetName(e.target.value)}
                placeholder="e.g. Al Amr Q4 Brand Awareness"
                required
                className="field mt-1.5 text-base"
              />
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="eyebrow">Target Audience</label>
                <input
                  type="text"
                  value={brief.target_audience || ''}
                  onChange={(e) => setBrief({ ...brief, target_audience: e.target.value })}
                  placeholder="e.g. Arabic entrepreneurs & founders"
                  className="field mt-1.5 text-xs"
                />
              </div>

              <div>
                <label className="eyebrow">Topic Context</label>
                <input
                  type="text"
                  value={brief.topic_context || ''}
                  onChange={(e) => setBrief({ ...brief, topic_context: e.target.value })}
                  placeholder="e.g. Business scaling and operational efficiency"
                  className="field mt-1.5 text-xs"
                />
              </div>
            </div>

            <div>
              <label className="eyebrow">Brief Description / Operator Notes</label>
              <textarea
                value={brief.description || ''}
                onChange={(e) => setBrief({ ...brief, description: e.target.value })}
                rows={2}
                placeholder="Operational brief or client guidelines for the clipping engine…"
                className="field mt-1.5 text-xs"
              />
            </div>

            {/* Hook Types */}
            <div>
              <label className="eyebrow">Hook Types & Requirements</label>
              <div className="mt-1.5 flex gap-2">
                <input
                  type="text"
                  value={hookTypeInput}
                  onChange={(e) => setHookTypeInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      addTag('hook_types', hookTypeInput, setHookTypeInput)
                    }
                  }}
                  placeholder="Type a hook type and press Enter (e.g. Question, Controversial statement, Bold claim)"
                  className="field text-xs"
                />
                <button
                  type="button"
                  onClick={() => addTag('hook_types', hookTypeInput, setHookTypeInput)}
                  className="btn btn-quiet text-xs shrink-0"
                >
                  Add
                </button>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(brief.hook_types || []).map((hook, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center gap-1 rounded bg-sodium-500/10 border border-sodium-500/30 px-2 py-0.5 text-xs text-sodium-300"
                  >
                    {hook}
                    <button
                      type="button"
                      onClick={() => removeTag('hook_types', i)}
                      className="hover:text-ink-100"
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            </div>

            {/* Required Topics */}
            <div>
              <label className="eyebrow">Required Topics (Mandatory Content)</label>
              <div className="mt-1.5 flex gap-2">
                <input
                  type="text"
                  value={topicInput}
                  onChange={(e) => setTopicInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      addTag('required_topics', topicInput, setTopicInput)
                    }
                  }}
                  placeholder="Type topic and press Enter (e.g. Workflow optimization, Real estate strategy)"
                  className="field text-xs"
                />
                <button
                  type="button"
                  onClick={() => addTag('required_topics', topicInput, setTopicInput)}
                  className="btn btn-quiet text-xs shrink-0"
                >
                  Add
                </button>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(brief.required_topics || []).map((topic, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center gap-1 rounded bg-ink-800 border border-ink-700 px-2 py-0.5 text-xs text-ink-200"
                  >
                    {topic}
                    <button
                      type="button"
                      onClick={() => removeTag('required_topics', i)}
                      className="hover:text-ink-100"
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            </div>

            {/* Banned Topics */}
            <div>
              <label className="eyebrow text-red-400">Banned Topics (Hard Disqualification)</label>
              <div className="mt-1.5 flex gap-2">
                <input
                  type="text"
                  value={bannedTopicInput}
                  onChange={(e) => setBannedTopicInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      addTag('banned_topics', bannedTopicInput, setBannedTopicInput)
                    }
                  }}
                  placeholder="Type banned topic and press Enter (e.g. Political arguments, Unverified claims)"
                  className="field text-xs"
                />
                <button
                  type="button"
                  onClick={() => addTag('banned_topics', bannedTopicInput, setBannedTopicInput)}
                  className="btn btn-quiet text-xs shrink-0"
                >
                  Add
                </button>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(brief.banned_topics || []).map((banned, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center gap-1 rounded bg-red-500/10 border border-red-500/30 px-2 py-0.5 text-xs text-red-300"
                  >
                    {banned}
                    <button
                      type="button"
                      onClick={() => removeTag('banned_topics', i)}
                      className="hover:text-ink-100"
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            </div>

            {/* Optional / Priority Keywords */}
            <div>
              <label className="eyebrow">Priority Keywords</label>
              <div className="mt-1.5 flex gap-2">
                <input
                  type="text"
                  value={keywordInput}
                  onChange={(e) => setKeywordInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      addTag('optional_keywords', keywordInput, setKeywordInput)
                    }
                  }}
                  placeholder="Type keyword and press Enter (e.g. ROI, Profit, Scale)"
                  className="field text-xs"
                />
                <button
                  type="button"
                  onClick={() => addTag('optional_keywords', keywordInput, setKeywordInput)}
                  className="btn btn-quiet text-xs shrink-0"
                >
                  Add
                </button>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(brief.optional_keywords || []).map((kw, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center gap-1 rounded bg-ink-800 border border-ink-700 px-2 py-0.5 text-xs text-ink-300"
                  >
                    {kw}
                    <button
                      type="button"
                      onClick={() => removeTag('optional_keywords', i)}
                      className="hover:text-ink-100"
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            </div>

            {/* Durations & Target Counts */}
            <div className="grid grid-cols-3 gap-4 border-t border-ink-800 pt-5">
              <div>
                <label className="eyebrow">Min Duration (s)</label>
                <input
                  type="number"
                  min={5}
                  max={300}
                  value={brief.minimum_duration ?? 20}
                  onChange={(e) =>
                    setBrief({ ...brief, minimum_duration: Number(e.target.value) })
                  }
                  className="field mt-1 text-xs"
                />
              </div>

              <div>
                <label className="eyebrow">Max Duration (s)</label>
                <input
                  type="number"
                  min={5}
                  max={300}
                  value={brief.maximum_duration ?? 90}
                  onChange={(e) =>
                    setBrief({ ...brief, maximum_duration: Number(e.target.value) })
                  }
                  className="field mt-1 text-xs"
                />
              </div>

              <div>
                <label className="eyebrow">Output Clips Count</label>
                <input
                  type="number"
                  min={1}
                  max={30}
                  value={brief.output_count ?? 5}
                  onChange={(e) =>
                    setBrief({ ...brief, output_count: Number(e.target.value) })
                  }
                  className="field mt-1 text-xs"
                />
              </div>
            </div>

            <div className="flex items-center justify-end gap-4 border-t border-ink-800 pt-5">
              <button
                type="button"
                onClick={startNew}
                className="btn btn-quiet text-xs"
              >
                Reset
              </button>
              <button
                type="submit"
                disabled={saving}
                className="btn btn-primary text-xs"
              >
                {saving ? 'Saving…' : selectedId ? 'Update Campaign Preset' : 'Save Campaign Preset'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
