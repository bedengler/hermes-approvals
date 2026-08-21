(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;
  const React = SDK.React, h = React.createElement;
  const base = "/api/plugins/approvals";
  const interactive = "cursor-pointer rounded px-2 py-1 transition-opacity hover:opacity-80 focus-visible:outline-2 focus-visible:outline-(--ui-accent) focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-60";
  const primary = interactive + " bg-(--ui-accent) text-(--ui-accent-foreground)";
  const secondary = interactive + " border border-(--ui-stroke-secondary)";
  const safeError = "Refresh failed. Please try again.";

  function GovernanceList({title, items, empty, onDecide, busy}) {
    return h("section", {className: "mt-4"}, h("h2", {className: "font-medium"}, title + " (" + items.length + ")"), items.length ? h("ul", null, items.map(item => h("li", {key: item.approval_id, className: "border-b border-(--ui-stroke-secondary) py-3"},
      h("div", {className: "font-medium"}, item.approval_id + " · " + item.status),
      h("div", {className: "text-(--ui-text-secondary)"}, "Gate: " + item.gate + " · Target: " + item.target),
      h("div", {className: "text-(--ui-text-tertiary) text-xs"}, "Expires: " + (item.expires_at || "n/a")),
      item.rationale && h("div", {className: "mt-1"}, item.rationale),
      item.decision_note && h("div", {className: "text-(--ui-text-tertiary)"}, "Decision note: " + item.decision_note),
      item.status === "pending" && h("div", {className: "flex gap-2 mt-2"},
        h("button", {disabled: !!busy, onClick: () => onDecide(item, "approve"), className: primary}, "Approve"),
        h("button", {disabled: !!busy, onClick: () => onDecide(item, "deny"), className: secondary}, "Deny"))
    ))) : h("p", {className: "py-3 text-(--ui-text-tertiary)"}, empty));
  }

  function ApprovalPage() {
    const [pending, setPending] = React.useState([]), [history, setHistory] = React.useState([]);
    const [governancePending, setGovernancePending] = React.useState([]), [governanceHistory, setGovernanceHistory] = React.useState([]);
    const [busy, setBusy] = React.useState(null), [confirming, setConfirming] = React.useState(null);
    const [refreshing, setRefreshing] = React.useState(false), [statusText, setStatusText] = React.useState("Loading approvals…");
    const [decisionOutcome, setDecisionOutcome] = React.useState(null);
    const previousSnapshot = React.useRef(null);

    const load = React.useCallback(async function () {
      setRefreshing(true);
      try {
        const responses = await Promise.all([SDK.fetchJSON(base + "/pending"), SDK.fetchJSON(base + "/history"), SDK.fetchJSON(base + "/governance/pending"), SDK.fetchJSON(base + "/governance/history")]);
        const snapshot = JSON.stringify(responses.map(response => response.items));
        const unchanged = previousSnapshot.current !== null && previousSnapshot.current === snapshot;
        previousSnapshot.current = snapshot;
        setPending(responses[0].items); setHistory(responses[1].items); setGovernancePending(responses[2].items); setGovernanceHistory(responses[3].items);
        const stamp = new Date().toLocaleTimeString();
        setStatusText(unchanged ? (responses[0].items.length + responses[2].items.length ? "No changes." : "No changes; nothing pending.") : "Loaded at " + stamp + ".");
      } catch (_e) {
        setStatusText(safeError);
      } finally {
        setRefreshing(false);
      }
    }, []);
    React.useEffect(function () { load(); const timer = setInterval(load, 5000); return () => clearInterval(timer); }, [load]);

    function requestDecision(item, decision) {
      if (!busy) { setDecisionOutcome(null); setConfirming({item, decision}); }
    }
    async function decideGovernance() {
      if (!confirming || busy) return;
      const item = confirming.item, decision = confirming.decision;
      setBusy(item.approval_id); setDecisionOutcome(null); setStatusText("Submitting decision…");
      try {
        await SDK.fetchJSON(base + "/governance/" + encodeURIComponent(item.approval_id) + "/respond", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({decision})});
        setConfirming(null); setDecisionOutcome({kind: "success", text: "Decision submitted successfully."});
      } catch (e) {
        const status = e && e.status;
        const text = status === 409 ? "Decision was stale or expired; nothing was changed." : status === 404 ? "Approval was not found; nothing was changed." : "Decision failed; nothing was changed.";
        setConfirming(null); setDecisionOutcome({kind: "error", text});
      } finally {
        setBusy(null);
        await load();
      }
    }
    async function decideRuntime(item, decision) {
      if (!busy) { setBusy(item.request_id); setDecisionOutcome(null); try { await SDK.fetchJSON(base + "/" + encodeURIComponent(item.request_id) + "/respond", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({decision, expected_version: item.version, nonce: item.nonce})}); setDecisionOutcome({kind: "success", text: "Decision submitted successfully."}); } catch (_e) { setDecisionOutcome({kind: "error", text: "Decision failed; nothing was changed."}); } finally { setBusy(null); await load(); } }
    }
    const row = item => h("li", {key: item.request_id, className: "flex flex-col gap-2 border-b border-(--ui-stroke-secondary) py-3"}, h("div", {className: "font-medium"}, item.explanation), h("div", {className: "text-(--ui-text-tertiary) text-xs"}, item.source + " · " + new Date(item.created_at * 1000).toLocaleString()), h("div", {className: "flex gap-2"}, h("button", {disabled: !!busy, className: primary, onClick: () => decideRuntime(item, "approve")}, "Approve"), h("button", {disabled: !!busy, className: secondary, onClick: () => decideRuntime(item, "deny")}, "Deny")));
    const confirmation = confirming && h("div", {className: "fixed inset-0 z-10 flex items-center justify-center bg-(--ui-bg)/80 p-4 backdrop-blur-sm", onKeyDown: event => { if (event.key === "Escape" && !busy) setConfirming(null); }},
      h("div", {role: "dialog", "aria-modal": "true", "aria-labelledby": "approval-confirm-title", "aria-describedby": "approval-confirm-details", tabIndex: -1, "data-modal": "governance-confirmation", className: "flex w-full max-w-lg max-h-[calc(100vh-2rem)] flex-col overflow-y-auto rounded-lg border border-(--ui-stroke-secondary) bg-(--ui-surface-background) p-4 text-(--ui-text-primary) shadow-lg"},
        h("h2", {id: "approval-confirm-title", className: "font-semibold"}, "Confirm governance decision"),
        h("div", {id: "approval-confirm-details", className: "mt-3 space-y-1 text-(--ui-text-secondary)"},
          h("p", null, "Approval ID: " + confirming.item.approval_id),
          h("p", null, "Gate: " + confirming.item.gate), h("p", null, "Target: " + confirming.item.target),
          h("p", null, "Expiry: " + (confirming.item.expires_at || "n/a")),
          h("p", null, "Rationale (redacted): " + (confirming.item.rationale || "none"))),
        h("p", {className: "mt-3 font-medium text-(--ui-text-primary)"}, "Selected decision: " + confirming.decision.toUpperCase()),
        h("p", {className: "mt-2 text-(--ui-text-secondary)"}, "This is the final confirmation. Verify the exact approval ID before submitting."),
        h("div", {className: "mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end"}, h("button", {disabled: !!busy, onClick: decideGovernance, className: primary}, busy ? "Submitting…" : "Confirm final " + confirming.decision), h("button", {disabled: !!busy, onClick: () => setConfirming(null), className: secondary}, "Cancel"))));
    return h("main", {className: "flex h-full flex-col gap-4 overflow-auto p-4 text-sm"},
      h("header", {className: "flex items-center justify-between"}, h("h1", {className: "text-lg font-semibold"}, "Approvals"), h("button", {disabled: refreshing, onClick: load, className: secondary, "aria-busy": refreshing}, refreshing ? "Refreshing…" : "Refresh")),
      h("div", {role: "status", "aria-live": "polite", className: "text-(--ui-text-tertiary) text-xs"}, "Last refreshed: " + statusText),
      decisionOutcome && h("div", {role: decisionOutcome.kind === "error" ? "alert" : "status", className: "rounded border border-(--ui-stroke-secondary) p-2"}, decisionOutcome.text),
      h("section", null, h("h2", {className: "font-medium"}, "Runtime command approvals (" + pending.length + ")"), pending.length ? h("ul", null, pending.map(row)) : h("p", {className: "py-3 text-(--ui-text-tertiary)"}, "No pending runtime approvals.")),
      h("section", {className: "mt-4"}, h("h2", {className: "font-medium"}, "Runtime history"), h("ul", null, history.map(item => h("li", {key: item.request_id, className: "border-b border-(--ui-stroke-secondary) py-2"}, item.explanation + " · " + item.status))),
      h("div", {className: "mt-6 border-t border-(--ui-stroke-secondary) pt-4"}, h("h1", {className: "text-lg font-semibold"}, "Governance Approvals"), h("p", {className: "text-(--ui-text-tertiary) text-xs"}, "Governance decisions require an explicit second confirmation and the authenticated dashboard authorization callback."), confirmation, h(GovernanceList, {title: "Pending", items: governancePending, empty: "No pending governance approvals.", onDecide: requestDecision, busy}), h(GovernanceList, {title: "History", items: governanceHistory, empty: "No governance approval history.", onDecide: requestDecision, busy}))));
  }
  if (window.__HERMES_PLUGINS__ && typeof window.__HERMES_PLUGINS__.register === "function") window.__HERMES_PLUGINS__.register("approvals", ApprovalPage);
})();
