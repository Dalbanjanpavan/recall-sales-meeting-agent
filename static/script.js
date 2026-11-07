/**
 * Recall — Sales Meeting Intelligence Agent Frontend Logic
 * Implements:
 * - Prospect preparation with multi-stage reasoning
 * - Customer decision accuracy & document lifecycle tracking
 * - Requester vs Task-owner separation
 * - Human approval / rejection execution
 * - Persistent task status updates & Hindsight sync with retry support
 */

// DOM Elements
const meetingForm = document.getElementById("meetingForm");
const submitBtn = document.getElementById("submitBtn");

const emptyState = document.getElementById("emptyState");
const loadingState = document.getElementById("loadingState");
const errorState = document.getElementById("errorState");
const briefResult = document.getElementById("briefResult");
const statusBadge = document.getElementById("statusBadge");
const reasoningModeTag = document.getElementById("reasoningModeTag");
const memoryStatusText = document.getElementById("memoryStatusText");

// Prospect Card Elements
const cardProspectName = document.getElementById("cardProspectName");
const cardCompany = document.getElementById("cardCompany");
const cardStage = document.getElementById("cardStage");
const memoryCount = document.getElementById("memoryCount");
const memoryStatsBadge = document.getElementById("memoryStatsBadge");

// Conflict Alert
const conflictsBox = document.getElementById("conflictsBox");
const conflictText = document.getElementById("conflictText");

// Next Best Action
const nbaAction = document.getElementById("nbaAction");
const nbaWhy = document.getElementById("nbaWhy");
const nbaEvidence = document.getElementById("nbaEvidence");
const nbaTiming = document.getElementById("nbaTiming");
const nbaStatus = document.getElementById("nbaStatus");
const nbaResponsible = document.getElementById("nbaResponsible");
const nbaConfidence = document.getElementById("nbaConfidence");

// Proposed Task Tool Card
const proposalCard = document.getElementById("proposalCard");
const proposalIdDisplay = document.getElementById("proposalIdDisplay");
const proposalDescription = document.getElementById("proposalDescription");
const proposalReason = document.getElementById("proposalReason");
const proposalEvidence = document.getElementById("proposalEvidence");
const proposalRequester = document.getElementById("proposalRequester");
const proposalOwner = document.getElementById("proposalOwner");
const proposalActions = document.getElementById("proposalActions");
const proposalFeedback = document.getElementById("proposalFeedback");

// Tasks Dashboard
const taskList = document.getElementById("taskList");
const taskCountBadge = document.getElementById("taskCountBadge");
const sidebarTaskBadge = document.getElementById("sidebarTaskBadge");

// Customer State Lists
const stateReqs = document.getElementById("stateReqs");
const stateObjections = document.getElementById("stateObjections");
const statePromises = document.getElementById("statePromises");
const stateUnresolved = document.getElementById("stateUnresolved");
const decisionStagesList = document.getElementById("decisionStagesList");
const documentLifecycleList = document.getElementById("documentLifecycleList");

// Briefing & Memories
const briefText = document.getElementById("briefText");
const memoryList = document.getElementById("memoryList");

// Current active proposal state
let currentProposal = null;
let currentProspectId = null;

// Preset scenarios for hackathon demonstration
const SCENARIOS = {
    1: {
        name: "Rahul Sharma",
        company: "Acme Technologies",
        goal: "Address pricing objection and agree on custom enterprise terms",
        notes: "Rahul mentioned their CFO reviewed our initial quote and expressed pricing concerns. They asked if we could provide a revised enterprise tier with custom SLA by Friday. The sales team agreed to evaluate customized pricing."
    },
    2: {
        name: "Vikram Patel",
        company: "Globex Corp",
        goal: "Review cloud migration roadmap and verify proposal delivery",
        notes: "Product demonstration was completed successfully last week. Vikram requested a customized migration proposal. The sales team promised to deliver it, but there is no confirmed record yet of Vikram receiving it."
    },
    3: {
        name: "Sarah Connor",
        company: "Cyberdyne Systems",
        goal: "Finalize master service agreement and contract signing timeline",
        notes: "Sarah confirmed internal security audit has passed. Revised proposal was formally approved by finance committee yesterday. Sarah expressed strong purchase intent, but contract remains unsigned."
    }
};

