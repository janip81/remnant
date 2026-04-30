import { useState } from 'react'
import Box from '@mui/material/Box'
import Card from '@mui/material/Card'
import CardContent from '@mui/material/CardContent'
import Chip from '@mui/material/Chip'
import IconButton from '@mui/material/IconButton'
import TextField from '@mui/material/TextField'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import DeleteIcon from '@mui/icons-material/DeleteOutline'
import EditIcon from '@mui/icons-material/EditOutlined'
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
}

export default function MemoryCard({ memory, onDelete, onUpdate }: Props) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(memory.memory)

  function handleSave() {
    if (draft.trim() && draft !== memory.memory) {
      onUpdate(memory.id, draft.trim())
    }
    setEditing(false)
  }

  function handleCancel() {
    setDraft(memory.memory)
    setEditing(false)
  }

  return (
    <Card variant="outlined">
      <CardContent sx={{ pb: '12px !important', pt: 1.5, px: 2 }}>
        <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1 }}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            {editing ? (
              <TextField
                multiline
                fullWidth
                size="small"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                autoFocus
                sx={{ fontFamily: 'monospace' }}
                inputProps={{ style: { fontFamily: 'monospace', fontSize: 13 } }}
              />
            ) : (
              <Typography
                variant="body2"
                component="pre"
                sx={{
                  fontFamily: 'monospace',
                  fontSize: 12.5,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  m: 0,
                }}
              >
                {memory.memory}
              </Typography>
            )}

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
            </Box>
          </Box>

          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5, flexShrink: 0 }}>
            {editing ? (
              <>
                <Tooltip title="Save">
                  <IconButton size="small" onClick={handleSave} color="primary">
                    <CheckIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
                <Tooltip title="Cancel">
                  <IconButton size="small" onClick={handleCancel}>
                    <CloseIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </>
            ) : (
              <>
                <Tooltip title="Edit">
                  <IconButton size="small" onClick={() => setEditing(true)}>
                    <EditIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
                <Tooltip title="Delete">
                  <IconButton size="small" onClick={() => onDelete(memory.id)} color="error">
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </>
            )}
          </Box>
        </Box>
      </CardContent>
    </Card>
  )
}
