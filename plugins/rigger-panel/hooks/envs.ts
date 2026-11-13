import type { EnvRow } from '../types'

export const ENV_FILE = '.claude/environments.json'

// Drawn first, in this order; every other name follows in the order of the file.
const FIRST = ['local', 'dev', 'prod']
// Every field besides the links that the file may hold.
export const DETAIL_FIELDS = ['server', 'account', 'role', 'path', 'branch', 'database', 'deploy', 'notes'] as const

// A field named like a secret, or a value that is a URL with credentials, refuses the whole file.
const SECRET_NAME = /pass(?:wd|word)?|token|secret|key/iu
const CREDENTIAL_URL = /:\/\/[^/\s@:]+:[^/\s@]*@/u
const WEB_URL = /^https?:\/\/\S+$/iu

// Any other top-level key is ignored, whatever its shape.
export type ParsedEnvs = { ok: true; project?: string; repository?: string; rows: EnvRow[] } | { ok: false; reason: string }

const fail = (reason: string): ParsedEnvs => ({ ok: false, reason })

// The first secret-looking name or value anywhere in the document, or undefined. Names only: the
// value is never echoed.
function findSecret(value: unknown, path: string): string | undefined {
  if (typeof value === 'string') return CREDENTIAL_URL.test(value) ? `value of field ${path || '(root)'}` : undefined
  if (Array.isArray(value)) {
    for (const [index, item] of value.entries()) {
      const hit = findSecret(item, `${path}[${index}]`)
      if (hit) return hit
    }
    return undefined
  }
  if (value && typeof value === 'object') {
    for (const [name, item] of Object.entries(value)) {
      const here = path ? `${path}.${name}` : name
      if (SECRET_NAME.test(name)) return `field ${here}`
      const hit = findSecret(item, here)
      if (hit) return hit
    }
  }
  return undefined
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const orderOf = (names: string[]) => [
  ...FIRST.filter(name => names.includes(name)),
  ...names.filter(name => !FIRST.includes(name)),
]

// The text of `.claude/environments.json` as drawable rows, or the reason it draws nothing.
export function parseEnvironments(text: string): ParsedEnvs {
  let doc: unknown
  try {
    doc = JSON.parse(text.charCodeAt(0) === 0xfeff ? text.slice(1) : text)
  } catch (error) {
    return fail(`invalid JSON (${error instanceof Error ? error.message : String(error)})`)
  }
  const secret = findSecret(doc, '')
  if (secret) return fail(`file refused, it looks like a secret: ${secret}. Keep passwords, tokens and keys out of this file`)
  if (!isRecord(doc)) return fail('wrong shape: expected an object')
  if (doc.version !== 1) return fail('wrong shape: "version" must be 1')
  if (doc.project !== undefined && typeof doc.project !== 'string') return fail('wrong shape: "project" must be a string')
  if (!isRecord(doc.environments)) return fail('wrong shape: no "environments" object')

  if (doc.repository !== undefined && (typeof doc.repository !== 'string' || !WEB_URL.test(doc.repository))) {
    return fail('wrong shape: "repository" must be an http(s) URL')
  }

  const source = doc.environments
  const rows: EnvRow[] = []
  for (const name of orderOf(Object.keys(source))) {
    const raw = source[name]
    if (!isRecord(raw)) return fail(`wrong shape: environment "${name}" must be an object`)
    const row: EnvRow = { name }
    for (const field of ['url', 'admin', ...DETAIL_FIELDS] as const) {
      const value = raw[field]
      if (value === undefined) continue
      if (typeof value !== 'string') return fail(`wrong shape: "${name}.${field}" must be a string`)
      if ((field === 'url' || field === 'admin') && !WEB_URL.test(value)) {
        return fail(`wrong shape: "${name}.${field}" must be an http(s) URL`)
      }
      row[field] = value
    }
    rows.push(row)
  }
  return {
    ok: true,
    ...(typeof doc.project === 'string' ? { project: doc.project } : {}),
    ...(typeof doc.repository === 'string' ? { repository: doc.repository } : {}),
    rows,
  }
}