function fillScenario(id) {
    const sc = SCENARIOS[id];
    if (!sc) return;
    document.getElementById("name").value = sc.name;
    document.getElementById("company").value = sc.company;
    document.getElementById("goal").value = sc.goal;
    document.getElementById("notes").value = sc.notes;
}

// ----------------- Main Meeting Preparation Handler -----------------

meetingForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const meetingData = {
        name: document.getElementById("name").value.trim(),
        company: document.getElementById("company").value.trim(),
        goal: document.getElementById("goal").value.trim(),
        notes: document.getElementById("notes").value.trim()
    };

    emptyState.classList.add("hidden");
    briefResult.classList.add("hidden");
    errorState.classList.add("hidden");
    loadingState.classList.remove("hidden");

    submitBtn.disabled = true;
    submitBtn.textContent = "Agent reasoning in progress...";
    statusBadge.textContent = "ANALYZING";

    try {
        const response = await fetch("/api/prepare", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(meetingData)
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Agent processing failed.");
        }

        renderAgentResults(data);

    } catch (error) {
        errorState.textContent = error.message;
        errorState.classList.remove("hidden");
        statusBadge.textContent = "ERROR";

    } finally {
        loadingState.classList.add("hidden");
        submitBtn.disabled = false;
        submitBtn.innerHTML = 'Analyze & Reason With Agent <span>→</span>';
    }
});

