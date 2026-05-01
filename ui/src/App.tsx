import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createTheme, ThemeProvider, alpha } from '@mui/material/styles'
import AppBar from '@mui/material/AppBar'
import Box from '@mui/material/Box'
import CssBaseline from '@mui/material/CssBaseline'
import InitColorSchemeScript from '@mui/material/InitColorSchemeScript'
import Tab from '@mui/material/Tab'
import Tabs from '@mui/material/Tabs'
import Toolbar from '@mui/material/Toolbar'
import { colorSchemes, typography, shadows, shape, gray } from './theme/primitives'
import Memories from './pages/Memories'
import Stats from './pages/Stats'
import TagRules from './pages/TagRules'
import Jobs from './pages/Jobs'

type Page = 'memories' | 'stats' | 'rules' | 'jobs'

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 10_000 } },
})

const theme = createTheme({
  cssVariables: {
    colorSchemeSelector: 'data-mui-color-scheme',
    cssVarPrefix: 'template',
  },
  colorSchemes,
  typography,
  shadows,
  shape,
  components: {
    MuiCssBaseline: { styleOverrides: { body: { margin: 0 } } },
    MuiButtonBase: {
      defaultProps: { disableTouchRipple: true, disableRipple: true },
      styleOverrides: {
        root: ({ theme }) => ({
          transition: 'all 100ms ease-in',
          '&:focus-visible': {
            outline: `3px solid ${alpha(theme.palette.primary.main, 0.5)}`,
            outlineOffset: '2px',
          },
        }),
      },
    },
    MuiButton: {
      styleOverrides: {
        root: { boxShadow: 'none', textTransform: 'none', fontWeight: 600, '&:hover': { boxShadow: 'none' } },
      },
    },
    MuiIconButton: {
      styleOverrides: {
        root: ({ theme }) => ({
          borderRadius: theme.shape.borderRadius,
          border: '1px solid',
          borderColor: (theme.vars || theme).palette.divider,
          '&:hover': { backgroundColor: (theme.vars || theme).palette.action.hover },
        }),
      },
    },
    MuiCard: {
      styleOverrides: {
        root: ({ theme }) => ({
          backgroundImage: 'none',
          border: `1px solid ${(theme.vars || theme).palette.divider}`,
          boxShadow: 'none',
        }),
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: ({ theme }) => ({
          backgroundColor: (theme.vars || theme).palette.background.paper,
          backgroundImage: 'none',
          borderBottom: `1px solid ${(theme.vars || theme).palette.divider}`,
          boxShadow: 'none',
          color: (theme.vars || theme).palette.text.primary,
        }),
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: ({ theme }) => ({
          borderRadius: theme.shape.borderRadius,
          '& .MuiOutlinedInput-notchedOutline': { borderColor: (theme.vars || theme).palette.divider },
          '&:hover .MuiOutlinedInput-notchedOutline': { borderColor: gray[400] },
        }),
      },
    },
    MuiChip: { styleOverrides: { root: { fontWeight: 500 } } },
    MuiDivider: {
      styleOverrides: {
        root: ({ theme }) => ({ borderColor: (theme.vars || theme).palette.divider, opacity: 1 }),
      },
    },
    MuiTab: {
      styleOverrides: {
        root: { textTransform: 'none', fontWeight: 500, minHeight: 40, fontSize: 13 },
      },
    },
    MuiTabs: {
      styleOverrides: {
        root: { minHeight: 40 },
        indicator: { height: 2 },
      },
    },
  },
})

export default function App() {
  const [page, setPage] = useState<Page>('memories')

  return (
    <>
      <InitColorSchemeScript defaultMode="dark" attribute="data-mui-color-scheme" />
      <ThemeProvider theme={theme} defaultMode="dark" disableTransitionOnChange>
        <CssBaseline enableColorScheme />
        <QueryClientProvider client={queryClient}>
          <AppBar position="sticky" elevation={0}>
            <Toolbar variant="dense" sx={{ gap: 2, alignItems: 'center' }}>
              <Box component="img" src="/logo-dark.png" alt="Remnant" sx={{ height: 28, width: 'auto', flexShrink: 0 }} />
              <Tabs
                value={page}
                onChange={(_, v) => setPage(v as Page)}
                sx={{ flex: 1 }}
                textColor="inherit"
              >
                <Tab label="Memories" value="memories" />
                <Tab label="Stats" value="stats" />
                <Tab label="Tag Rules" value="rules" />
                <Tab label="Jobs" value="jobs" />
              </Tabs>
            </Toolbar>
          </AppBar>
          <Box component="main">
            {page === 'memories' && <Memories />}
            {page === 'stats' && <Stats />}
            {page === 'rules' && <TagRules />}
            {page === 'jobs' && <Jobs />}
          </Box>
        </QueryClientProvider>
      </ThemeProvider>
    </>
  )
}
