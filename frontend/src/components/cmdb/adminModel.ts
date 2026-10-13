/** Pure helpers of the CMDB admin pages (class list and catalog). */

export const KEY_PATTERN = /^[a-z][a-z0-9_]{1,49}$/;

/** Slug for the class key proposed from the name (`Mein Server` becomes `mein_server`). */
export function suggestClassKey(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/ä/g, "ae")
    .replace(/ö/g, "oe")
    .replace(/ü/g, "ue")
    .replace(/ß/g, "ss")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return /^[a-z]/.test(slug) ? slug.slice(0, 50) : "";
}

/** The number a CI of the class gets first (D5: prefix + six digits). */
export function exampleNumber(prefix: string, classKey: string): string {
  const p = prefix.trim() || (classKey ? `${classKey.toUpperCase().slice(0, 15)}-` : "");
  return `${p}000001`;
}

/** The two catalog classes whose items carry a functionality (D6): CI states drive the propagation. */
export const STATE_FUNCTIONALITIES: Readonly<Record<string, readonly string[]>> = {
  "ITSM::ConfigItem::DeploymentState": ["preproductive", "productive", "postproductive"],
  "ITSM::Core::IncidentState": ["operational", "warning", "incident"],
};
