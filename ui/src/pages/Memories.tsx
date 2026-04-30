import { useCallback, useDeferredValue, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Button from '@mui/material/Button'
import CircularProgress from '@mui/material/CircularProgress'
import Divider from '@mui/material/Divider'
import FormControl from '@mui/material/FormControl'
import InputAdornment from '@mui/material/InputAdornment'
import InputLabel from '@mui/material/InputLabel'
import MenuItem from '@mui/material/MenuItem'
import Select from '@mui/material/Select'
import Stack from '@mui/material/Stack'
import TextField from '@mui/material/TextField'
import Toolbar from '@mui/material/Toolbar'
import Typography from '@mui/material/Typography'
import AddIcon from '@mui/icons-material/Add'
import DownloadIcon from '@mui/icons-material/Download'
import SearchIcon from '@mui/icons-material/Search'
import { api, FetchParams } from '../api/memories'
import MemoryCard from '../components/MemoryCard'
import StatsBar from '../components/StatsBar'

type Sort = 'newest' | 'oldest' | 'relevance'

export default function Memories() {
  const qc = useQueryClient()

  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<Sort>('newest')
  const [agentFilter, setAgentFilter] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('')
  const [addText, setAddText] = useState('')
  const [showAdd, setShowAdd] = useState(false)

  const deferredSearch = useDeferredValue(search)

  const fetchParams: FetchParams = {
    q: deferredSearch || undefined,
    sort: deferredSearch ? 'relevance' : sort,
    agent_id: agentFilter || undefined,
    category: categoryFilter || undefined,
  }

  const { data: stats } = useQuery({
    queryKey: ['stats'],
    queryFn: api.stats,
    refetchInterval: 30_000,
  })

  const { data: memories = [], isLoading, isError } = useQuery({
    queryKey: ['memories', fetchParams],
    queryFn: () => api.list(fetchParams),
  })

  const agents = stats ? Object.keys(stats.by_agent).sort() : []
  const categories = stats ? Object.keys(stats.by_category).filter(c => c !== 'uncategorized').sort() : []

  const invalidate = useCallback(() => {
    qc.invalidateQueries({ queryKey: ['memories'] })
    qc.invalidateQueries({ queryKey: ['stats'] })
  }, [qc])

  const addMutation = useMutation({
    mutationFn: (content: string) => api.add(content),
    onSuccess: () => { setAddText(''); setShowAdd(false); invalidate() },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, content }: { id: string; content: string }) => api.update(id, content),
    onSuccess: invalidate,
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(id),
    onSuccess: invalidate,
  })

  return (
    <Box sx={{ maxWidth: 900, mx: 'auto', px: 2, py: 3 }}>
      {/* Stats */}
      {stats && <StatsBar stats={stats} />}

      <Divider sx={{ mb: 2 }} />

      {/* Toolbar */}
      <Toolbar disableGutters sx={{ gap: 1, flexWrap: 'wrap', mb: 2, minHeight: 'unset !important' }}>
        <TextField
          placeholder="Search memories…"
          size="small"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          sx={{ flex: '1 1 200px' }}
          InputProps={{
            startAdornment: (
              <InputAdornment position="start">
                <SearchIcon fontSize="small" />
              </InputAdornment>
            ),
          }}
        />

        {!search && (
          <FormControl size="small" sx={{ minWidth: 130 }}>
            <InputLabel>Sort</InputLabel>
            <Select value={sort} label="Sort" onChange={(e) => setSort(e.target.value as Sort)}>
              <MenuItem value="newest">Newest first</MenuItem>
              <MenuItem value="oldest">Oldest first</MenuItem>
            </Select>
          </FormControl>
        )}

        {agents.length > 1 && (
          <FormControl size="small" sx={{ minWidth: 150 }}>
            <InputLabel>Agent</InputLabel>
            <Select value={agentFilter} label="Agent" onChange={(e) => setAgentFilter(e.target.value)}>
              <MenuItem value="">All agents</MenuItem>
              {agents.map((a) => <MenuItem key={a} value={a}>{a}</MenuItem>)}
            </Select>
          </FormControl>
        )}

        {categories.length > 0 && (
          <FormControl size="small" sx={{ minWidth: 140 }}>
            <InputLabel>Category</InputLabel>
            <Select value={categoryFilter} label="Category" onChange={(e) => setCategoryFilter(e.target.value)}>
              <MenuItem value="">All</MenuItem>
              {categories.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
            </Select>
          </FormControl>
        )}

        <Button
          startIcon={<AddIcon />}
          variant="outlined"
          size="small"
          onClick={() => setShowAdd((v) => !v)}
        >
          Add
        </Button>

        <Button
          startIcon={<DownloadIcon />}
          variant="outlined"
          size="small"
          component="a"
          href={api.exportUrl}
          download="memories.json"
        >
          Export
        </Button>
      </Toolbar>

      {/* Add form */}
      {showAdd && (
        <Box sx={{ mb: 2, display: 'flex', gap: 1, alignItems: 'flex-end' }}>
          <TextField
            multiline
            minRows={2}
            fullWidth
            size="small"
            placeholder="Type a memory to add…"
            value={addText}
            onChange={(e) => setAddText(e.target.value)}
            inputProps={{ style: { fontFamily: 'monospace', fontSize: 13 } }}
          />
          <Button
            variant="contained"
            size="small"
            disabled={!addText.trim() || addMutation.isPending}
            onClick={() => addMutation.mutate(addText.trim())}
            sx={{ whiteSpace: 'nowrap' }}
          >
            Save
          </Button>
        </Box>
      )}

      {/* Results header */}
      <Typography variant="caption" color="text.secondary" sx={{ mb: 1, display: 'block' }}>
        {isLoading ? 'Loading…' : `${memories.length.toLocaleString()} ${search ? 'results' : 'memories'}`}
      </Typography>

      {/* Content */}
      {isError && <Alert severity="error" sx={{ mb: 2 }}>Failed to load memories.</Alert>}

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
          <CircularProgress />
        </Box>
      ) : (
        <Stack spacing={1}>
          {memories.map((m) => (
            <MemoryCard
              key={m.id}
              memory={m}
              onDelete={(id) => deleteMutation.mutate(id)}
              onUpdate={(id, content) => updateMutation.mutate({ id, content })}
            />
          ))}
          {memories.length === 0 && (
            <Typography color="text.secondary" align="center" sx={{ py: 6 }}>
              {search ? `No results for "${search}"` : 'No memories yet.'}
            </Typography>
          )}
        </Stack>
      )}
    </Box>
  )
}
