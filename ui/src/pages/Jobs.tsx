import { useQuery } from '@tanstack/react-query'
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import CircularProgress from '@mui/material/CircularProgress'
import Divider from '@mui/material/Divider'
import Table from '@mui/material/Table'
import TableBody from '@mui/material/TableBody'
import TableCell from '@mui/material/TableCell'
import TableContainer from '@mui/material/TableContainer'
import TableHead from '@mui/material/TableHead'
import TableRow from '@mui/material/TableRow'
import Tooltip from '@mui/material/Tooltip'
import Typography from '@mui/material/Typography'
import { JobRun, jobsApi } from '../api/jobs'

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function fmtDuration(secs: number | null): string {
  if (secs == null) return '—'
  if (secs < 60) return `${secs.toFixed(1)}s`
  return `${(secs / 60).toFixed(1)}m`
}

function StatusChip({ status }: { status: JobRun['status'] }) {
  const color = status === 'completed' ? 'success' : status === 'failed' ? 'error' : 'warning'
  return <Chip label={status} size="small" color={color as any} variant="outlined" sx={{ fontSize: 11 }} />
}

function PhaseChip({ phase }: { phase: string }) {
  const colors: Record<string, string> = {
    'llm-tagging': '#7c4dff',
    'hash-dedup': '#0288d1',
    'semantic-dedup': '#00897b',
  }
  return (
    <Chip
      label={phase}
      size="small"
      variant="outlined"
      sx={{ fontSize: 11, fontFamily: 'monospace', borderColor: colors[phase] || 'divider', color: colors[phase] || 'text.primary' }}
    />
  )
}

export default function Jobs() {
  const { data: jobs = [], isLoading, isError } = useQuery({
    queryKey: ['jobs'],
    queryFn: jobsApi.list,
    refetchInterval: 30_000,
  })

  // Aggregate totals across all completed runs
  const completed = jobs.filter(j => j.status === 'completed')
  const totalTagged = completed.filter(j => j.phase === 'llm-tagging').reduce((s, j) => s + j.memories_changed, 0)
  const totalDeduped = completed.filter(j => j.phase !== 'llm-tagging').reduce((s, j) => s + j.memories_changed, 0)
  const totalRulesAdded = completed.reduce((s, j) => s + (j.rules_added ?? 0), 0)
  const lastRun = jobs[0] ?? null

  return (
    <Box sx={{ maxWidth: 1100, mx: 'auto', px: 2, py: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mb: 1 }}>
        <Typography variant="h6" sx={{ fontWeight: 700 }}>Nightly Job History</Typography>
        <Typography variant="caption" color="text.secondary">
          {lastRun ? `last run ${fmtDate(lastRun.started_at)}` : 'no runs yet'}
        </Typography>
      </Box>

      <Divider sx={{ mb: 2 }} />

      {/* Summary stats */}
      <Box sx={{ display: 'flex', gap: 3, mb: 3, flexWrap: 'wrap' }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>{jobs.length}</Typography>
          <Typography variant="caption" color="text.secondary">total phases logged</Typography>
        </Box>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>{totalTagged.toLocaleString()}</Typography>
          <Typography variant="caption" color="text.secondary">memories LLM-tagged</Typography>
        </Box>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>{totalDeduped.toLocaleString()}</Typography>
          <Typography variant="caption" color="text.secondary">memories deduped</Typography>
        </Box>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>{totalRulesAdded.toLocaleString()}</Typography>
          <Typography variant="caption" color="text.secondary">rules discovered</Typography>
        </Box>
      </Box>

      {isError && <Alert severity="error" sx={{ mb: 2 }}>Failed to load job history.</Alert>}

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
          <CircularProgress />
        </Box>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Started</TableCell>
                <TableCell>Phase</TableCell>
                <TableCell>Status</TableCell>
                <TableCell align="right">Scanned</TableCell>
                <TableCell align="right">Changed</TableCell>
                <TableCell align="right">Rules+</TableCell>
                <TableCell align="right">Duration</TableCell>
                <TableCell>Error</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {jobs.map(job => (
                <TableRow key={job.id} hover>
                  <TableCell sx={{ whiteSpace: 'nowrap', fontSize: 12 }}>{fmtDate(job.started_at)}</TableCell>
                  <TableCell><PhaseChip phase={job.phase} /></TableCell>
                  <TableCell><StatusChip status={job.status} /></TableCell>
                  <TableCell align="right" sx={{ fontFamily: 'monospace', fontSize: 12 }}>{job.memories_scanned ?? 0}</TableCell>
                  <TableCell align="right" sx={{ fontFamily: 'monospace', fontSize: 12 }}>{job.memories_changed ?? 0}</TableCell>
                  <TableCell align="right" sx={{ fontFamily: 'monospace', fontSize: 12 }}>{job.rules_added ?? 0}</TableCell>
                  <TableCell align="right" sx={{ fontFamily: 'monospace', fontSize: 12 }}>{fmtDuration(job.duration_seconds)}</TableCell>
                  <TableCell sx={{ fontSize: 11, color: 'error.main', maxWidth: 200 }}>
                    {job.error ? (
                      <Tooltip title={job.error}>
                        <span style={{ cursor: 'help' }}>{job.error.slice(0, 60)}{job.error.length > 60 ? '…' : ''}</span>
                      </Tooltip>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
              {jobs.length === 0 && (
                <TableRow>
                  <TableCell colSpan={8} align="center" sx={{ py: 6, color: 'text.secondary' }}>
                    No job runs yet. The nightly CronJob logs each phase here.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  )
}
