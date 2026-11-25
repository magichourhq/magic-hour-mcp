import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import type { FollowUp } from "./followUps";
import { menuOffset } from "./utils/menuOffset";
import "./MoreMenu.css";

/**
 * The arrow on the More button. It points up while the menu is closed, toward where the
 * menu opens, and down while it's open; the rotation is animated in `MoreMenu.css`.
 * @param props.up `true` while the arrow should point up.
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
 * The menu opens upward and floats over the card's media, so the card keeps its height
 * and the conversation around it doesn't move. It lines up with the More button and stays
 * inside the action row's width, even in a narrow card. It works with the keyboard and
 * closes on Esc, Tab, an outside click or a selection.
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
  /** Set when ↑ opens the menu, so focus starts on the last item, the one next to the button. */
  const focusLastOnOpen = useRef(false);

  /** Aligns the open menu with the More button, and again when the card is resized. */
  useLayoutEffect(() => {
    if (!open) return;
    /** Measures the row, button and menu, and lines the menu up with the button. */
    const align = () => {
      const menu = menuRef.current;
      /** The menu must stay inside the action row, the parent of `.more`. */
      const row = rootRef.current?.parentElement;
      const button = buttonRef.current;
      if (!menu || !row || !button) return;
      const rowBox = row.getBoundingClientRect();
      const buttonBox = button.getBoundingClientRect();
      /** `menuOffset` measures from the row; the menu is positioned from `.more`, which starts at the button. */
      const offset = menuOffset(rowBox, buttonBox, menu.offsetWidth);
      menu.style.left = `${offset - (buttonBox.left - rowBox.left)}px`;
    };
    align();
    window.addEventListener("resize", align);
    return () => window.removeEventListener("resize", align);
  }, [open]);

  /**
   * Focuses the first item when the menu opens, or the last one when ↑ opened it, and
   * closes the menu on a click outside.
   */
  useEffect(() => {
    if (!open) return;
    const items = itemRefs.current.filter((item): item is HTMLButtonElement => item !== null);
    (focusLastOnOpen.current ? items[items.length - 1] : items[0])?.focus();
    focusLastOnOpen.current = false;
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
   * Opens the menu from the keyboard: ↑ focuses the last item and ↓ the first. Enter and
   * Space already open it through `onClick`, on the first item.
   * @param event The key event on the button.
   */
  const onButtonKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if ((event.key !== "ArrowUp" && event.key !== "ArrowDown") || open) return;
    event.preventDefault();
    focusLastOnOpen.current = event.key === "ArrowUp";
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
        <Chevron up={!open} />
      </button>
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
