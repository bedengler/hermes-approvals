"use strict";

const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");

async function main() {
  const bundlePath = process.argv[2];
  const bundle = fs.readFileSync(bundlePath, "utf8");
  const state = [];
  const requests = [];
  const failReads = process.argv[3] === "fail";
  let hookIndex = 0;
  let effect;
  let effectStarted = false;
  let ApprovalPage;

  const React = {
    createElement(type, props, ...children) {
      if (typeof type === "function") return type({ ...(props || {}), children });
      return { type, props: props || {}, children };
    },
    useState(initial) {
      const index = hookIndex++;
      if (!(index in state)) state[index] = initial;
      return [state[index], value => {
        state[index] = typeof value === "function" ? value(state[index]) : value;
      }];
    },
    useCallback(callback) { return callback; },
    useEffect(callback) {
      if (!effectStarted) {
        effectStarted = true;
        effect = callback;
      }
    },
    useRef(initial) {
      const index = hookIndex++;
      if (!(index in state)) state[index] = { current: initial };
      return state[index];
    },
  };

  const fixtures = {
    "/api/plugins/approvals/pending": {
      profile: "synthetic-profile",
      items: [{ request_id: "runtime-pending", explanation: "synthetic pending", source: "test", created_at: 1 }],
    },
    "/api/plugins/approvals/history": {
      profile: "synthetic-profile",
      items: [{ request_id: "runtime-history", explanation: "synthetic resolved", status: "approved" }],
    },
    "/api/plugins/approvals/governance/pending": {
      items: [{ approval_id: "governance-pending", gate: "test", target: "fixture", status: "pending" }],
    },
    "/api/plugins/approvals/governance/history": {
      items: [{ approval_id: "governance-history", gate: "test", target: "fixture", status: "denied" }],
    },
  };
  const window = {
    __HERMES_PLUGIN_SDK__: {
      React,
      async fetchJSON(url, options) {
        requests.push({ url, options });
        if (failReads) throw new Error("synthetic read failure");
        return fixtures[url];
      },
    },
    __HERMES_PLUGINS__: {
      register(_name, component) { ApprovalPage = component; },
    },
  };

  vm.runInNewContext(bundle, { window, setInterval: () => 1, clearInterval() {} });
  assert.equal(typeof ApprovalPage, "function", "bundle registers the approvals page");

  function render() {
    hookIndex = 0;
    return ApprovalPage();
  }
  function descendants(node) {
    if (Array.isArray(node)) return node.flatMap(descendants);
    if (node === null || node === undefined || typeof node !== "object") return [];
    return [node, ...node.children.flatMap(descendants)];
  }
  function text(node) {
    if (Array.isArray(node)) return node.map(text).join(" ");
    if (node === null || node === undefined || typeof node === "boolean") return "";
    if (typeof node !== "object") return String(node);
    return node.children.map(text).join(" ");
  }

  render();
  effect();
  await new Promise(resolve => setImmediate(resolve));
  const tree = render();
  const elements = descendants(tree);
  const buttons = elements.filter(node => node.type === "button");
  assert.deepEqual(buttons.map(button => text(button.children)), ["Refresh"]);
  assert.equal(requests.length, 4);
  assert.deepEqual(requests.map(request => request.url).sort(), Object.keys(fixtures).sort());
  assert.ok(requests.every(request => request.options === undefined), "page makes read-only requests");
  const rendered = text(tree);
  if (failReads) {
    assert.ok(rendered.includes("Refresh failed. Please try again."));
    assert.ok(rendered.includes("Runtime command approvals (—)"));
    assert.ok(rendered.includes("Runtime history (—)"));
    assert.ok(!rendered.includes("No pending runtime approvals."));
    assert.ok(!rendered.includes("Runtime command approvals (0)"));
    assert.equal(requests.length, 4);
    assert.ok(requests.every(request => request.options === undefined));
    return;
  }
  for (const expected of [
    "Runtime command approvals (1)",
    "Runtime history (1)",
    "Pending (1)",
    "History (1)",
    "synthetic pending",
    "synthetic resolved",
    "governance-pending",
    "governance-history",
  ]) assert.ok(rendered.includes(expected), `rendered view includes ${expected}`);

  await buttons[0].props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests.length, 8, "refresh repeats only the four read requests");
  assert.ok(requests.every(request => request.options === undefined));
  assert.ok(requests.every(request => !/\/respond(?:\?|$)/.test(request.url)));
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
