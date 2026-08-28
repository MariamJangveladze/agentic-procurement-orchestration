# Designer Agent Brief — Procurement Case Workspace

## 1. Design task

Design the frontend for a financial-organization internal procurement application. The first business flow is procurement **below GEL 5,000**. The application must later support the existing above-GEL-5,000 flow without a visual redesign.

The product is a case-management workspace, not a generic AI chatbot and not a Slack clone. Every procurement request becomes a durable case with a controlled workflow, documents, audit history, and a case-specific conversation.

The backend uses LangGraph and LangChain. It owns workflow state, policy routing, human-in-the-loop (HITL) pauses, and the audit trail. The frontend must make that control visible and easy to use.

## 2. Product promise

> Any employee can create and follow a procurement request without sending email chains. Every authorized participant can see what is happening, what is required from them, and why—while binding decisions remain explicit and auditable.

## 3. Design principles

1. **Case first, chat second.** The user always knows which procurement case they are in, its status, owner, deadline, and next required action.
2. **Chat coordinates; controls decide.** Chat can ask questions, explain policy, and summarize documents. Approvals, rejections, and other binding actions happen through explicit action cards and buttons.
3. **Calm, credible, enterprise-grade.** Use an operations aesthetic: compact but readable, high information clarity, restrained color, no playful AI imagery, no “magic” claims.
4. **Evidence beside decisions.** A person reviewing a case should see the relevant supplier offer, budget information, documents, and policy explanation without hunting through a chat history.
5. **The current action is obvious.** Each role sees one clear work queue and a clear action card when their attention is required.
6. **The workflow is explainable.** Show progress and decision history; do not expose raw LangGraph internals, prompts, model reasoning, or hidden policy logic.
7. **Safe by default.** Never make a free-text phrase such as “yes” in chat count as an approval. Do not make an agent recommendation look like a human decision.

## 4. Primary users and roles

| Role | Primary need | Typical action |
|---|---|---|
| Requester | Create a request; understand status; answer questions; confirm delivery | Submit request, upload evidence, reply, accept delivery |
| Head of Department | Make a quick, informed business decision | Approve, reject, request clarification |
| Logistics Specialist | Prepare supplier research; coordinate the case | Add offer, attach supplier documents, answer reviewer findings, start procurement |
| Control Reviewer | Review a complete evidence pack efficiently | Approve, reject, request information |
| Finance | Check budget and financial evidence | Confirm budget status, request correction, approve/reject when policy requires |
| Legal | Review contractual/legal materials | Approve, reject, request documents, review agreement draft |
| Administrator / Auditor | Inspect cases and trace accountability | Search, filter, inspect immutable timeline |

## 5. Below-GEL-5,000 product scope

The exact approval policy for below GEL 5,000 will be configured by the backend. Do **not** hard-code reviewer routing in the user interface.

The UI must support these generic workflow states:

- Draft
- Submitted
- Waiting for requester information
- Waiting for manager approval
- In Logistics preparation
- Waiting for one or more control reviews
- Returned for rework
- Approved / procurement in progress
- Awaiting delivery
- Awaiting requester acceptance
- Closed
- Rejected / cancelled

The server supplies the current state, visible progress steps, eligible actions, required role, evidence requirements, and action labels. The frontend renders them.

## 6. Information architecture

```text
App shell
├── Home / My work
│   ├── My action queue
│   ├── My requests
│   └── Recently updated cases
├── New procurement request
├── Case workspace
│   ├── Case conversation
│   ├── Workflow status and current action
│   ├── Documents and evidence
│   ├── Decision timeline
│   └── Case details
├── All cases (authorized users only)
└── Profile / notification preferences
```

## 7. Required screens

### A. Home — “My work”

**Purpose:** Help a user immediately understand what needs attention.

Include:

- Top navigation with logo/product name, global search, notifications, and user menu.
- Page title: `My work`.
- A prominent `New procurement request` primary button.
- `Requires your action` queue at top. Each row shows case ID, request title, department, amount, age/SLA, current action, and one primary action button.
- `My requests` list for requesters with status chips and progress.
- Filters: status, category, amount band, department, updated date, assigned role.
- Empty state for users with no outstanding work.

### B. New procurement request

**Purpose:** Make the first request feel simpler than writing an email.

Use a focused, guided form with an optional assistant panel. Do not begin with a blank chat screen.

Required fields:

- Material or service
- Subcategory
- Description / business need
- Deadline
- Estimated amount in GEL
- Department (pre-filled from identity where possible)
- Optional attachment upload