function renderAgentResults(data) {
    statusBadge.textContent = "AGENT ACTIVE";
    currentProspectId = data.prospect_id;

    if (reasoningModeTag) {
        reasoningModeTag.textContent = data.ai_reasoning_mode === "groq_llm" ? "GROQ LLM" : "FALLBACK RULES";
    }
    if (memoryStatusText) {
        const hs = data.hindsight_status || {};
        memoryStatusText.textContent = hs.recall === "succeeded"
            ? `Hindsight recall succeeded · retain: ${hs.retain || "not attempted"}`
            : data.hindsight_available
                ? `Hindsight configured · recall: ${hs.recall || "not attempted"}`
                : "Hindsight unavailable — using local context only";
    }

    // 1. Prospect Header & Memory Stats
    cardProspectName.textContent = data.prospect_name;
    cardCompany.textContent = data.company;
    cardStage.textContent = (data.customer_state && data.customer_state.current_stage) || "Discovery";
    
    const uniqueCount = data.memory_count || 0;
    const rawCount = data.raw_memory_count || uniqueCount;
    memoryCount.textContent = `${uniqueCount} unique`;
    if (memoryStatsBadge) {
        memoryStatsBadge.textContent = `${uniqueCount} unique memories (${rawCount} raw recalled)`;
    }

    // 2. Conflicts (if any)
    if (data.conflicts && data.conflicts.length > 0) {
        conflictsBox.classList.remove("hidden");
        conflictText.innerHTML = data.conflicts.map(c => `<div>• ${escapeHtml(c.description || JSON.stringify(c))}</div>`).join("");
    } else {
        conflictsBox.classList.add("hidden");
    }

    // 3. Next Best Action
    const nba = data.next_best_action || {};
    nbaAction.textContent = nba.recommended_action || "Continue discovery with prospect.";
    nbaWhy.textContent = nba.why_this_action || "Maintain deal momentum.";
    nbaEvidence.textContent = nba.supporting_evidence || "Meeting notes";

    // Strict date integrity: Never display invalid 2026-11-10 or unconfirmed date
    let timingVal = nba.timing || nba.relevant_dates || "Next conversation";
    if (timingVal.includes("2026-11-10") || (data.proposed_task && data.proposed_task.has_deadline_conflict)) {
        timingVal = "Deadline not confirmed";
    }
    nbaTiming.textContent = timingVal;
    nbaStatus.textContent = nba.current_status || "Status not confirmed";
    nbaResponsible.textContent = nba.responsible_person || "Sales Team";
    nbaConfidence.textContent = `Confidence: ${nba.confidence || "High"}`;

    // 4. Proposed Task Tool Card (Human-in-the-loop Gate)
    if (data.proposed_task) {
        currentProposal = data.proposed_task;
        proposalIdDisplay.textContent = currentProposal.proposal_id;
        proposalDescription.textContent = currentProposal.description;
        proposalReason.textContent = currentProposal.reason;
        proposalEvidence.textContent = currentProposal.supporting_evidence;
        proposalRequester.textContent = currentProposal.requester || data.prospect_name;
        proposalOwner.textContent = currentProposal.responsible_person || "Sales Team";

        const propDeadline = document.getElementById("proposalDeadline");
        if (propDeadline) {
            if (currentProposal.confirmed_due_date) {
                propDeadline.textContent = currentProposal.confirmed_due_date;
            } else if (currentProposal.has_deadline_conflict) {
                propDeadline.textContent = "Deadline not confirmed";
            } else if (currentProposal.due_date && !currentProposal.due_date.includes("2026-11-10")) {
                propDeadline.textContent = currentProposal.due_date;
            } else {
                propDeadline.textContent = "Deadline not confirmed";
            }
        }

        // Deadline conflict warning & human confirmation
        const conflictBox = document.getElementById("proposalConflictWarning");
        const conflictDesc = document.getElementById("proposalConflictDesc");
        const dateInput = document.getElementById("confirmedDueDateInput");
        const quickSelect = document.getElementById("deadlineQuickSelect");

        if (conflictBox && currentProposal.has_deadline_conflict) {
            conflictDesc.textContent = currentProposal.deadline_conflict_details || "Conflicting or ambiguous deadlines detected. Human confirmation required.";
            conflictBox.classList.remove("hidden");
            if (dateInput) dateInput.value = "";
            if (quickSelect) quickSelect.value = "";
        } else if (conflictBox) {
            conflictBox.classList.add("hidden");
        }

        proposalFeedback.className = "proposal-feedback hidden";
        proposalFeedback.textContent = "";
        proposalActions.classList.remove("hidden");
        proposalCard.classList.remove("hidden");
    } else {
        proposalCard.classList.add("hidden");
        currentProposal = null;
    }

    // 5. Decision Stages & Document Lifecycle Lists
    renderDecisionStages(data.customer_state?.decision_stages);
    renderDocumentLifecycle(data.customer_state?.document_lifecycle);

    // 6. Customer State Cards
    renderStateList(stateReqs, data.customer_state?.requirements);
    renderStateList(stateObjections, data.customer_state?.objections);
    renderStateList(statePromises, data.customer_state?.team_promises);
    renderStateList(stateUnresolved, data.customer_state?.unresolved_actions);

    // 7. Briefing Text
    briefText.textContent = data.brief || "No briefing text generated.";

    // 8. Retrieved Memories (Deduplicated)
    memoryList.replaceChildren();
    if (data.memories && data.memories.length > 0) {
        data.memories.forEach((mem) => {
            const item = document.createElement("div");
            item.className = "memory-item";
            item.textContent = mem;
            memoryList.appendChild(item);
        });
    } else {
        const item = document.createElement("div");
        item.className = "memory-item";
        item.textContent = "No previous memories found for this prospect in Hindsight.";
        memoryList.appendChild(item);
    }

    // 9. Render Existing Tasks
    renderTaskList(data.tasks || []);

    briefResult.classList.remove("hidden");
}

