import Box from '@mui/material/Box'
import Chip from '@mui/material/Chip'
import Typography from '@mui/material/Typography'
import { Stats } from '../api/memories'

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

interface Props { stats: Stats }

export default function StatsBar({ stats }: Props) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap', mb: 2 }}>
      <Box>
        <Typography variant="h6" component="span" sx={{ fontWeight: 700, mr: 0.5 }}>
          {stats.total.toLocaleString()}
        </Typography>
        <Typography variant="body2" component="span" color="text.secondary">
          memories
        </Typography>
      </Box>

      <Typography variant="caption" color="text.secondary">
        first: {fmtDate(stats.first_added)}
      </Typography>
      <Typography variant="caption" color="text.secondary">
        last: {fmtDate(stats.last_added)}
      </Typography>

      <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', ml: 'auto' }}>
        {Object.entries(stats.by_agent).map(([agent, count]) => (
          <Chip
            key={agent}
            label={`${agent}: ${count}`}
            size="small"
            variant="outlined"
            sx={{ fontFamily: 'monospace', fontSize: 11 }}
          />
        ))}
      </Box>
    </Box>
  )
}
