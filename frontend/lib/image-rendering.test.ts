import assert from "node:assert/strict";
import test from "node:test";

import { fallbackImages, inlineImages } from "./image-rendering.ts";

const images = [
  { n: 1, url: "/images/one.png" },
  { n: 2, url: "/images/two.png" },
];

test("inlineImages only renders image numbers supplied by the backend", () => {
  assert.equal(
    inlineImages("第一步[图1]，不存在的图[图9]。", images),
    "第一步![图1](/images/one.png)，不存在的图。",
  );
});

test("fallbackImages exposes real images when the answer omitted every image reference", () => {
  assert.deepEqual(fallbackImages("退货入库分为两步。", images), images);
});

test("fallbackImages does not append unselected images after one valid inline image", () => {
  assert.deepEqual(fallbackImages("第一步这样操作。[图1]", images), []);
});

test("an invented image number does not suppress the safe fallback", () => {
  assert.deepEqual(fallbackImages("请看并不存在的截图[图9]。", images), images);
});
