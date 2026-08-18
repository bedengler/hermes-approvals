(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;
  const React = SDK.React, h = React.createElement;
  const base = "/api/plugins/approvals";
  function ApprovalPage() {
    const [pending, setPending] = React.useState([]), [history, setHistory] = React.useState([]);
    const [error, setError] = React.useState(null), [busy, setBusy] = React.useState(null);
    const load = React.useCallback(async function () {
      try {
        const [p, r] = await Promise.all([SDK.fetchJSON(base + "/pending"), SDK.fetchJSON(base + "/history")]);
        setPending(p.items); setHistory(r.items); setError(null);
      } catch (e) { setError(e.message); }
    }, []);
    React.useEffect(function () { load(); const timer = setInterval(load, 5000); return () => clearInterval(timer); }, [load]);
    async function decide(item, decision) {
      setBusy(item.request_id); setError(null);
      try {
        await SDK.fetchJSON(base + "/" + encodeURIComponent(item.request_id) + "/respond", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({decision, expected_version: item.version, nonce: item.nonce})});
        await load();
      } catch (e) { setError(e.message); await load(); } finally { setBusy(null); }
    }
    const row = item => h("li", {key: item.request_id, className: "flex flex-col gap-2 border-b border-(--ui-stroke-secondary) py-3"},
      h("div", {className: "font-medium"}, item.explanation),
      h("div", {className: "text-(--ui-text-tertiary) text-xs"}, item.source + " · " + new Date(item.created_at * 1000).toLocaleString()),
      h("div", {className: "flex gap-2"}, h("button", {disabled: !!busy, className: "rounded px-2 py-1 bg-(--ui-accent) text-(--ui-accent-foreground)", onClick: () => decide(item, "approve")}, "Approve"), h("button", {disabled: !!busy, className: "rounded px-2 py-1 border border-(--ui-stroke-secondary)", onClick: () => decide(item, "deny")}, "Deny")));
    return h("main", {className: "flex h-full flex-col gap-4 overflow-auto p-4 text-sm"},
      h("header", {className: "flex items-center justify-between"}, h("h1", {className: "text-lg font-semibold"}, "Approvals"), h("button", {onClick: load, className: "text-(--ui-text-secondary)"}, "Refresh")),
      error && h("div", {role: "alert", className: "rounded border border-(--ui-danger) p-2 text-(--ui-danger)"}, error),
      h("section", null, h("h2", {className: "font-medium"}, "Pending (" + pending.length + ")"), pending.length ? h("ul", null, pending.map(row)) : h("p", {className: "py-3 text-(--ui-text-tertiary)"}, "No pending approvals.")),
      h("section", {className: "mt-4"}, h("h2", {className: "font-medium"}, "History"), h("ul", null, history.map(item => h("li", {key: item.request_id, className: "border-b border-(--ui-stroke-secondary) py-2"}, item.explanation + " · " + item.status))))
    );
  }
  if (window.__HERMES_PLUGINS__ && typeof window.__HERMES_PLUGINS__.register === "function") {
    window.__HERMES_PLUGINS__.register("approvals", ApprovalPage);
  }
})();