Add a small assistant prompt below the description:

> “Describe what you need in your own words. I will identify missing information before you submit.”

On submit, show a confirmation page with case ID, initial status, expected next owner, and a direct link to the case workspace.

### C. Case workspace — primary screen

This is the most important screen. Design desktop-first at 1440 px, responsive down to tablet; mobile can prioritize status and current action.

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ Breadcrumb / Cases / PR-2026-00124      Search      Notifications   Profile │
├─────────────────────────────────────────────────────────────────────────────┤
│ PR-2026-00124  •  Laptops for service desk  •  4,800 GEL  [In review]       │
│ Operations  •  Requested by Nino Beridze  •  Due 15 Aug 2026                 │
├───────────────────────────────┬───────────────────────┬─────────────────────┤
│ Case conversation             │ Workflow              │ Evidence             │
│                               │ • Submitted           │ Supplier offer (PDF) │
│ AI and participant messages   │ • Manager approved    │ Budget snapshot      │
│ questions, answers, updates   │ • Logistics prepared  │ Supplier documents   │
│                               │ • Finance pending     │ Agreement draft      │
│ [write message...]            │                       │ [+ Upload]           │
│                               │ Current action card   │                     │
│                               │ [Approve] [Reject]    │                     │
│                               │ [Request information] │                     │
├───────────────────────────────┴───────────────────────┴─────────────────────┤
│ Tabs: Overview | Decisions & timeline | Documents | Activity                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### Layout behaviour

- **Header:** case ID, concise title, amount, status, requester/department, deadline, overflow menu.
- **Left, 50–55%:** conversation. This is the working area for questions, answers, agent summaries, and system updates.
- **Middle, 25–30%:** workflow progress and the current action card. This column must stay visible on desktop while reading conversation where practical.
- **Right, 20–25%:** evidence/document panel. Make document count and missing items visible.
- On narrower widths, collapse evidence into a right drawer and workflow into a persistent top summary.

### D. Action / HITL cards

Every binding human decision is represented as an unmistakable card in the workflow column and can also appear inline in the conversation.

Each card must show:

- Who must act and by when
- Exact action being requested
- Decision context and evidence links
- Clear allowed actions
- Comment field, required for rejection and information request
- Confirmation state after submission
- Immutable actor, timestamp, and comment after completion

Examples:

```text
Manager approval required
Approve the business need for laptops for the service desk.

[Open request details]  [Approve]  [Reject]  [Request clarification]
```

```text
Finance review required
Budget availability: 7,200 GEL. Proposed offer: 4,800 GEL.

[View budget evidence]  [Approve]  [Reject]  [Request information]
```

Use semantic colors consistently:

- Blue/indigo: action required or active work
- Green: approved or completed
- Amber: waiting, warning, or information requested
- Red: rejected, expired, or blocked
- Neutral gray: inactive/upcoming workflow steps

Never rely on color alone; pair every state with a label and icon.

### E. Conversation model

The conversation is tied to one case, not a global assistant.

Message types:

- Human message: requester, Logistics, manager, reviewer
- System event: routed, approved, rejected, document uploaded, deadline changed
- Agent assistance: concise summary, missing-information question, supplier-evidence summary, agreement-draft notice
- HITL action card: binding decision only

Rules:

- Clearly label agent messages as `Procurement Assistant` with a distinct but restrained visual style.
- Agent messages must show source/evidence links when making factual claims.
- Human messages show role, name, timestamp, and optional attachment.
- System events are compact timeline entries, not chat bubbles.
- Add suggested prompts such as `What is missing?`, `Show current status`, and `Summarize reviewer findings`.
- Do not show chain-of-thought, raw retrieval chunks, hidden prompts, or model confidence percentages.

### F. Documents and evidence

Documents are first-class objects, not attachments lost in chat history.

For every item show:

- Type: supplier offer, supplier profile, budget evidence, agreement draft, acceptance act, other
- Status: received, missing, superseded, pending review, approved
- Uploader and upload time
- Version number
- Preview/open action
- Download action only if policy permits

Add a `Missing evidence` section at the top when policy requirements are incomplete.

### G. Decision timeline

Provide an audit-friendly timeline accessible from the case workspace:

- Event name
- Actor and role
- Timestamp
- Previous and new case status where relevant
- Decision comment
- Linked evidence
- “Agent prepared” marker where an agent produced a summary/draft

Do not expose technical trace IDs in the normal user view. An authorized technical/auditor view may include a link to LangSmith or a workflow trace.

## 8. Critical states to design

Create explicit designs for each of these states:

1. New request draft with missing required information.
2. Submitted request waiting for manager.
3. Manager approval card, with rejection comment required.
4. Logistics preparing supplier research and uploading an offer.
5. Multiple simultaneous reviews, with four reviewer states visible.
6. Reviewer requests additional information; case conversation routes a question to the correct owner.
7. Case returned to Logistics for rework, with findings grouped and actionable.
8. Agent-generated agreement draft awaiting human Legal/Logistics review.
9. Delivery awaiting requester acceptance.
10. Closed case with a complete immutable timeline.
11. Out-of-budget or rejected case with a clear explanation and next allowed action.
12. Empty states, loading states, permission-denied state, and temporary system-error state.

## 9. Interaction requirements

- Approval/rejection must require an explicit click, then show a confirmation dialog for high-impact actions.
- Rejection and request-information actions require a comment; explain who will receive it.
- After a decision, update the timeline immediately and show the next workflow owner/state.
- Permit document drag-and-drop and show upload progress, validation errors, and version conflicts.
- Support deep links to a case and to a specific action card.
- The current action queue must update without users needing to scan every case.
- Use optimistic UI only for non-binding chat drafts; wait for server confirmation before displaying an approval as complete.
- Preserve drafts if a user navigates away from an unsent chat message or rejection comment.

## 10. Visual direction

- **Tone:** trustworthy, precise, calm, operational.
- **Color:** neutral background, white surfaces, one dark blue/indigo primary color, semantic status colors as described above.
- **Typography:** modern sans-serif; clear hierarchy; avoid oversized marketing-style type.
- **Density:** medium density—more compact than a consumer chat app, less dense than a legacy enterprise table.
- **Components:** cards, status chips, stepper/timeline, compact tables, attachment rows, drawers, confirmation dialogs, avatar/role labels.
- **AI treatment:** quiet and transparent. Use a small assistant icon/badge, never a dominant robot illustration.
- **Language:** support English and Georgian. Plan for longer Georgian labels and text expansion; do not use fixed-width text buttons.

## 11. Accessibility and compliance expectations

- Meet WCAG 2.2 AA contrast and keyboard-navigation expectations.
- All status meaning must be available in text, not color alone.
- Approval buttons must have clear, unique accessible labels such as `Approve finance review for PR-2026-00124`.
- Make focus state highly visible.
- Avoid automatic approval or destructive actions from keyboard shortcuts.
- Never display sensitive supplier or personal data in a notification preview unless the user is authorized.
- Assume SSO and role-based permissions are provided by the backend; design sensible denied/no-access states.

## 12. Data contract assumptions for frontend implementation

The UI expects the backend to provide:

```text
Case:
  id, title, amount_gel, category, subcategory, status,
  requester, department, deadline, current_owner, available_actions,
  workflow_steps[], documents[], messages[], audit_events[]

Action:
  id, type, title, required_role, due_at, evidence_ids[],
  allowed_decisions[], comment_required, confirmation_required

Message:
  id, author_type (human | agent | system), author_name, author_role,
  content, timestamp, evidence_ids[], related_action_id

Document:
  id, name, type, status, version, uploaded_by, uploaded_at,
  preview_url, download_url, required_by_policy
```

Do not invent workflow status or permissions on the client. The frontend renders `available_actions` returned by the backend.

## 13. Out of scope for the first design

- Slack or Teams integration
- Supplier portal
- Payment execution
- Full administrator policy editor
- Native mobile application
- Autonomous procurement decisions
- Public-facing vendor access

## 14. Required design deliverables

Produce:

1. A lightweight design system: colors, typography, status chips, buttons, input fields, cards, tables, document rows, timeline, chat messages, and action cards.
2. Desktop designs for Home/My Work, New Request, and Case Workspace.
3. Tablet-responsive layout for Case Workspace.
4. The 12 critical states listed above.
5. Clickable prototype for this primary journey:

```text
Requester creates request → manager approves → Logistics adds supplier offer →
reviewer requests information → Logistics responds → reviewer approves →
case reaches delivery/acceptance.
```

6. Developer handoff notes covering spacing, responsive behavior, component states, empty/error/loading states, and interaction rules.

## 15. Success criteria

The design is successful if a first-time employee can:

- create a request without asking where to send it;
- find their next required action in under 10 seconds;
- understand why a case is blocked;
- find the document/evidence supporting a decision;
- distinguish an AI suggestion from a binding human decision;
- complete an approval/rejection confidently without using email or Slack;
- review the case history later and understand who did what, when, and why.
