import type { AuthIdentity } from './api'

/**
 * Resolve the label shown in the sidebar account row / account panel header.
 *
 * Logged-in cloud `displayName` wins when it looks like a real nickname.
 * If the cloud value is just the bound phone/email (common server default),
 * treat it as unset and fall back to the local `gw-user-name` — otherwise
 * users who set a nickname locally (or at registration that only stuck locally)
 * keep seeing their phone number after login.
 */

export function digitsOnly(value: string): string {
  return value.replace(/\D/g, '')
}

/** True when `name` is empty or matches a login identity (phone/email). */
export function isIdentityLikeDisplayName(
  name: string,
  identities: readonly AuthIdentity[] = [],
): boolean {
  const n = name.trim()
  if (!n) return true

  for (const id of identities) {
    const ident = (id.identifier ?? '').trim()
    if (!ident) continue
    if (n === ident) return true
    if (id.provider === 'email' && n.toLowerCase() === ident.toLowerCase()) return true
    if (id.provider === 'phone') {
      const a = digitsOnly(n)
      const b = digitsOnly(ident)
      if (a.length >= 11 && b.length >= 11 && (a === b || a.endsWith(b) || b.endsWith(a))) {
        return true
      }
    }
  }

  // Cloud sometimes returns a bare CN mobile with identities still loading.
  const bare = digitsOnly(n)
  if (bare.length === 11 && /^1\d{10}$/.test(bare) && !/[a-zA-Z\u4e00-\u9fff]/.test(n)) {
    return true
  }
  return false
}

export function resolveAccountDisplayName(input: {
  loggedIn: boolean
  cloudName?: string | null
  identities?: readonly AuthIdentity[]
  localName?: string | null
  fallback: string
}): string {
  const cloud = (input.cloudName ?? '').trim()
  const local = (input.localName ?? '').trim()
  const identities = input.identities ?? []

  if (input.loggedIn && cloud && !isIdentityLikeDisplayName(cloud, identities)) {
    return cloud
  }
  if (local) return local
  // Prefer a human fallback over echoing the phone/email as the "name".
  return input.fallback
}
