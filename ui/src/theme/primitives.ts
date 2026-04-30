import { createTheme, alpha, Shadows } from '@mui/material/styles'

const defaultTheme = createTheme()

export const brand = {
  50: 'hsl(210, 100%, 95%)',
  100: 'hsl(210, 100%, 92%)',
  200: 'hsl(210, 100%, 80%)',
  300: 'hsl(210, 100%, 65%)',
  400: 'hsl(210, 98%, 48%)',
  500: 'hsl(210, 98%, 42%)',
  600: 'hsl(210, 98%, 55%)',
  700: 'hsl(210, 100%, 35%)',
  800: 'hsl(210, 100%, 16%)',
  900: 'hsl(210, 100%, 21%)',
}

export const gray = {
  50: 'hsl(220, 35%, 97%)',
  100: 'hsl(220, 30%, 94%)',
  200: 'hsl(220, 20%, 88%)',
  300: 'hsl(220, 20%, 80%)',
  400: 'hsl(220, 20%, 65%)',
  500: 'hsl(220, 20%, 42%)',
  600: 'hsl(220, 20%, 35%)',
  700: 'hsl(220, 20%, 25%)',
  800: 'hsl(220, 30%, 6%)',
  900: 'hsl(220, 35%, 3%)',
}

export const colorSchemes = {
  light: {
    palette: {
      primary: { light: brand[200], main: brand[400], dark: brand[700], contrastText: brand[50] },
      background: { default: 'hsl(0, 0%, 99%)', paper: 'hsl(220, 35%, 97%)' },
      text: { primary: gray[800], secondary: gray[600] },
      divider: alpha(gray[300], 0.4),
      action: { hover: alpha(gray[200], 0.2), selected: alpha(gray[200], 0.3) },
      baseShadow: 'hsla(220, 30%, 5%, 0.07) 0px 4px 16px 0px',
    },
  },
  dark: {
    palette: {
      primary: { contrastText: brand[50], light: brand[300], main: brand[400], dark: brand[700] },
      background: { default: gray[900], paper: 'hsl(220, 30%, 7%)' },
      text: { primary: 'hsl(0, 0%, 100%)', secondary: gray[400] },
      divider: alpha(gray[700], 0.6),
      action: { hover: alpha(gray[600], 0.2), selected: alpha(gray[600], 0.3) },
      baseShadow: 'hsla(220, 30%, 5%, 0.7) 0px 4px 16px 0px',
    },
  },
}

export const typography = {
  fontFamily: 'Inter, sans-serif',
  h6: { fontSize: defaultTheme.typography.pxToRem(18), fontWeight: 600 },
  subtitle2: { fontSize: defaultTheme.typography.pxToRem(14), fontWeight: 500 },
  body1: { fontSize: defaultTheme.typography.pxToRem(14) },
  body2: { fontSize: defaultTheme.typography.pxToRem(14), fontWeight: 400 },
  caption: { fontSize: defaultTheme.typography.pxToRem(12), fontWeight: 400 },
}

export const shape = { borderRadius: 8 }

// @ts-ignore
export const shadows: Shadows = [
  'none',
  'var(--template-palette-baseShadow)',
  ...defaultTheme.shadows.slice(2),
]