function renderDecisionStages(stages) {
    if (!decisionStagesList) return;
    decisionStagesList.replaceChildren();

    if (!stages || stages.length === 0) {
        decisionStagesList.innerHTML = `<div class="no-data-hint">No formal decision milestone recorded.</div>`;
        return;
    }

    stages.forEach(st => {
        const item = document.createElement("div");
        item.className = "decision-item";
        item.innerHTML = `
            <div class="decision-header">
                <span class="decision-type-pill">${escapeHtml(st.type)}</span>
                <span class="decision-status-pill">${escapeHtml(st.status || "Confirmed")}</span>
            </div>
            ${st.conditions && st.conditions !== 'None' ? `<div class="decision-cond"><strong>Conditions:</strong> ${escapeHtml(st.conditions)}</div>` : ''}
            <div class="decision-meta"><strong>Source:</strong> ${escapeHtml(st.source || "Interaction notes")}</div>
        `;
        decisionStagesList.appendChild(item);
    });
}

function renderDocumentLifecycle(docs) {
    if (!documentLifecycleList) return;
    documentLifecycleList.replaceChildren();

    if (!docs || docs.length === 0) {
        documentLifecycleList.innerHTML = `<div class="no-data-hint">No document activity recorded.</div>`;
        return;
    }

    docs.forEach(doc => {
        const item = document.createElement("div");
        item.className = "doc-lifecycle-item";
        item.innerHTML = `
            <div class="doc-header">
                <span class="doc-title">${escapeHtml(doc.document)}</span>
                <span class="doc-state-badge state-${(doc.state || "requested").toLowerCase()}">${escapeHtml(doc.state)}</span>
            </div>
            <div class="doc-meta">
                <span><strong>Owner:</strong> ${escapeHtml(doc.responsible || "Sales Team")}</span> | 
                <span><strong>Source:</strong> ${escapeHtml(doc.source || "Notes")}</span>
            </div>
        `;
        documentLifecycleList.appendChild(item);
    });
}

function renderStateList(element, items) {
    element.replaceChildren();
    if (!items || items.length === 0) {
        const li = document.createElement("li");
        li.textContent = "None recorded";
        element.appendChild(li);
        return;
    }
    items.forEach(text => {
        const li = document.createElement("li");
        li.textContent = text;
        element.appendChild(li);
    });
}

// ----------------- Human Approval / Rejection Handlers -----------------

function applyDeadlineSelect() {
    const sel = document.getElementById("deadlineQuickSelect");
    const inp = document.getElementById("confirmedDueDateInput");
    if (sel && inp && sel.value) {
        inp.value = sel.value;
    }
}

async function approveCurrentProposal() {
    if (!currentProposal) return;

    let confirmedDueDate = null;
    const dateInput = document.getElementById("confirmedDueDateInput");
    if (dateInput && dateInput.value.trim()) {
        confirmedDueDate = dateInput.value.trim();
    }

    if (currentProposal.has_deadline_conflict && !confirmedDueDate) {
        alert("This proposed action has an unresolved deadline conflict.\n\nPlease select or enter a confirmed due date before approving.");
        if (dateInput) dateInput.focus();
        return;
    }

    const approveBtn = document.getElementById("approveBtn");
    approveBtn.disabled = true;
    approveBtn.textContent = "Executing...";

    try {
        const payload = { proposal_id: currentProposal.proposal_id };
        if (confirmedDueDate) {
            payload.confirmed_due_date = confirmedDueDate;
        }

        const response = await fetch("/api/tasks/approve", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || "Approval execution failed.");
        }

        // Show success feedback
        proposalFeedback.className = "proposal-feedback feedback-success";
        proposalFeedback.innerHTML = `✓ <strong>Approved & Executed:</strong> Task <code>${escapeHtml(data.task.task_id)}</code> persisted to SQLite database.`;
        proposalFeedback.classList.remove("hidden");
        proposalActions.classList.add("hidden");

        // Refresh task list
        await refreshTasks(currentProspectId);

    } catch (err) {
        alert("Error approving task: " + err.message);
        approveBtn.disabled = false;
        approveBtn.textContent = "✓ Approve & Create Task";
    }
}

