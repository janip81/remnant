import { useRef, useState } from 'react'
import Box from '@mui/material/Box'
import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
import Chip from '@mui/material/Chip'
import IconButton from '@mui/material/IconButton'
import InputBase from '@mui/material/InputBase'
import TextField from '@mui/material/TextField'
import Tooltip from '@mui/material/Tooltip'
import DeleteIcon from '@mui/icons-material/DeleteOutline'
import EditIcon from '@mui/icons-material/EditOutlined'
import LabelIcon from '@mui/icons-material/LabelOutlined'
import CheckIcon from '@mui/icons-material/Check'
import CloseIcon from '@mui/icons-material/Close'
import { Memory } from '../api/memories'

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
  })
}

interface Props {
  memory: Memory
  onDelete: (id: string) => void
  onUpdate: (id: string, content: string) => void
  onUpdateTags: (id: string, tags: string[]) => void
}

export default function MemoryCard({ memory, onDelete, onUpdate, onUpdateTags }: Props) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(memory.memory)

  const [editingTags, setEditingTags] = useState(false)
  const [draftTags, setDraftTags] = useState<string[]>(memory.tags ?? [])
  const [tagInput, setTagInput] = useState('')
  const tagInputRef = useRef<HTMLInputElement>(null)

  function handleSave() {
    if (draft.trim() && draft !== memory.memory) onUpdate(memory.id, draft.trim())
    setEditing(false)
  }

  function handleCancel() {
    setDraft(memory.memory)
    setEditing(false)
  }

  function handleTagsEdit() {
    setDraftTags(memory.tags ?? [])
    setEditingTags(true)
    setTimeout(() => tagInputRef.current?.focus(), 50)
  }

  function handleTagsSave() {
    onUpdateTags(memory.id, draftTags)
    setEditingTags(false)
    setTagInput('')
  }

  function handleTagsCancel() {
    setDraftTags(memory.tags ?? [])
    setEditingTags(false)
    setTagInput('')
  }

  function addTag(value: string) {
    const tag = value.trim().toLowerCase().replace(/\s+/g, '-')
    if (tag && !draftTags.includes(tag)) {
      setDraftTags(prev => [...prev, tag])
    }
    setTagInput('')
  }

  function removeTag(tag: string) {
    setDraftTags(prev => prev.filter(t => t !== tag))
  }

  function handleTagKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      addTag(tagInput)
    } else if (e.key === 'Backspace' && !tagInput && draftTags.length > 0) {
      setDraftTags(prev => prev.slice(0, -1))
    } else if (e.key === 'Escape') {
      handleTagsCancel()
    }
  }

  return (
    <Card variant="outlined">
      <CardContent sx={{ pb: '12px !important', pt: 1.5, px: 2 }}>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1 }}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            {/* Content */}
            {editing ? (
              <TextField
                multiline
                fullWidth
                size="small"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                autoFocus
                inputProps={{ style: { fontFamily: 'monospace', fontSize: 13 } }}
              />
            ) : (
              <Box
                component="pre"
                sx={{
                  fontFamily: 'monospace',
                  fontSize: 12.5,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  m: 0,
                  color: 'text.primary',
                }}
              >
                {memory.memory}
              </Box>
            )}

            {/* Chips row */}
            <Box sx={{ display: 'flex', gap: 0.5, mt: 1, flexWrap: 'wrap', alignItems: 'center' }}>
              <Chip label={fmtDate(memory.created_at)} size="small" variant="outlined" sx={{ fontSize: 11 }} />
              {memory.category && (
                <Chip label={memory.category} size="small" color="secondary" variant="outlined" sx={{ fontSize: 11 }} />
              )}
              {memory.agent_id && (
                <Chip label={memory.agent_id} size="small" color="primary" variant="outlined" sx={{ fontSize: 11, fontFamily: 'monospace' }} />
              )}
              {memory.score != null && (
                <Chip label={`score ${memory.score.toFixed(3)}`} size="small" sx={{ fontSize: 11, fontFamily: 'monospace' }} />
              )}

              {/* Tags — view mode */}
              {!editingTags && (memory.tags ?? []).map(tag => (
                <Chip
                  key={tag}
                  label={tag}
                  size="small"
                  color="success"
                  variant="outlined"
                  sx={{ fontSize: 11 }}
                />
              ))}

              {/* Tags — edit mode */}
              {editingTags && (
                <Box sx={{
                  display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 0.5,
                  border: '1px solid', borderColor: 'divider', borderRadius: 1,
                  px: 0.75, py: 0.25, flex: '1 1 200px', minWidth: 0,
                }}>
                  {draftTags.map(tag => (
                    <Chip
                      key={tag}
                      label={tag}
                      size="small"
                      color="success"
                      variant="outlined"
                      onDelete={() => removeTag(tag)}
                      sx={{ fontSize: 11 }}
                    />
                  ))}
                  <InputBase
                    inputRef={tagInputRef}
                    value={tagInput}
                    onChange={(e) => setTagInput(e.target.value)}
                    onKeyDown={handleTagKeyDown}
                    onBlur={() => { if (tagInput.trim()) addTag(tagInput) }}
                    placeholder={draftTags.length === 0 ? 'prod-k8s, remnant, upgrade…' : 'add tag…'}
                    sx={{ fontSize: 12, flex: 1, minWidth: 80 }}
                  />
                </Box>
              )}
            </Box>
          </Box>

          {/* Action buttons */}
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, flexShrink: 0 }}>
            {editing ? (
              <>
                <Tooltip title="Save"><IconButton size="small" onClick={handleSave} color="primary"><CheckIcon fontSize="small" /></IconButton></Tooltip>
                <Tooltip title="Cancel"><IconButton size="small" onClick={handleCancel}><CloseIcon fontSize="small" /></IconButton></Tooltip>
              </>
            ) : editingTags ? (
              <>
                <Tooltip title="Save tags"><IconButton size="small" onClick={handleTagsSave} color="success"><CheckIcon fontSize="small" /></IconButton></Tooltip>
                <Tooltip title="Cancel"><IconButton size="small" onClick={handleTagsCancel}><CloseIcon fontSize="small" /></IconButton></Tooltip>
              </>
            ) : (
              <>
                <Tooltip title="Edit content"><IconButton size="small" onClick={() => setEditing(true)}><EditIcon fontSize="small" /></IconButton></Tooltip>
                <Tooltip title="Edit tags"><IconButton size="small" onClick={handleTagsEdit} color="success"><LabelIcon fontSize="small" /></IconButton></Tooltip>
                <Tooltip title="Delete"><IconButton size="small" onClick={() => onDelete(memory.id)} color="error"><DeleteIcon fontSize="small" /></IconButton></Tooltip>
              </>
            )}
          </Box>
        </Box>
      </CardContent>
    </Card>
  )
}
