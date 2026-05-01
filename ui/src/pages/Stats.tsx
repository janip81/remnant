import { useQuery } from '@tanstack/react-query'
import Alert from '@mui/material/Alert'
import Box from '@mui/material/Box'
import CircularProgress from '@mui/material/CircularProgress'
import Divider from '@mui/material/Divider'
import Grid from '@mui/material/Grid'
import Typography from '@mui/material/Typography'
import { api, Stats as StatsType } from '../api/memories'

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

function BarChart({ data, color = 'primary.main' }: { data: Record<string, number>; color?: string }) {
  const sorted = Object.entries(data).sort(([, a], [, b]) => b - a)
  const max = sorted[0]?.[1] ?? 1
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.5 }}>
      {sorted.map(([label, value]) => (
        <Box key={label} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Typography
            variant="caption"
            sx={{ width: 140, textAlign: 'right', flexShrink: 0, fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
            title={label}
          >
            {label}
          </Typography>
          <Box sx={{ flex: 1, bgcolor: 'action.hover', borderRadius: 0.5, height: 14, position: 'relative', minWidth: 0 }}>
            <Box
              sx={{
                width: `${(value / max) * 100}%`,
                bgcolor: color,
                height: '100%',
                borderRadius: 0.5,
                transition: 'width 0.3s ease',
              }}
            />
          </Box>
          <Typography variant="caption" sx={{ width: 44, flexShrink: 0, fontFamily: 'monospace', textAlign: 'right' }}>
            {value.toLocaleString()}
          </Typography>
        </Box>
      ))}
    </Box>
  )
}

function StatBlock({ label, value }: { label: string; value: string | number }) {
  return (
    <Box>
      <Typography variant="h5" sx={{ fontWeight: 700 }}>{typeof value === 'number' ? value.toLocaleString() : value}</Typography>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
    </Box>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Box>
      <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1, textTransform: 'uppercase', fontSize: 11, letterSpacing: 0.5 }}>
        {title}
      </Typography>
      {children}
    </Box>
  )
}

export default function Stats() {
  const { data: stats, isLoading, isError } = useQuery<StatsType>({
    queryKey: ['stats'],
    queryFn: api.stats,
    refetchInterval: 60_000,
  })

  if (isLoading) return (
    <Box sx={{ display: 'flex', justifyContent: 'center', py: 10 }}>
      <CircularProgress />
    </Box>
  )

  if (isError || !stats) return (
    <Box sx={{ maxWidth: 900, mx: 'auto', px: 2, py: 3 }}>
      <Alert severity="error">Failed to load stats.</Alert>
    </Box>
  )

  const agentCount = Object.keys(stats.by_agent).length
  const categoryCount = Object.keys(stats.by_category).filter(c => c !== 'uncategorized').length
  const tagCount = Object.keys(stats.by_tag ?? {}).length
  const taggedCount = Object.entries(stats.by_tag ?? {}).reduce((s, [, v]) => s + v, 0)
  const untaggedCount = stats.total - taggedCount

  return (
    <Box sx={{ maxWidth: 1000, mx: 'auto', px: 2, py: 3 }}>
      <Typography variant="h6" sx={{ fontWeight: 700, mb: 1 }}>Statistics</Typography>
      <Divider sx={{ mb: 3 }} />

      {/* Summary row */}
      <Box sx={{ display: 'flex', gap: 4, flexWrap: 'wrap', mb: 4 }}>
        <StatBlock label="total memories" value={stats.total} />
        <StatBlock label="agents" value={agentCount} />
        <StatBlock label="categories" value={categoryCount} />
        <StatBlock label="distinct tags" value={tagCount} />
        <StatBlock label="tagged memories" value={taggedCount} />
        <StatBlock label="untagged" value={untaggedCount} />
        <StatBlock label="first added" value={fmtDate(stats.first_added)} />
        <StatBlock label="last added" value={fmtDate(stats.last_added)} />
      </Box>

      <Grid container spacing={4}>
        <Grid item xs={12} md={6}>
          <Section title="By Category">
            <BarChart data={stats.by_category} color="secondary.main" />
          </Section>
        </Grid>

        <Grid item xs={12} md={6}>
          <Section title="By Agent">
            <BarChart data={stats.by_agent} color="primary.main" />
          </Section>
        </Grid>

        {stats.by_tag && Object.keys(stats.by_tag).length > 0 && (
          <Grid item xs={12}>
            <Section title={`Top Tags (${tagCount} total)`}>
              <BarChart
                data={Object.fromEntries(
                  Object.entries(stats.by_tag)
                    .sort(([, a], [, b]) => b - a)
                    .slice(0, 40)
                )}
                color="success.main"
              />
            </Section>
          </Grid>
        )}
      </Grid>
    </Box>
  )
}
