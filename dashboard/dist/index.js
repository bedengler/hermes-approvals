(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  const React = SDK.React;
  const h = React.createElement;
  const base = "/api/plugins/approvals";
  const secondary = "rounded border border-(--ui-stroke-secondary) px-2 py-1";
  const safeError = "Refresh failed. Please try again.";

  function itemCount(loaded, items) {
    return loaded ? String(items.length) : "—";
  }

  function ReadOnlyList({ title, items, empty, loaded }) {
    return h(
      "section",
      { className: "mt-4" },
      h("h2", { className: "font-medium" }, title + " (" + itemCount(loaded, items) + ")"),
      !loaded
        ? h("p", { className: "py-3 text-(--ui-text-tertiary)" }, "Approval data is not available yet.")
        : items.length
          ? h("ul", null, items.map(item => h(
            "li",
            { key: item.approval_id, className: "border-b border-(--ui-stroke-secondary) py-3" },
            h("div", { className: "font-medium" }, item.approval_id + " · " + item.status),
            h("div", { className: "text-(--ui-text-secondary)" }, "Gate: " + item.gate + " · Target: " + item.target),
            h("div", { className: "text-(--ui-text-tertiary) text-xs" }, "Expires: " + (item.expires_at || "n/a")),
            item.rationale && h("div", { className: "mt-1" }, item.rationale),
            item.decision_note && h("div", { className: "text-(--ui-text-tertiary)" }, "Decision note: " + item.decision_note)
          )))
          : h("p", { className: "py-3 text-(--ui-text-tertiary)" }, empty)
    );
  }

  function ApprovalPage() {
    const [pending, setPending] = React.useState([]);
    const [history, setHistory] = React.useState([]);
    const [governancePending, setGovernancePending] = React.useState([]);
    const [governanceHistory, setGovernanceHistory] = React.useState([]);
    const [refreshing, setRefreshing] = React.useState(false);
    const [loaded, setLoaded] = React.useState(false);
    const [statusText, setStatusText] = React.useState("Loading approvals…");
    const previousSnapshot = React.useRef(null);

    const load = React.useCallback(async function () {
      setRefreshing(true);
      try {
        const responses = await Promise.all([
          SDK.fetchJSON(base + "/pending"),
          SDK.fetchJSON(base + "/history"),
          SDK.fetchJSON(base + "/governance/pending"),
          SDK.fetchJSON(base + "/governance/history")
        ]);
        const snapshot = JSON.stringify(responses.map(response => response.items));
        const unchanged = previousSnapshot.current !== null && previousSnapshot.current === snapshot;
        previousSnapshot.current = snapshot;
        setPending(responses[0].items);
        setHistory(responses[1].items);
        setGovernancePending(responses[2].items);
        setGovernanceHistory(responses[3].items);
        setLoaded(true);
        const stamp = new Date().toLocaleTimeString();
        const pendingCount = responses[0].items.length + responses[2].items.length;
        setStatusText(unchanged
          ? (pendingCount ? "No changes." : "No changes; nothing pending.")
          : "Loaded at " + stamp + ".");
      } catch (_error) {
        setStatusText(safeError);
      } finally {
        setRefreshing(false);
      }
    }, []);

    React.useEffect(function () {
      load();
      const timer = setInterval(load, 5000);
      return () => clearInterval(timer);
    }, [load]);

    const runtimePending = loaded
      ? pending.length
        ? h("ul", null, pending.map(item => h(
          "li",
          { key: item.request_id, className: "flex flex-col gap-2 border-b border-(--ui-stroke-secondary) py-3" },
          h("div", { className: "font-medium" }, item.explanation),
          h("div", { className: "text-(--ui-text-tertiary) text-xs" }, item.source + " · " + new Date(item.created_at * 1000).toLocaleString())
        )))
        : h("p", { className: "py-3 text-(--ui-text-tertiary)" }, "No pending runtime approvals.")
      : h("p", { className: "py-3 text-(--ui-text-tertiary)" }, "Approval data is not available yet.");

    const runtimeHistory = loaded
      ? history.length
        ? h("ul", null, history.map(item => h(
          "li",
          { key: item.request_id, className: "border-b border-(--ui-stroke-secondary) py-2" },
          item.explanation + " · " + item.status
        )))
        : h("p", { className: "py-3 text-(--ui-text-tertiary)" }, "No runtime approval history.")
      : h("p", { className: "py-3 text-(--ui-text-tertiary)" }, "Approval data is not available yet.");

    return h(
      "main",
      { className: "flex h-full flex-col gap-4 overflow-auto p-4 text-sm" },
      h(
        "header",
        { className: "flex items-center justify-between" },
        h("h1", { className: "text-lg font-semibold" }, "Approvals"),
        h("button", { disabled: refreshing, onClick: load, className: secondary, "aria-busy": refreshing }, refreshing ? "Refreshing…" : "Refresh")
      ),
      h("div", { role: statusText === safeError ? "alert" : "status", "aria-live": "polite", className: "text-(--ui-text-tertiary) text-xs" }, "Last refreshed: " + statusText),
      h("section", null,
        h("h2", { className: "font-medium" }, "Runtime command approvals (" + itemCount(loaded, pending) + ")"),
        runtimePending
      ),
      h("section", { className: "mt-4" },
        h("h2", { className: "font-medium" }, "Runtime history (" + itemCount(loaded, history) + ")"),
        runtimeHistory
      ),
      h("div", { className: "mt-6 border-t border-(--ui-stroke-secondary) pt-4" },
        h("h1", { className: "text-lg font-semibold" }, "Governance Approvals"),
        h(ReadOnlyList, { title: "Pending", items: governancePending, empty: "No pending governance approvals.", loaded }),
        h(ReadOnlyList, { title: "History", items: governanceHistory, empty: "No governance approval history.", loaded })
      )
    );
  }

  if (window.__HERMES_PLUGINS__ && typeof window.__HERMES_PLUGINS__.register === "function") {
    window.__HERMES_PLUGINS__.register("approvals", ApprovalPage);
  }
})();
