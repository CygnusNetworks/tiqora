/**
 * Zoom-menu gating by Znuny's Action ACLs (`GET /tickets/{id}/acl-actions`). `null` means "not
 * known" (loading or failed): everything stays visible, the UI is never blocked on this call.
 */
export type AllowedActions = ReadonlySet<string> | null;

export function actionAllowed(allowed: AllowedActions | undefined, action: string): boolean {
  return allowed == null || allowed.has(action);
}