async function rejectCurrentProposal() {
    if (!currentProposal) return;

    const rejectBtn = document.getElementById("rejectBtn");
    rejectBtn.disabled = true;

    try {
        const response = await fetch("/api/tasks/reject", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                proposal_id: currentProposal.proposal_id,
                reason: "Salesperson rejected this action."
            })
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || "Rejection failed.");
        }

        // Show rejection feedback
        proposalFeedback.className = "proposal-feedback feedback-rejected";
        proposalFeedback.innerHTML = `✕ <strong>Action Rejected:</strong> Decision recorded in audit log. No task was created.`;
        proposalFeedback.classList.remove("hidden");
        proposalActions.classList.add("hidden");

    } catch (err) {
        alert("Error rejecting task: " + err.message);
        rejectBtn.disabled = false;
    }
}

// ----------------- Task Outcome Recording & Learning -----------------

async function updateTaskOutcome(taskId) {
    const statusSelect = document.getElementById(`status-${taskId}`);
    const outcomeInput = document.getElementById(`outcome-${taskId}`);
    const updateBtn = document.getElementById(`btn-${taskId}`);
    const statusMsg = document.getElementById(`msg-${taskId}`);

    if (!statusSelect || !outcomeInput || !updateBtn) return;

    const newStatus = statusSelect.value;
    const outcomeNote = outcomeInput.value.trim();

    updateBtn.disabled = true;
    updateBtn.textContent = "Saving...";

    try {
        const response = await fetch(`/api/tasks/${taskId}/outcome`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                status: newStatus,
                outcome_note: outcomeNote
            })
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || "Failed to update outcome.");
        }

        if (statusMsg) {
            if (data.hindsight_synced) {
                statusMsg.innerHTML = `<span style="color:var(--green)">✓ Saved & Synced to Hindsight!</span>`;
            } else if (data.hindsight_sync_status === "failed") {
                statusMsg.innerHTML = `<span style="color:var(--red)">⚠️ Saved locally; Hindsight sync failed.</span> <button type="button" class="btn-retry-sync" onclick="retrySync('${escapeHtml(taskId)}')">Retry Sync</button>`;
            } else {
                statusMsg.innerHTML = `✓ Status updated locally.`;
            }
            statusMsg.style.display = "inline-block";
        }

        // Refresh tasks
        await refreshTasks(currentProspectId);

    } catch (err) {
        alert("Error recording outcome: " + err.message);
    } finally {
        updateBtn.disabled = false;
        updateBtn.textContent = "Record Outcome";
    }
}

async function retrySync(taskId) {
    try {
        const res = await fetch(`/api/tasks/${taskId}/retry-sync`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || "Retry failed.");
        }
        alert("Hindsight memory synchronized successfully!");
        await refreshTasks(currentProspectId);
    } catch (e) {
        alert("Retry failed: " + e.message);
    }
}

async function refreshTasks(prospectId) {
    try {
        const url = prospectId ? `/api/tasks?prospect_id=${encodeURIComponent(prospectId)}` : "/api/tasks";
        const res = await fetch(url);
        const data = await res.json();
        if (res.ok && data.tasks) {
            renderTaskList(data.tasks);
        }
    } catch (e) {
        console.warn("Failed to refresh tasks:", e);
    }
}

