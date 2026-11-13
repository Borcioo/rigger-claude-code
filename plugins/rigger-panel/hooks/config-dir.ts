import type { EngineInterface } from 'claude-code'

// Same order as the rest of the harness: CLAUDE_CONFIG_DIR, else `.claude` in the home folder.
// Windows has USERPROFILE (its HOME, when set, may be a Git Bash style path); macOS
// and Linux have HOME only. The value is used as given: the engine's file system takes forward
// slashes after a Windows drive and tolerates a doubled separator.
export async function configDir($: EngineInterface) {
  const explicit = (await $.env.get('CLAUDE_CONFIG_DIR'))?.trim()
  if (explicit) return explicit
  const home = (await $.env.get('USERPROFILE'))?.trim() || (await $.env.get('HOME'))?.trim()
  return home ? `${home}/.claude` : undefined
}
