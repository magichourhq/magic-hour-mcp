/**
 * Where the More menu starts horizontally, in px from the left edge of the action row.
 * Pure, with no DOM access, so it can be tested with `node --test`.
 * @module
 */

/** The horizontal extent of a box, in px from the page's left edge (a `DOMRect` fits). */
export type Span = { left: number; right: number };

/**
 * Calculates how far from the row's left edge the menu should start so it lines up with
 * the More button. The menu starts at the button's left edge. If that
 * would run past the row's right edge, it ends at the button's right edge instead, but
 * never starts before the row's left edge.
 * @param row The action row the menu sits in.
 * @param button The More button.
 * @param menuWidth The menu's rendered width, in px.
 * @returns The menu's left offset inside the row, in px (0 or more).
 */
export function menuOffset(row: Span, button: Span, menuWidth: number): number {
  const rowWidth = row.right - row.left;
  const buttonStart = button.left - row.left;
  if (buttonStart + menuWidth <= rowWidth) return buttonStart;
  const buttonEnd = button.right - row.left;
  return Math.max(0, buttonEnd - menuWidth);
}
