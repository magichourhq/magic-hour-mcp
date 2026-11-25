/** Tests for where the More menu starts under the More button. */
import assert from "node:assert/strict";
import test from "node:test";
import { menuOffset } from "./menuOffset.ts";

/** The action row from the 520 px local card check: 18 px padding on each side. */
const row = { left: 18, right: 502 };

test("starts under the button's left edge when the menu fits", () => {
  assert.equal(menuOffset(row, { left: 271, right: 361 }, 171), 253);
});

test("ends at the button's right edge when starting there would overflow", () => {
  assert.equal(menuOffset(row, { left: 347, right: 437 }, 195), 224);
});

test("never starts before the row when the menu is wider than the space left of the button's end", () => {
  assert.equal(menuOffset(row, { left: 18, right: 108 }, 600), 0);
});
