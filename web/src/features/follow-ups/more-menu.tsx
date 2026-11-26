import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import type { FollowUp } from "./follow-ups";
import "./more-menu.css";

function Chevron({ up }: { up: boolean }) {
  return (
    <span className="more-chevron" data-up={up} aria-hidden="true">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="m6 9 6 6 6-6" />
      </svg>
    </span>
  );
}

function nextFocusIndex(key: string, current: number, count: number): number | null {
  switch (key) {
    case "ArrowDown": return (current + 1) % count;
    case "ArrowUp": return (current - 1 + count) % count;
    case "Home": return 0;
    case "End": return count - 1;
    default: return null;
  }
}

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
  const focusLastOnOpen = useRef(false);

  useLayoutEffect(() => {
    if (!open) return;
    const align = () => {
      const menu = menuRef.current;
      const row = rootRef.current?.parentElement;
      const button = buttonRef.current;
      if (!menu || !row || !button) return;
      const rowBox = row.getBoundingClientRect();
      const buttonBox = button.getBoundingClientRect();
      const buttonStart = buttonBox.left - rowBox.left;
      const fits = buttonStart + menu.offsetWidth <= rowBox.width;
      const offset = fits ? buttonStart : Math.max(0, buttonBox.right - rowBox.left - menu.offsetWidth);
      /** `offset` is measured from the row, but `left` is relative to `.more`, which starts at the button. */
      menu.style.left = `${offset - buttonStart}px`;
    };
    align();
    window.addEventListener("resize", align);
    return () => window.removeEventListener("resize", align);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const items = itemRefs.current.filter((item): item is HTMLButtonElement => item !== null);
    (focusLastOnOpen.current ? items[items.length - 1] : items[0])?.focus();
    focusLastOnOpen.current = false;
    const closeOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [open]);

  const closeAndFocusButton = () => {
    setOpen(false);
    buttonRef.current?.focus();
  };

  const selectItem = (item: FollowUp) => {
    if (disabled) return;
    closeAndFocusButton();
    onSelect(item);
  };

  const onButtonKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if ((event.key !== "ArrowUp" && event.key !== "ArrowDown") || open) return;
    event.preventDefault();
    focusLastOnOpen.current = event.key === "ArrowUp";
    setOpen(true);
  };

  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape" || (event.key === "Tab" && event.shiftKey)) {
      event.preventDefault();
      closeAndFocusButton();
      return;
    }
    /**
     * Tab leaves the menu. Focus goes back to More first, so the browser's own Tab moves on
     * from the button instead of from an item that's about to be removed. Shift+Tab stops on
     * More (above), since the menu sits right after it.
     */
    if (event.key === "Tab") {
      buttonRef.current?.focus();
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
            tabIndex={-1} aria-disabled={disabled} onClick={() => selectItem(item)}>
            {item.label}
          </button>
        ))}
      </div>}
    </div>
  );
}
