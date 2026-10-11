/**
 * Where the widget gallery sits, decided by the WINDOW width alone. The grid's own breakpoint
 * follows its container, which the open panel shrinks, so deciding by it made an open gallery
 * oscillate (panel opens -> grid drops to the phone layout -> panel closes -> grid widens ...).
 *
 * "side" needs the window wide enough that the grid keeps its tablet layout (>= 520 px) next to a
 * 360 px panel: the shell takes about 216 px sidebar + 64 px padding, so 1280 px leaves 1000 px
 * for the grid and 640 px with the panel open. Narrower windows get the overlay; phones none.
 */
export const GALLERY_SIDE_MIN = 1280;
export const GALLERY_OVERLAY_MIN = 640;

export type GalleryPlacement = "side" | "overlay" | "none";

export function galleryPlacementForWidth(windowWidth: number): GalleryPlacement {
  if (windowWidth >= GALLERY_SIDE_MIN) return "side";
  return windowWidth >= GALLERY_OVERLAY_MIN ? "overlay" : "none";
}
