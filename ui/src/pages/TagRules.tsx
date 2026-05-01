import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import Chip from '@mui/material/Chip'
import CircularProgress from '@mui/material/CircularProgress'
import Collapse from '@mui/material/Collapse'
import Divider from '@mui/material/Divider'
import IconButton from '@mui/material/IconButton'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import AddIcon from '@mui/icons-material/Add'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import { tagRulesApi, TagRule } from '../api/tag-rules'

function SourceBadge({ source }: { source: string }) {
  const color = source === 'manual' ? 'primary' : source === 'seed' ? 'default' : 'warning'
  return <Chip label={source} size="small" color={color as any} variant="outlined" sx={{ fontSize: 10, height: 18 }} />
}

function TagGroup({ rule, onDeleteKeyword }: { rule: TagRule; onDeleteKeyword: (tag: string, kw: string) => void }) {
  const [expanded, setExpanded] = useState(true)
  return (
    <Box sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 1, px: 2, py: 1 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, cursor: 'pointer' }} onClick={() => setExpanded(v => !v)}>
        <Typography variant="body2" sx={{ fontWeight: 600, fontFamily: 'monospace', flex: 1 }}>
          {rule.tag}
        </Typography>
        <SourceBadge source={rule.source} />
        <Typography variant="caption" color="text.secondary">{rule.keywords.length} keywords</Typography>
        <IconButton size="small" sx={{ border: 'none !important', p: 0 }}>
          {expanded ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
        </IconButton>
      </Box>
      <Collapse in={expanded}>
        <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mt: 1 }}>
          {rule.keywords.map(kw => (
            <Chip
              key={kw}
              label={kw}
              size="small"
              variant="outlined"
              color="success"
              onDelete={() => onDeleteKeyword(rule.tag, kw)}
              sx={{ fontFamily: 'monospace', fontSize: 11 }}
            />
          ))}
        </Box>
      </Collapse>
    </Box>
  )
}

export default function TagRules() {
  const qc = useQueryClient()
  const [newTag, setNewTag] = useState('')
  const [newKeyword, setNewKeyword] = useState('')
  const [filterText, setFilterText] = useState('')

  const { data: rules = [], isLoading, isError } = useQuery({
    queryKey: ['tag-rules'],
    queryFn: tagRulesApi.list,
  })

  const addMutation = useMutation({
    mutationFn: ({ tag, keyword }: { tag: string; keyword: string }) =>
      tagRulesApi.add(tag, keyword),
    onSuccess: () => {
      setNewTag('')
      setNewKeyword('')
      qc.invalidateQueries({ queryKey: ['tag-rules'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: ({ tag, keyword }: { tag: string; keyword: string }) =>
      tagRulesApi.delete(tag, keyword),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tag-rules'] }),
  })

  const filtered = filterText
    ? rules.filter(r =>
        r.tag.includes(filterText.toLowerCase()) ||
        r.keywords.some(k => k.includes(filterText.toLowerCase()))
      )
    : rules

  return (
    <Box sx={{ maxWidth: 900, mx: 'auto', px: 2, py: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 1 }}>
        <Typography variant="h6" sx={{ fontWeight: 700 }}>Tag Rules</Typography>
        <Typography variant="caption" color="text.secondary">
          {rules.length} tags · keyword matching for auto-tagging at write time
        </Typography>
      </Box>

      <Divider sx={{ mb: 2 }} />

      {/* Add rule form */}
      <Box sx={{ display: 'flex', gap: 1, mb: 2, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <TextField
          label="Tag"
          size="small"
          placeholder="e.g. nextcloud"
          value={newTag}
          onChange={e => setNewTag(e.target.value)}
          sx={{ width: 180 }}
          inputProps={{ style: { fontFamily: 'monospace' } }}
        />
        <TextField
          label="Keyword"
          size="small"
          placeholder="e.g. nextcloud, nc-admin"
          value={newKeyword}
          onChange={e => setNewKeyword(e.target.value)}
          sx={{ flex: '1 1 200px' }}
          inputProps={{ style: { fontFamily: 'monospace' } }}
          onKeyDown={e => {
            if (e.key === 'Enter' && newTag.trim() && newKeyword.trim()) {
              addMutation.mutate({ tag: newTag.trim(), keyword: newKeyword.trim() })
            }
          }}
        />
        <Button
          startIcon={<AddIcon />}
          variant="contained"
          size="small"
          disabled={!newTag.trim() || !newKeyword.trim() || addMutation.isPending}
          onClick={() => addMutation.mutate({ tag: newTag.trim(), keyword: newKeyword.trim() })}
        >
          Add keyword
        </Button>
      </Box>

      {addMutation.isError && (
        <Alert severity="error" sx={{ mb: 2 }}>Failed to add rule.</Alert>
      )}

      {/* Filter */}
      <TextField
        placeholder="Filter tags or keywords…"
        size="small"
        fullWidth
        value={filterText}
        onChange={e => setFilterText(e.target.value)}
        sx={{ mb: 2 }}
      />

      {/* Rules list */}
      {isError && <Alert severity="error">Failed to load rules.</Alert>}
      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
          <CircularProgress />
        </Box>
      ) : (
        <Stack spacing={1}>
          {filtered.map(rule => (
            <TagGroup
              key={rule.tag}
              rule={rule}
              onDeleteKeyword={(tag, kw) => deleteMutation.mutate({ tag, keyword: kw })}
            />
          ))}
          {filtered.length === 0 && (
            <Typography color="text.secondary" align="center" sx={{ py: 6 }}>
              {filterText ? `No rules matching "${filterText}"` : 'No rules defined.'}
            </Typography>
          )}
        </Stack>
      )}

      <Typography variant="caption" color="text.secondary" sx={{ mt: 3, display: 'block' }}>
        Source: <strong>seed</strong> = loaded from code defaults · <strong>manual</strong> = added via UI or API · <strong>llm-discovered</strong> = suggested by nightly job
      </Typography>
    </Box>
  )
}
