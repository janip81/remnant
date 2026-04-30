import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createTheme, ThemeProvider, alpha } from '@mui/material/styles'
import AppBar from '@mui/material/AppBar'
import Box from '@mui/material/Box'
import CssBaseline from '@mui/material/CssBaseline'
import InitColorSchemeScript from '@mui/material/InitColorSchemeScript'
import Toolbar from '@mui/material/Toolbar'
import Typography from '@mui/material/Typography'
import { colorSchemes, typography, shadows, shape, gray, brand } from './theme/primitives'
import Memories from './pages/Memories'

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
  },
})

export default function App() {
  return (
    <>
      <InitColorSchemeScript
        defaultMode="dark"
        attribute="data-mui-color-scheme"
      />
      <ThemeProvider theme={theme} defaultMode="dark" disableTransitionOnChange>
        <CssBaseline enableColorScheme />
        <QueryClientProvider client={queryClient}>
          <AppBar position="sticky" elevation={0}>
            <Toolbar variant="dense" sx={{ gap: 1 }}>
              <Typography variant="subtitle2" sx={{ fontWeight: 700, color: `hsl(210, 98%, 55%)` }}>
                ⬡ Claude Memory
              </Typography>
            </Toolbar>
          </AppBar>
          <Box component="main">
            <Memories />
          </Box>
        </QueryClientProvider>
      </ThemeProvider>
    </>
  )
}
