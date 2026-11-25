import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import type { FollowUp } from "./followUps";
import { menuOffset } from "./utils/menuOffset";
import "./MoreMenu.css";

/**
 * The arrow on the More button. It points down, and its wrapper rotates it to point up
 * while the menu is open; the rotation is animated in `MoreMenu.css`.
 * @param props.up `true` while the menu is open.
 * @returns A decorative arrow, hidden from screen readers.
 */
function Chevron({ up }: { up: boolean }) {
  return (
    <span className="more-chevron" data-up={up} aria-hidden="true">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="m6 9 6 6 6-6" />
      </svg>
    </span>
  );
}

/**
 * Works out which menu item a navigation key moves focus to. Arrows wrap around at the ends.
 * @param key The pressed key, for example `"ArrowDown"`.
 * @param current Index of the focused item, or -1 when none is focused.
 * @param count Number of items in the menu.
 * @returns The index to focus, or `null` when the key doesn't move focus.
 */
function nextFocusIndex(key: string, current: number, count: number): number | null {
  switch (key) {
    case "ArrowDown": return (current + 1) % count;
    case "ArrowUp": return (current - 1 + count) % count;
    case "Home": return 0;
    case "End": return count - 1;
    default: return null;
  }
}

/**
 * The More button and its menu of extra follow-up actions.
 *
 * The menu opens in the page flow under the action row instead of floating over it, so
 * the card's iframe grows to fit it and it is never clipped, even in a narrow card. It is
 * then shifted to sit right under the More button, like a dropdown. It works with the
 * keyboard and closes on Esc, Tab, an outside click or a selection.
 * @param props.items The actions to list, top to bottom. Nothing renders when it's empty.
 * @param props.disabled Disables the items, for example while a draft is opening.
 * @param props.onSelect Called with the chosen action after the menu closes.
 * @returns The button and, while open, the menu; or `null` when there are no items.
 */
export function MoreMenu({ items, disabled, onSelect }: {
  items: FollowUp[];
  disabled: boolean;
  onSelect: (item: FollowUp) => void;
}) {
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);

  /** Aligns the open menu under the More button, and again when the card is resized. */
  useLayoutEffect(() => {
    if (!open) return;
    /** Measures the row, button and menu, and moves the menu under the button. */
    const align = () => {
      const menu = menuRef.current;
      /** `.more` is `display: contents` and has no box, so measure against the action row. */
      const row = rootRef.current?.parentElement;
      const button = buttonRef.current;
      if (!menu || !row || !button) return;
      const offset = menuOffset(row.getBoundingClientRect(), button.getBoundingClientRect(), menu.offsetWidth);
      menu.style.marginLeft = `${offset}px`;
    };
    align();
    window.addEventListener("resize", align);
    return () => window.removeEventListener("resize", align);
  }, [open]);

  /** Focuses the first item when the menu opens, and closes it on a click outside. */
  useEffect(() => {
    if (!open) return;
    itemRefs.current[0]?.focus();
    /** Closes the menu when a pointer goes down outside the button and menu. */
    const closeOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [open]);

  /** Closes the menu and returns focus to the More button, so keyboard users don't lose their place. */
  const closeAndFocusButton = () => {
    setOpen(false);
    buttonRef.current?.focus();
  };

  /**
   * Runs a menu item: closes the menu first, then hands the action to the card.
   * @param item The chosen action.
   */
  const selectItem = (item: FollowUp) => {
    closeAndFocusButton();
    onSelect(item);
  };

  /**
   * Opens the menu with ↓ on the More button. Enter and Space already open it through `onClick`.
   * @param event The key event on the button.
   */
  const onButtonKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "ArrowDown" || open) return;
    event.preventDefault();
    setOpen(true);
  };

  /**
   * Keyboard handling inside the open menu: Esc closes and returns focus, Tab closes and
   * lets focus move on, and arrows, Home and End move between items.
   * @param event The key event from the menu or one of its items.
   */
  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeAndFocusButton();
      return;
    }
    /** Tab leaves the menu; let focus move on naturally. */
    if (event.key === "Tab") {
      setOpen(false);
      return;
    }
    const buttons = itemRefs.current.filter((item): item is HTMLButtonElement => item !== null);
    const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const next = nextFocusIndex(event.key, current, buttons.length);
    if (next === null) return;
    event.preventDefault();
    buttons[next]?.focus();
  };

  if (items.length === 0) return null;

  return (
    <div className="more" ref={rootRef}>
      <button ref={buttonRef} className="action action-secondary" type="button" aria-haspopup="menu" aria-expanded={open}
        aria-controls={open ? menuId : undefined} onClick={() => setOpen((value) => !value)} onKeyDown={onButtonKeyDown}>
        More
        <Chevron up={open} />
      </button>
      {open && <span className="more-break" aria-hidden="true" />}
      {open && <div ref={menuRef} id={menuId} className="more-menu" role="menu" aria-label="More actions" onKeyDown={onMenuKeyDown}>
        {items.map((item, index) => (
          <button key={item.id} ref={(element) => { itemRefs.current[index] = element; }} className="more-item" type="button" role="menuitem"
            tabIndex={-1} disabled={disabled} onClick={() => selectItem(item)}>
            {item.label}
          </button>
        ))}
      </div>}
    </div>
  );
}
