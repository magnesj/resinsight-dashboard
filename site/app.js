"use strict";

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child != null) node.append(child);
  }
  return node;
}

function ago(iso) {
  if (!iso) return "unknown";
  const minutes = Math.round((Date.now() - new Date(iso)) / 60000);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

// Link only http(s) URLs coming from the data file
function link(text, url) {
  return /^https:\/\//.test(url || "") ? el("a", { href: url, target: "_blank", rel: "noopener" }, text) : text;
}

function workflowCard(w) {
  const status = w.status || "unknown";
  const card = el("div", { class: `card ${status}` },
    el("h3", {}, link(w.name, w.url)),
    el("span", { class: `badge ${status}` }, status));

  if (w.error) card.append(el("div", { class: "muted" }, w.error));
  if (w.updated_at) card.append(el("div", { class: "muted" }, `${ago(w.updated_at)} · ${w.event || ""} · ${w.sha || ""}`));
  if (status === "running" && w.last_finished) {
    card.append(el("div", { class: "muted" }, "Last finished: ", link(w.last_finished.status, w.last_finished.url)));
  }
  if (Array.isArray(w.jobs) && w.jobs.length) {
    const list = el("ul", { class: "jobs" });
    for (const job of w.jobs) {
      list.append(el("li", {}, el("span", { class: `dot ${job.status}` }), link(job.name, job.url)));
    }
    card.append(list);
  }
  return card;
}

function testResultCard(w) {
  const status = w.status || "unknown";
  const card = el("div", { class: `card ${status}` },
    el("h3", {}, link(w.name, w.url)),
    el("span", { class: `badge ${status}` }, status));
  if (w.error) card.append(el("div", { class: "muted" }, w.error));
  if (w.updated_at) card.append(el("div", { class: "muted" }, `${ago(w.updated_at)} · ${w.sha || ""}`));

  const list = el("ul", { class: "jobs" });
  for (const report of w.reports || []) {
    const label = report.name.replace(/^(gtest|pytest)-results-/, "$1 ");
    const summary = report.error
      ? report.error
      : `${report.passed} passed, ${report.failed} failed, ${report.skipped} skipped`;
    const item = el("li", {}, el("span", { class: `dot ${report.status}` }), `${label}: ${summary}`);
    if (report.failed_tests && report.failed_tests.length) {
      item.append(el("ul", {}, ...report.failed_tests.map((t) => el("li", {}, t))));
    }
    list.append(item);
  }
  if (list.children.length) card.append(list);
  return card;
}

function fill(id, workflows) {
  $(id).replaceChildren(...workflows.map(workflowCard));
}

function releasesSection(data) {
  const release = data.release || {};
  const nightly = data.nightly || {};
  const nightlyStatus = nightly.status || "unknown";

  const releaseCard = el("div", { class: "card success" },
    el("h3", {}, "Latest public release"),
    release.tag ? el("div", {}, link(release.tag, release.url)) : el("div", { class: "muted" }, release.error || "unknown"),
    el("div", { class: "muted" }, release.published_at ? new Date(release.published_at).toLocaleDateString() : ""));

  const nightlyCard = el("div", { class: `card ${nightlyStatus}` },
    el("h3", {}, link("Nightly binaries (dev-latest)", nightly.url)),
    el("span", { class: `badge ${nightlyStatus}` }, nightlyStatus),
    el("div", { class: "muted" }, nightly.published_at ? `updated ${ago(nightly.published_at)}` : nightly.error || ""));
  if (nightlyStatus === "stale") nightlyCard.append(el("div", { class: "muted" }, "Older than 36 h"));
  if (nightly.assets) {
    const list = el("ul");
    for (const asset of nightly.assets) {
      const size = asset.size >= 1048576 ? `${(asset.size / 1048576).toFixed(0)} MB` : `${Math.max(1, Math.round(asset.size / 1024))} KB`;
      list.append(el("li", {}, link(`${asset.name} (${size})`, asset.url)));
    }
    nightlyCard.append(list);
  }
  $("releases").replaceChildren(releaseCard, nightlyCard);
}

async function main() {
  let data;
  try {
    const response = await fetch("data/status.json", { cache: "no-cache" });
    data = await response.json();
  } catch (error) {
    $("overall").textContent = "no data";
    $("overall").className = "badge";
    return;
  }

  $("overall").textContent = data.overall;
  $("overall").className = `badge ${data.overall}`;
  $("updated").textContent = `Updated ${ago(data.generated_at)}`;

  const head = data.head || {};
  $("head").replaceChildren(
    head.sha
      ? el("span", {}, `${data.branch} @ `, link(head.sha, head.url), ` — ${head.message} (${head.author}, ${ago(head.date)})`)
      : el("span", {}, head.error || ""));

  fill("builds", data.builds || []);
  fill("tests", data.tests || []);
  $("test-results").replaceChildren(...(data.test_results || []).map(testResultCard));
  fill("quality", data.quality || []);
  releasesSection(data);
  $("links").replaceChildren(...(data.links || []).map((l) => el("li", {}, link(l.name, l.url))));
}

main();