function renderTaskList(tasks) {
    taskCountBadge.textContent = `${tasks.length} Tasks`;
    sidebarTaskBadge.textContent = `${tasks.length}`;

    if (!tasks || tasks.length === 0) {
        taskList.innerHTML = `<div class="no-tasks-hint">No approved tasks yet. Approve a proposal above to execute task creation.</div>`;
        return;
    }

    taskList.replaceChildren();

    tasks.forEach(t => {
        const card = document.createElement("div");
        card.className = "task-card";

        const statusClass = `status-${(t.task_status || "pending").toLowerCase().replace(" ", "-")}`;

        let syncBadgeHtml = "";
        if (t.hindsight_sync_status === "synced") {
            syncBadgeHtml = `<span class="sync-pill sync-ok">✓ Hindsight Synced</span>`;
        } else if (t.hindsight_sync_status === "failed") {
            syncBadgeHtml = `<span class="sync-pill sync-fail">⚠️ Sync Failed <button type="button" class="btn-retry-pill" onclick="retrySync('${escapeHtml(t.task_id)}')">Retry</button></span>`;
        }

        let deadlineHtml = "";
        if (t.confirmed_due_date) {
            deadlineHtml = `<span><strong>Confirmed Deadline:</strong> <span class="badge-confirmed-date">${escapeHtml(t.confirmed_due_date)}</span></span> | `;
        } else {
            let note = "";
            if (t.deadline_warning) {
                note = ` <span class="deadline-hint">⚠ ${escapeHtml(t.deadline_warning)}</span>`;
            } else if (t.due_date && t.due_date.includes("2026-11-10")) {
                note = ` <span class="deadline-hint">⚠ Historical invalid deadline: ${escapeHtml(t.due_date)} (Tuesday, not Friday)</span>`;
            } else if (t.natural_language_deadline || t.due_date) {
                note = ` <span class="deadline-hint">(Unconfirmed: ${escapeHtml(t.natural_language_deadline || t.due_date)})</span>`;
            }
            deadlineHtml = `<span><strong>Deadline:</strong> <span class="badge-unconfirmed-date">Deadline not confirmed</span>${note}</span> | `;
        }

        let commitmentHtml = "";
        if (t.commitment_maker) {
            commitmentHtml = `<span><strong>Committed By:</strong> ${escapeHtml(t.commitment_maker)}</span> | `;
        }

        const isTerminal = t.task_status === "Completed" || t.task_status === "Cancelled";
        const outcomeControls = isTerminal
            ? `<div class="task-outcome-box"><div class="outcome-box-title"><span>RECORDED OUTCOME</span></div><p>${escapeHtml(t.outcome_note || "No outcome note recorded.")}</p></div>`
            : `<div class="task-outcome-box">
                <div class="outcome-box-title">
                    <span>RECORD CONFIRMED OUTCOME (UPDATES MEMORY)</span>
                    <span id="msg-${escapeHtml(t.task_id)}" class="hindsight-tag-status"></span>
                </div>
                <div class="outcome-controls">
                    <select id="status-${escapeHtml(t.task_id)}" class="status-select">
                        <option value="Pending" ${t.task_status === 'Pending' ? 'selected' : ''}>Pending</option>
                        <option value="In Progress" ${t.task_status === 'In Progress' ? 'selected' : ''}>In Progress</option>
                        <option value="Completed">Completed</option>
                        <option value="Cancelled">Cancelled</option>
                    </select>
                    <input type="text" id="outcome-${escapeHtml(t.task_id)}" class="outcome-input" placeholder="Enter confirmed outcome note..." value="${escapeHtml(t.outcome_note || '')}" />
                    <button type="button" id="btn-${escapeHtml(t.task_id)}" class="btn-update-outcome" onclick="updateTaskOutcome('${escapeHtml(t.task_id)}')">Record Outcome</button>
                </div>
            </div>`;

        card.innerHTML = `
            <div class="task-header">
                <div class="task-id-group">
                    <span class="task-id-badge">${escapeHtml(t.task_id)}</span>
                    ${syncBadgeHtml}
                </div>
                <span class="task-status-badge ${statusClass}">${escapeHtml(t.task_status)}</span>
            </div>
            <div class="task-desc">${escapeHtml(t.description)}</div>
            <div class="task-meta-line">
                <span><strong>Requester:</strong> ${escapeHtml(t.requester || t.prospect_name || "Customer")}</span> | 
                ${commitmentHtml}
                <span><strong>Responsible:</strong> ${escapeHtml(t.responsible_person || "Sales Team")}</span> | 
                ${deadlineHtml}
                <span><strong>Created:</strong> ${escapeHtml((t.created_at || "").substring(0, 10))}</span>
            </div>

            ${outcomeControls}
        `;
        taskList.appendChild(card);
    });
}

function toggleBriefing() {
    briefText.classList.toggle("hidden");
}

function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Load initial tasks on start
document.addEventListener("DOMContentLoaded", () => {
    refreshTasks(null);
});
