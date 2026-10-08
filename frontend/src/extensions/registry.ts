/**
 * Frontend extension registry: named extension points that core code (and
 * later plugins) register items into. Mirrors the backend `tiqora.extensions`
 * registry. A duplicate item key is a programming error and throws.
 */
export type ExtensionOwner = string; // "core" or a plugin id

export interface ExtensionPoint<T> {
  readonly key: string;
  register(item: T, owner?: ExtensionOwner): void;
  get(key: string): T | undefined;
  list(): T[];
  owner(key: string): ExtensionOwner | undefined;
  clearForTests(): void;
}

const introspectors = new Map<string, () => { key: string; owner: ExtensionOwner }[]>();
const POINTS = new Map<string, ExtensionPoint<never>>();

export function createExtensionPoint<T>(key: string, itemKey: (item: T) => string): ExtensionPoint<T> {
  // Same point key -> same point, so module re-evaluation (HMR) keeps one registry.
  const existing = POINTS.get(key);
  if (existing) return existing as unknown as ExtensionPoint<T>;

  const items = new Map<string, { item: T; owner: ExtensionOwner }>();
  const point: ExtensionPoint<T> = {
    key,
    register(item, owner = "core") {
      const k = itemKey(item);
      if (items.has(k)) {
        throw new Error(`Extension point "${key}": "${k}" is already registered`);
      }
      items.set(k, { item, owner });
    },
    get: (k) => items.get(k)?.item,
    list: () => [...items.values()].map((e) => e.item),
    owner: (k) => items.get(k)?.owner,
    clearForTests: () => items.clear(),
  };
  // Introspection for listExtensionPoints (kept off the public interface).
  introspectors.set(key, () => [...items.entries()].map(([k, e]) => ({ key: k, owner: e.owner })));
  POINTS.set(key, point as unknown as ExtensionPoint<never>);
  return point;
}

export function listExtensionPoints(): { key: string; items: { key: string; owner: ExtensionOwner }[] }[] {
  return [...introspectors.entries()].map(([key, items]) => ({ key, items: items() }));
}
