/*
 * Progressive enhancement & real-time updates for the Patchwork dashboard.
 * Pure vanilla JavaScript: theme toggle, clipboard, live Server-Sent Events,
 * job re-runs, and real-time execution log streaming.
 */
(function () {
  "use strict";

  var root = document.documentElement;
  var SUN_PATH = "M8 12a4 4 0 1 1 0-8 4 4 0 0 1 0 8ZM8 0a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0V.75A.75.75 0 0 1 8 0Zm0 13a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0v-1.5A.75.75 0 0 1 8 13ZM2.343 2.343a.75.75 0 0 1 1.061 0l1.06 1.061a.751.751 0 0 1-.018 1.042.751.751 0 0 1-1.042.018l-1.06-1.06a.75.75 0 0 1 0-1.061Zm8.536 8.536a.75.75 0 0 1 1.06 0l1.06 1.06a.75.75 0 0 1-1.06 1.061l-1.06-1.06a.75.75 0 0 1 0-1.06ZM0 8a.75.75 0 0 1 .75-.75h1.5a.75.75 0 0 1 0 1.5H.75A.75.75 0 0 1 0 8Zm13 0a.75.75 0 0 1 .75-.75h1.5a.75.75 0 0 1 0 1.5h-1.5A.75.75 0 0 1 13 8ZM2.343 13.657a.75.75 0 0 1 0-1.061l1.061-1.06a.751.751 0 0 1 1.042.018.751.751 0 0 1 .018 1.042l-1.06 1.06a.75.75 0 0 1-1.061 0Zm8.536-8.536a.75.75 0 0 1 0-1.06l1.06-1.06a.75 75 0 1 1 1.061 1.06l-1.06 1.06a.75.75 0 0 1-1.06 0Z";
  var MOON_PATH = "M9.598 1.591a.749.749 0 0 1 .785-.175 7.001 7.001 0 1 1-8.967 8.967.75.75 0 0 1 .961-.96 5.5 5.5 0 0 0 7.046-7.046.75.75 0 0 1 .175-.786Z";
  var CHECK_PATH = "M13.78 4.22a.75.75 0 0 1 0 1.06l-7.25 7.25a.75.75 0 0 1-1.06 0L2.22 9.28a.751.751 0 0 1 .018-1.042.751.751 0 0 1 1.042-.018L6 10.94l6.72-6.72a.75.75 0 0 1 1.06 0Z";
  var X_PATH = "M3.72 3.72a.75.75 0 0 1 1.06 0L8 6.94l3.22-3.22a.749.749 0 0 1 1.275.326.749.749 0 0 1-.215.734L9.06 8l3.22 3.22a.749.749 0 0 1-.326 1.275.749.749 0 0 1-.734-.215L8 9.06l-3.22 3.22a.751.751 0 0 1-1.042-.018.751.751 0 0 1-.018-1.042L6.94 8 3.72 4.78a.75.75 0 0 1 0-1.06Z";
  var SYNC_PATH = "M1.705 8.005a.75.75 0 0 1 .834.656 5.5 5.5 0 0 0 9.592 2.97l-1.204-1.204a.25.25 0 0 1 .177-.427h3.646a.25.25 0 0 1 .25.25v3.646a.25.25 0 0 1-.427.177l-1.38-1.38A7.002 7.002 0 0 1 1.05 8.84a.75.75 0 0 1 .656-.834ZM8 2.5a5.487 5.487 0 0 0-4.131 1.869l1.204 1.204A.25.25 0 0 1 4.896 6H1.25A.25.25 0 0 1 1 5.75V2.104a.25.25 0 0 1 .427-.177l1.38 1.38A7.002 7.002 0 0 1 14.95 7.16a.75.75 0 0 1-1.49.178A5.5 5.5 0 0 0 8 2.5Z";
  var STOP_PATH = "M4.25 2.5a1.75 1.75 0 0 0-1.75 1.75v7.5c0 .966.784 1.75 1.75 1.75h7.5A1.75 1.75 0 0 0 13.5 11.75v-7.5A1.75 1.75 0 0 0 11.75 2.5h-7.5Z";

  function iconMarkup(path, size) {
    size = size || 16;
    return '<svg class="octicon" viewBox="0 0 16 16" width="' + size + '" height="' + size + '" fill="currentColor" aria-hidden="true"><path d="' + path + '"/></svg>';
  }

  function currentTheme() {
    return root.getAttribute("data-color-mode") === "dark" ? "dark" : "light";
  }

  function syncThemeButton() {
    var button = document.querySelector("[data-theme-toggle]");
    if (!button) return;
    var isDark = currentTheme() === "dark";
    button.innerHTML = iconMarkup(isDark ? SUN_PATH : MOON_PATH);
    button.setAttribute("aria-label", isDark ? "Switch to light color mode" : "Switch to dark color mode");
    button.setAttribute("title", isDark ? "Switch to light color mode" : "Switch to dark color mode");
  }

  var themeToggle = document.querySelector("[data-theme-toggle]");
  if (themeToggle) {
    themeToggle.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      root.setAttribute("data-color-mode", next);
      try {
        localStorage.setItem("patchwork-theme", next);
      } catch (e) {}
      syncThemeButton();
    });
  }
  syncThemeButton();

  // Clipboard copy
  function fallbackCopy(text) {
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    document.body.removeChild(area);
  }

  document.querySelectorAll("[data-copy]").forEach(function (button) {
    button.addEventListener("click", function () {
      var text = button.getAttribute("data-copy") || "";
      var original = button.innerHTML;
      function done() {
        button.textContent = "Copied";
        window.setTimeout(function () {
          button.innerHTML = original;
        }, 1400);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(function () {
          fallbackCopy(text);
          done();
        });
      } else {
        fallbackCopy(text);
        done();
      }
    });
  });

  // ==========================================================================
  // Real-Time System: Status Helpers
  // ==========================================================================
  var STATUS_META = {
    queued: { label: "Queued", state: "pending", desc: "Waiting for background pipeline worker." },
    extracting: { label: "Extracting", state: "pending", desc: "Gathering logs, isolating crash, and parsing AST." },
    diagnosing: { label: "Diagnosing", state: "pending", desc: "Analyzing error with AI model and creating unified diff." },
    verifying: { label: "Verifying", state: "pending", desc: "Testing fix in an isolated sandbox loop." },
    pr_opened: { label: "PR opened", state: "open", desc: "Remediation verified; pull request created on GitHub." },
    flaky_rerun: { label: "Flaky rerun", state: "flaky", desc: "Transient or infrastructure failure; workflow rerun triggered." },
    failed: { label: "Failed", state: "closed", desc: "Remediation could not be completed automatically." },
    cancelled: { label: "Cancelled", state: "closed", desc: "Remediation job was stopped by user." },
    guardrail_rejected: { label: "Guardrail blocked", state: "closed", desc: "Patch rejected by a safety rule before creating PR." }
  };

  function getStatusMeta(status) {
    return STATUS_META[status] || { label: status, state: "pending", desc: status };
  }

  function renderStatusBadge(status) {
    var meta = getStatusMeta(status);
    return '<span class="State State--' + meta.state + '"><span>' + meta.label + '</span></span>';
  }

  function renderStatusIcon(status, size) {
    size = size || 16;
    var meta = getStatusMeta(status);
    var iconClass = "StateIcon StateIcon--" + meta.state;
    var iconPath = CHECK_PATH;
    if (meta.state === "pending") {
      iconPath = SYNC_PATH;
    } else if (meta.state === "closed") {
      iconPath = X_PATH;
    }
    return '<span class="' + iconClass + '">' + iconMarkup(iconPath, size) + '</span>';
  }

  function formatTime(isoString) {
    if (!isoString) return "";
    try {
      var d = new Date(isoString);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
    } catch (e) {
      return "";
    }
  }

  // ==========================================================================
  // Job Detail Page: Live Stepper & Activity Log Console
  // ==========================================================================
  var jobDetailHead = document.querySelector(".job-detail-head");
  var currentJobId = jobDetailHead ? jobDetailHead.getAttribute("data-job-id") : null;

  var consoleLogBody = document.getElementById("console-log-body");
  var autoScrollChk = document.getElementById("auto-scroll-chk");
  var clearConsoleBtn = document.getElementById("clear-console-btn");
  var streamIndicator = document.getElementById("stream-indicator");
  var streamText = document.getElementById("stream-text");

  if (clearConsoleBtn && consoleLogBody) {
    clearConsoleBtn.addEventListener("click", function () {
      consoleLogBody.innerHTML = '<div class="console-empty text-muted text-small" id="console-empty">Console cleared. Waiting for new events...</div>';
    });
  }

  function appendConsoleEvent(event) {
    if (!consoleLogBody) return;
    var emptyMsg = document.getElementById("console-empty");
    if (emptyMsg) {
      emptyMsg.remove();
    }

    // Prevent duplicate event IDs
    if (event.id && consoleLogBody.querySelector('[data-event-id="' + event.id + '"]')) {
      return;
    }

    var line = document.createElement("div");
    line.className = "console-line console-line--" + (event.level || "info");
    if (event.id) {
      line.setAttribute("data-event-id", event.id);
    }

    var timeSpan = document.createElement("span");
    timeSpan.className = "console-time";
    timeSpan.textContent = formatTime(event.created_at) || new Date().toLocaleTimeString([], { hour12: false });

    var stageSpan = document.createElement("span");
    stageSpan.className = "console-stage console-stage--" + (event.stage || "info");
    stageSpan.textContent = (event.stage || "INFO").toUpperCase();

    var msgSpan = document.createElement("span");
    msgSpan.className = "console-msg";
    msgSpan.textContent = event.message || "";

    line.appendChild(timeSpan);
    line.appendChild(stageSpan);
    line.appendChild(msgSpan);
    consoleLogBody.appendChild(line);

    if (autoScrollChk && autoScrollChk.checked) {
      consoleLogBody.scrollTop = consoleLogBody.scrollHeight;
    }
  }

  function updatePipelineStepper(status, guardrailViolation) {
    var stepper = document.getElementById("pipeline-stepper");
    if (!stepper) return;

    var order = ["queued", "extracting", "diagnosing", "verifying", "pr_opened"];
    var stageStates = { queued: "pending", extracting: "pending", diagnosing: "pending", verifying: "pending", pr_opened: "pending" };

    if (status === "queued") {
      stageStates.queued = "active";
    } else if (status === "extracting") {
      stageStates.queued = "completed";
      stageStates.extracting = "active";
    } else if (status === "diagnosing") {
      stageStates.queued = "completed";
      stageStates.extracting = "completed";
      stageStates.diagnosing = "active";
    } else if (status === "verifying") {
      stageStates.queued = "completed";
      stageStates.extracting = "completed";
      stageStates.diagnosing = "completed";
      stageStates.verifying = "active";
    } else if (status === "pr_opened") {
      order.forEach(function (s) { stageStates[s] = "completed"; });
    } else if (status === "flaky_rerun") {
      stageStates.queued = "completed";
      stageStates.extracting = "completed";
      stageStates.diagnosing = "skipped";
      stageStates.verifying = "skipped";
      stageStates.pr_opened = "completed";
    } else if (status === "guardrail_rejected") {
      if ((guardrailViolation || "").toUpperCase() === "INFINITE_LOOP_PREVENTION") {
        stageStates.queued = "failed";
      } else {
        stageStates.queued = "completed";
        stageStates.extracting = "completed";
        stageStates.diagnosing = "completed";
        stageStates.verifying = "failed";
      }
    } else if (status === "failed" || status === "cancelled") {
      stageStates.queued = "completed";
      stageStates.extracting = "completed";
      stageStates.diagnosing = "completed";
      stageStates.verifying = "failed";
    }

    order.forEach(function (stageId) {
      var node = stepper.querySelector('[data-stage-id="' + stageId + '"]');
      if (!node) return;
      var state = stageStates[stageId];
      node.className = "step-node step-node--" + state;

      var iconWrap = node.querySelector(".step-icon-wrap");
      if (iconWrap) {
        if (state === "completed") {
          iconWrap.innerHTML = iconMarkup(CHECK_PATH, 14);
        } else if (state === "active") {
          iconWrap.innerHTML = '<span class="step-spinner"></span>';
        } else if (state === "failed") {
          iconWrap.innerHTML = iconMarkup(X_PATH, 14);
        } else {
          iconWrap.innerHTML = '<span class="step-dot"></span>';
        }
      }

      var divider = stepper.querySelector('[data-divider-after="' + stageId + '"]');
      if (divider) {
        if (state === "completed") {
          divider.classList.add("is-completed");
        } else {
          divider.classList.remove("is-completed");
        }
      }
    });
  }

  function updateJobDetailUI(job) {
    if (!job || job.id !== currentJobId) return;

    var statusWrap = document.getElementById("job-status-badge-wrap");
    if (statusWrap) {
      statusWrap.innerHTML = renderStatusBadge(job.status);
    }
    var statusDesc = document.getElementById("job-status-desc");
    if (statusDesc) {
      var meta = getStatusMeta(job.status);
      statusDesc.textContent = meta.desc;
    }
    var stateIconWrap = document.getElementById("state-icon-wrap");
    if (stateIconWrap) {
      var m = getStatusMeta(job.status);
      stateIconWrap.className = "StateIcon StateIcon--" + m.state;
      stateIconWrap.innerHTML = iconMarkup(m.state === "open" ? CHECK_PATH : (m.state === "closed" ? X_PATH : SYNC_PATH), 20);
    }

    updatePipelineStepper(job.status, job.guardrail_violation);

    // Stream indicator badge
    var isActive = ["queued", "extracting", "diagnosing", "verifying"].indexOf(job.status) !== -1;
    if (streamIndicator && streamText) {
      if (isActive) {
        streamIndicator.classList.add("is-live");
        streamText.textContent = "Live";
      } else {
        streamIndicator.classList.remove("is-live");
        streamText.textContent = job.status === "pr_opened" ? "Completed" : "Finished";
      }
    }

    // Re-run button state
    var rerunBtn = document.getElementById("rerun-btn");
    if (rerunBtn) {
      var btnText = rerunBtn.querySelector(".btn-text");
      if (isActive) {
        rerunBtn.disabled = true;
        if (btnText) btnText.textContent = "Running...";
      } else {
        rerunBtn.disabled = false;
        if (btnText) btnText.textContent = "Re-run job";
      }
    }

    // Stop button state
    var stopForm = document.getElementById("stop-form");
    if (stopForm) {
      stopForm.style.display = isActive ? "inline-block" : "none";
      var stopBtn = document.getElementById("stop-btn");
      if (stopBtn) stopBtn.disabled = false;
    }

    // Root cause update
    if (job.root_cause) {
      var diagBody = document.getElementById("diagnosis-body");
      var noDiag = document.getElementById("no-diag-msg");
      if (noDiag) noDiag.remove();
      if (diagBody && !diagBody.querySelector("h3")) {
        var h3 = document.createElement("h3");
        h3.textContent = "Root cause";
        var p = document.createElement("p");
        p.textContent = job.root_cause;
        diagBody.insertBefore(p, diagBody.firstChild);
        diagBody.insertBefore(h3, p);
      }
    }

    // Pull request button update
    if (job.pull_request_url && !document.getElementById("pr-action-btn")) {
      var actionsWrap = document.querySelector(".job-detail-head .page-actions");
      if (actionsWrap) {
        var prLink = document.createElement("a");
        prLink.className = "btn btn-sm btn-primary";
        prLink.id = "pr-action-btn";
        prLink.href = job.pull_request_url;
        prLink.target = "_blank";
        prLink.rel = "noopener";
        prLink.innerHTML = iconMarkup("M1.5 3.25a2.25 2.25 0 1 1 3 2.122v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.25 2.25 0 0 1 1.5 3.25Zm5.677-.177L9.5 5.396l2.323-2.323a.75.75 0 0 1 1.06 1.06l-2.853 2.854a.75.75 0 0 1-1.06 0L6.117 4.134a.75.75 0 1 1 1.06-1.06Z", 16) +
          " View pull request " + (job.pull_request_number ? "#" + job.pull_request_number : "");
        actionsWrap.insertBefore(prLink, actionsWrap.lastElementChild);
      }
    }
  }

  // Hook Re-run Form on Job Detail Page (AJAX submission)
  var detailRerunForm = document.querySelector("[data-rerun-form]");
  if (detailRerunForm) {
    detailRerunForm.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var btn = document.getElementById("rerun-btn");
      if (!btn || btn.disabled) return;

      var btnText = btn.querySelector(".btn-text");
      btn.disabled = true;
      btn.classList.add("is-loading");
      if (btnText) btnText.textContent = "Queuing re-run...";

      // Clear console log
      if (consoleLogBody) {
        consoleLogBody.innerHTML = '<div class="console-empty text-muted text-small" id="console-empty">Re-run initiated. Waiting for events...</div>';
      }
      if (streamIndicator && streamText) {
        streamIndicator.classList.add("is-live");
        streamText.textContent = "Live";
      }
      updatePipelineStepper("queued", null);
      var stopForm = document.getElementById("stop-form");
      if (stopForm) {
        stopForm.style.display = "inline-block";
        var stopBtn = document.getElementById("stop-btn");
        if (stopBtn) stopBtn.disabled = false;
      }

      fetch("/api/v1/jobs/" + encodeURIComponent(currentJobId) + "/rerun", {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      })
      .then(function (res) {
        btn.classList.remove("is-loading");
        if (!res.ok) {
          throw new Error("Re-run request failed");
        }
        return res.json();
      })
      .then(function (data) {
        if (btnText) btnText.textContent = "Running...";
      })
      .catch(function (err) {
        btn.classList.remove("is-loading");
        btn.disabled = false;
        if (btnText) btnText.textContent = "Re-run job";
        alert("Failed to queue re-run: " + err.message);
      });
    });
  }

  // Hook Stop Form on Job Detail Page (AJAX submission)
  var detailStopForm = document.querySelector("[data-stop-form]");
  if (detailStopForm) {
    detailStopForm.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var btn = document.getElementById("stop-btn");
      if (!btn || btn.disabled) return;

      var btnText = btn.querySelector(".btn-text");
      btn.disabled = true;
      btn.classList.add("is-loading");
      if (btnText) btnText.textContent = "Stopping...";

      fetch("/api/v1/jobs/" + encodeURIComponent(currentJobId) + "/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      })
      .then(function (res) {
        btn.classList.remove("is-loading");
        if (!res.ok) {
          throw new Error("Stop request failed");
        }
        return res.json();
      })
      .then(function (data) {
        btn.disabled = false;
        if (btnText) btnText.textContent = "Stop job";
        if (detailStopForm) detailStopForm.style.display = "none";
      })
      .catch(function (err) {
        btn.classList.remove("is-loading");
        btn.disabled = false;
        if (btnText) btnText.textContent = "Stop job";
        alert("Failed to stop job: " + err.message);
      });
    });
  }

  // ==========================================================================
  // Jobs List Page (/jobs) & Overview Page (/overview) Updates
  // ==========================================================================
  function handleJobCreated(job) {
    // 1. Overview page update
    var interceptedStat = document.querySelector('[data-stat-value="intercepted"]');
    if (interceptedStat) {
      var currentVal = parseInt(interceptedStat.textContent.replace(/,/g, ""), 10) || 0;
      interceptedStat.textContent = (currentVal + 1).toLocaleString();
    }

    var recentList = document.getElementById("recent-jobs-list");
    if (recentList) {
      var li = document.createElement("li");
      li.className = "fade-in-highlight";
      li.setAttribute("data-recent-job-id", job.id);
      li.innerHTML = '<a class="job-row" href="/jobs/' + job.id + '">' +
        '<span data-status-icon>' + renderStatusIcon(job.status, 16) + '</span>' +
        '<div class="job-row-main">' +
          '<div class="job-row-title"><strong>' + job.repo_name + '</strong> <span class="text-muted">Run #' + job.run_id + '</span></div>' +
          '<div class="job-row-meta">Just now</div>' +
        '</div>' +
        '<span data-status-badge>' + renderStatusBadge(job.status) + '</span>' +
      '</a>';
      recentList.insertBefore(li, recentList.firstChild);
    }

    // 2. Jobs list page update
    var jobsTableBody = document.getElementById("jobs-table-body");
    if (jobsTableBody) {
      var tr = document.createElement("tr");
      tr.className = "fade-in-highlight";
      tr.setAttribute("data-job-row", job.id);
      tr.setAttribute("data-job-status", job.status);

      tr.innerHTML = '<td data-field="status">' + renderStatusBadge(job.status) + '</td>' +
        '<td><a class="table-primaryLink" href="/jobs/' + job.id + '">' + job.repo_name + '</a>' +
          '<div class="table-secondary">' + (job.workflow_name || "CI") + '</div></td>' +
        '<td><span class="branch-name">' + iconMarkup("M2 5.5a2.5 2.5 0 1 1 5 0v1a2.5 2.5 0 0 1-5 0v-1Z", 14) + ' ' + job.branch_name + '</span></td>' +
        '<td><a class="table-runLink" href="https://github.com/' + job.repo_name + '/actions/runs/' + job.run_id + '" target="_blank" rel="noopener">#' + job.run_id + '</a>' +
          '<div class="table-secondary">' + (job.commit_sha || "").substring(0, 7) + '</div></td>' +
        '<td data-field="failure"><span class="text-muted">Not extracted yet</span></td>' +
        '<td data-field="attempts"><span class="text-muted">0/3</span></td>' +
        '<td data-field="duration"><span class="text-muted">0s</span></td>' +
        '<td><span class="text-muted">just now</span></td>' +
        '<td class="text-right table-actions">' +
          '<form method="post" action="/jobs/' + job.id + '/stop" class="d-inline" data-stop-row-form>' +
            '<button type="submit" class="btn btn-sm btn-invisible text-danger p-1" title="Stop running job" data-stop-row="' + job.id + '">' +
              iconMarkup(STOP_PATH, 14) +
            '</button>' +
          '</form>' +
          '<form method="post" action="/jobs/' + job.id + '/rerun" class="d-inline" data-rerun-row-form>' +
            '<button type="submit" class="btn btn-sm btn-invisible p-1" title="Re-run remediation job" data-rerun-row="' + job.id + '" disabled>' +
              iconMarkup(SYNC_PATH, 14) +
            '</button>' +
          '</form>' +
        '</td>';

      jobsTableBody.insertBefore(tr, jobsTableBody.firstChild);

      // Increment tab counters
      var allCounter = document.querySelector('[data-tab-counter="all"]');
      if (allCounter) {
        var n = parseInt(allCounter.textContent, 10) || 0;
        allCounter.textContent = n + 1;
      }
      var activeCounter = document.querySelector('[data-tab-counter="active"]');
      if (activeCounter) {
        var nActive = parseInt(activeCounter.textContent, 10) || 0;
        activeCounter.textContent = nActive + 1;
      }
    }
  }

  function handleJobStatus(data) {
    if (!data || !data.id) return;

    // 1. If currently on this job's detail page
    if (currentJobId && currentJobId === data.id) {
      updateJobDetailUI(data);
    }

    // 2. If on overview page
    var recentItem = document.querySelector('[data-recent-job-id="' + data.id + '"]');
    if (recentItem) {
      var badgeSpan = recentItem.querySelector("[data-status-badge]");
      if (badgeSpan) badgeSpan.innerHTML = renderStatusBadge(data.status);
      var iconSpan = recentItem.querySelector("[data-status-icon]");
      if (iconSpan) iconSpan.innerHTML = renderStatusIcon(data.status, 16);
    }

    if (data.status === "pr_opened") {
      var healedStat = document.querySelector('[data-stat-value="healed"]');
      if (healedStat) {
        var count = parseInt(healedStat.textContent.replace(/,/g, ""), 10) || 0;
        healedStat.textContent = (count + 1).toLocaleString();
      }
    }

    // 3. If on jobs list page
    var row = document.querySelector('[data-job-row="' + data.id + '"]');
    if (row) {
      row.setAttribute("data-job-status", data.status);
      var statusCell = row.querySelector('[data-field="status"]');
      if (statusCell) statusCell.innerHTML = renderStatusBadge(data.status);

      if (data.failing_file) {
        var failCell = row.querySelector('[data-field="failure"]');
        if (failCell) {
          failCell.innerHTML = '<span class="table-file">' + data.failing_file + (data.failing_line ? ':' + data.failing_line : '') + '</span>';
        }
      }

      if (data.attempts_count !== undefined) {
        var attCell = row.querySelector('[data-field="attempts"]');
        if (attCell) attCell.innerHTML = '<span class="text-muted">' + data.attempts_count + '/3</span>';
      }

      if (data.duration_seconds !== undefined) {
        var durCell = row.querySelector('[data-field="duration"]');
        if (durCell) durCell.innerHTML = '<span class="text-muted">' + data.duration_seconds + 's</span>';
      }

      var rowRerunBtn = row.querySelector('[data-rerun-row="' + data.id + '"]');
      if (rowRerunBtn) {
        var isActive = ["queued", "extracting", "diagnosing", "verifying"].indexOf(data.status) !== -1;
        rowRerunBtn.disabled = isActive;
      }

      var rowStopForm = row.querySelector('[data-stop-row-form]');
      if (rowStopForm) {
        var isActive = ["queued", "extracting", "diagnosing", "verifying"].indexOf(data.status) !== -1;
        rowStopForm.style.display = isActive ? "inline-block" : "none";
        var rowStopBtn = rowStopForm.querySelector("[data-stop-row]");
        if (rowStopBtn) rowStopBtn.disabled = false;
      }
    }
  }

  // Hook Quick Re-run and Stop buttons in table rows
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (form && form.matches("[data-stop-row-form]")) {
      ev.preventDefault();
      var button = form.querySelector("[data-stop-row]");
      if (!button || button.disabled) return;
      var jobId = button.getAttribute("data-stop-row");
      button.disabled = true;
      button.classList.add("is-loading");

      fetch("/api/v1/jobs/" + encodeURIComponent(jobId) + "/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      })
      .then(function (res) {
        button.classList.remove("is-loading");
        if (!res.ok) throw new Error("Failed to stop job");
        return res.json();
      })
      .then(function () {
        button.disabled = false;
        form.style.display = "none";
        handleJobStatus({ id: jobId, status: "cancelled" });
      })
      .catch(function (err) {
        button.classList.remove("is-loading");
        button.disabled = false;
        alert("Error stopping job: " + err.message);
      });
    }

    if (form && form.matches("[data-rerun-row-form]")) {
      ev.preventDefault();
      var button = form.querySelector("[data-rerun-row]");
      if (!button || button.disabled) return;
      var jobId = button.getAttribute("data-rerun-row");
      button.disabled = true;
      button.classList.add("is-loading");

      fetch("/api/v1/jobs/" + encodeURIComponent(jobId) + "/rerun", {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      })
      .then(function (res) {
        button.classList.remove("is-loading");
        if (!res.ok) throw new Error("Failed to re-run job");
        return res.json();
      })
      .then(function () {
        handleJobStatus({ id: jobId, status: "queued", attempts_count: 0, duration_seconds: 0 });
      })
      .catch(function (err) {
        button.classList.remove("is-loading");
        button.disabled = false;
        alert("Error re-running job: " + err.message);
      });
    }
  });

  // ==========================================================================
  // Real-Time Server-Sent Events (SSE) Stream Connection
  // ==========================================================================
  function initEventSource() {
    if (!window.EventSource) return;

    var es = new EventSource("/api/v1/events");

    es.addEventListener("job_created", function (e) {
      try {
        var data = JSON.parse(e.data);
        handleJobCreated(data);
      } catch (err) {}
    });

    es.addEventListener("job_status", function (e) {
      try {
        var data = JSON.parse(e.data);
        handleJobStatus(data);
      } catch (err) {}
    });

    es.addEventListener("job_event", function (e) {
      try {
        var data = JSON.parse(e.data);
        if (currentJobId && data.job_id === currentJobId) {
          appendConsoleEvent(data);
          updatePipelineStepper(data.stage, null);
        }
      } catch (err) {}
    });

    es.onerror = function () {
      // EventSource will automatically retry connection
    };
  }

  initEventSource();

  // Active Job Detail page fallback polling (every 4 seconds) to ensure state stays in sync
  if (currentJobId) {
    var pollInterval = window.setInterval(function () {
      var head = document.querySelector(".job-detail-head");
      var status = head ? head.getAttribute("data-job-status") : "";
      var isActive = ["queued", "extracting", "diagnosing", "verifying"].indexOf(status) !== -1;
      if (!isActive) return;

      fetch("/api/v1/jobs/" + encodeURIComponent(currentJobId))
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (job) {
          if (job) {
            updateJobDetailUI(job);
            if (head) head.setAttribute("data-job-status", job.status);
            if (job.events && job.events.length) {
              job.events.forEach(function (ev) { appendConsoleEvent(ev); });
            }
          }
        })
        .catch(function () {});
    }, 4000);
  }
})();
