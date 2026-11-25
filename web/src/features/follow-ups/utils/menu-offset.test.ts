/** Tests for where the More menu starts horizontally, lined up with the More button. */
import assert from "node:assert/strict";
import test from "node:test";
import { menuOffset } from "./menu-offset.ts";

/** A 500 px action row starting at the page's left edge, so offsets read as page positions. */
const row = { left: 0, right: 500 };

test("starts at the button's left edge when the menu fits", () => {
  assert.equal(menuOffset(row, { left: 200, right: 300 }, 150), 200);
});

test("starts at the button's left edge when the menu exactly reaches the row's end", () => {
  assert.equal(menuOffset(row, { left: 350, right: 450 }, 150), 350);
});

test("ends at the button's right edge when starting there would overflow", () => {
  assert.equal(menuOffset(row, { left: 400, right: 500 }, 150), 350);
});

test("never starts before the row when the menu is wider than the space left of the button's end", () => {
  assert.equal(menuOffset(row, { left: 0, right: 100 }, 600), 0);
});

test("measures from the row's left edge, not the page's", () => {
  assert.equal(menuOffset({ left: 50, right: 550 }, { left: 250, right: 350 }, 150), 200);
});
