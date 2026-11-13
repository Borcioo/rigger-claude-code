import type { ElementConstructor, EngineInterface, SvgProps } from 'claude-code'

// What `$.ui.resolve(e)` hands a render hook.
export type Elements = ReturnType<EngineInterface['ui']['resolve']>

// Spacing every tab shares: columns between a label and its value, rows between blocks.
export const GAP = 1
export const BLOCK_GAP = 1

// Labels of the Environments cards, in drawing order; the first column of a card is as wide as the longest.
export const ENV_LABELS = {
  url: 'Site',
  admin: 'Admin',
  branch: 'Branch',
  server: 'Server',
  account: 'Account',
  path: 'Path',
  database: 'Database',
  deploy: 'Deploy',
  notes: 'Notes',
} as const

export const ENV_DETAILS = ['branch', 'server', 'account', 'path', 'database', 'deploy', 'notes'] as const

export const labelWidth = (labels: readonly string[]) => Math.max(0, ...labels.map(label => label.length))

// The Svg element only where the surface draws it: not on the terminal, as the Plan tab always did.
export const svgOf = (elements: Elements, surface: string) =>
  surface === 'terminal' ? undefined : (elements as unknown as { Svg: ElementConstructor<SvgProps> }).Svg

// A dim label of a fixed width, then the value in a column of its own that wraps inside itself.
export const row = ({ Box, Text }: Elements, width: number, label: string, value: JSX.Element) => (
  <Box flexDirection="row" columnGap={GAP}>
    <Box width={width} flexShrink={0}>
      <Text dimColor>{label}</Text>
    </Box>
    <Box flexDirection="column" flexShrink={1} flexGrow={1}>
      {value}
    </Box>
  </Box>
)

export const textRow = (elements: Elements, width: number, label: string, value: string) =>
  row(elements, width, label, <elements.Text>{value}</elements.Text>)

export const isProd = (name: string) => /^prod/iu.test(name)

// PROD is white on red; any other role is white on blue.
export const roleBadge = ({ Text }: Elements, role: string) => (
  <Text bold color="white" backgroundColor={isProd(role) ? 'red' : 'blue'}>
    {` ${role} `}
  </Text>
)

const clamp = (percent: number) => (Number.isFinite(percent) ? Math.min(100, Math.max(0, percent)) : 0)

export const barColor = (percent: number) => (percent >= 85 ? 'red' : percent >= 70 ? 'yellow' : 'green')

const BAR_FILL = { red: '#c62828', yellow: '#f9a825', green: '#2e7d32' } as const

// The context bar of the terminal: `cells` characters wide, filled by `percent`.
export const textBar = (percent: number, cells: number) => {
  const filled = Math.round((clamp(percent) / 100) * cells)
  return '█'.repeat(filled) + '░'.repeat(cells - filled)
}

// How wide the terminal bar may be under a label column: what is left of the pane, but at most 20.
export const barCells = (columns: number, width: number) => Math.max(6, Math.min(20, columns - width - GAP - 4))

export const BAR_WIDTH = 140
export const BAR_HEIGHT = 6

// The context bar of a graphical surface; the track is gray with some transparency, so it holds on both themes.
export const barSvg = (percent: number) => {
  const filled = Math.round((clamp(percent) / 100) * BAR_WIDTH)
  const fill = filled > 0 ? `<rect width="${filled}" height="${BAR_HEIGHT}" rx="3" fill="${BAR_FILL[barColor(percent)]}"/>` : ''
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${BAR_WIDTH} ${BAR_HEIGHT}"><rect width="${BAR_WIDTH}" height="${BAR_HEIGHT}" rx="3" fill="#8a8a8a" fill-opacity="0.3"/>${fill}</svg>`
}
