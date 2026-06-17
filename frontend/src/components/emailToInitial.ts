/**
 * Derives an Avatar initial from an email address.
 * Uses the first character of the local part (before @), uppercased.
 * Falls back to "?" if parsing fails.
 */
export function emailToInitial(email: string): string {
  const localPart = email.split("@")[0];
  if (!localPart || localPart.length === 0) return "?";
  return localPart[0].toUpperCase();
}
