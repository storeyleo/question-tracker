# Question Tracker

## Purpose

Question Tracker is a local desktop application for organising university
problem sheets, solutions and past papers and tracking performance on
individual questions.

The initial target platform is Windows, but the architecture should avoid
unnecessary Windows-specific dependencies so that macOS and Linux versions
remain practical in the future.

---

## Technology

- Language: Python
- Desktop framework: PySide6 / Qt 6
- Database: SQLite
- Initial platform: Windows
- Future platforms: macOS and Linux
- Application is local-only with no accounts or authentication.

Platform-independent APIs and pathlib should be preferred over
Windows-specific implementations where practical.

---

## Managed File Library

Question Tracker maintains its own file library.

Example:

Question Tracker/
└── files/
    └── Year 2/
        └── Linear Algebra/
            ├── Problem Sheets/
            ├── Problem Sheet Solutions/
            └── Past Papers/

Year and module names are user-created folder names rather than separate
metadata-heavy entities.

Users may create folders and copy PDFs into the library manually.

The application should detect newly created folders and PDFs.

Newly detected PDFs may remain in a "Needs setup" state until configured.

### File types

The three primary imported document types are:

- Problem Sheet
- Solution
- Past Paper

A problem sheet or past paper normally has one main PDF.

A question set may have zero, one or multiple associated solution/reference
PDFs.

Solution association is manual initially. Filename-based suggestions may be
added later.

If a managed PDF is deleted outside the application, its tracking data should
remain and the document should be displayed as missing so that the PDF can be
replaced.

Replacing a PDF must preserve questions, attempts, notes, bookmarks and other
tracking data.

Paths stored in the database should be relative to the managed library root
where practical.

---

## Question Sets

Problem sheets and past papers use the same underlying question-set model.

Each question set may contain:

- Main PDF
- Associated solution/reference PDFs
- Display name independent of filename
- Questions
- Topic headings
- General notes
- Completion/mastery information

---

## Question Structure

Questions support up to three levels.

Examples:

1
1.a
1.a)i

Only leaf questions are markable.

A question that has children acts only as a parent/container.

Question identity must be independent of its visible number/label so that
reordering does not lose tracking history.

Question navigation follows visible left-to-right order.

Example:

1.a → 1.b → 1.c → 2.a → 2.b → 3 → 4.a)i

### Creating question structures

A PDF may be imported without immediately creating its question structure.

The initial implementation may use a simple manual question editor.

The intended future interface is similar to Microsoft Word's table-size
picker:

- Drag horizontally to create main questions.
- Drag downward from a question to create subquestions.
- Drag downward again to create third-level subquestions.

Question structures can be edited later from the Question Tracker screen.

Questions should support drag-and-drop reordering.

If a marked parent question is later split into children, its existing history
moves to the new leftmost leaf by default.

Deleting a question containing tracking data must warn the user and provide
options to:

- Cancel
- Permanently delete the data
- Move the history to another question

---

## Question Labels (stored as topics)

Each leaf question may have zero or one flat classification label. The reusable
topic entities are these labels, not question numbers or structural hierarchy
headings. Existing topic IDs, per-question-set scope, sort order and colours
remain. A unique question_topics(question_id) index enforces zero-or-one
assignment without replacing the existing tables.

Examples:

- Bases
- Linear Independence
- Eigenvalues

Headings are manually created and assigned.

Labels have a title and their existing colour. Assigning a new label replaces
the old assignment atomically; clearing or deleting a label leaves the question
unlabelled. Labels are reusable across many leaves and are not inherited from
parents. Selecting parents in the editor still applies the choice to their
descendant leaves. Structural root/second-level headings remain separate.

In the tracker UI, headings should visually span the questions assigned to
them.

Topic headings should be reorderable.

Migration 006 retains the assigned topic with lowest sort_order, then stable ID,
when old data has multiple assignments. Other assignments are removed but every
topic definition and all tracking data are retained. During history transfers,
an existing destination label wins; an unlabelled destination receives the
lowest-sort-order source label. A split transfers the source label to its first
child. Label editing retains its existing non-undoable service behaviour.

---

## Attempts

Every time a leaf question is assigned a colour/status, a new attempt is
created automatically.

There is no separate "new attempt" action.

Each attempt stores:

- Status
- Timestamp
- Optional note

Existing attempts can be selected and edited if the user marked one
incorrectly.

Attempt history must not be overwritten.

### Statuses

Initial default statuses/keybinds:

1 = Green
    Correct

2 = Orange
    Partial / needs reattempt

3 = Red
    Incorrect

4 = Dark red
    Did not know how to start

White means that no attempt exists and is not itself an attempt status.

Colours and keybinds may become user-configurable later.

### Attempt display

Attempts are displayed as offset/overlapping coloured boxes.

The oldest attempt is furthest behind.

The newest attempt is on top.

Previous attempts remain partially visible behind the latest attempt so that
the question's history can be understood at a glance.

---

## Keyboard Navigation

The marking workflow should be extremely fast.

Default controls:

Left / Right Arrow
    Move between leaf questions.

1 / 2 / 3 / 4
    Add an attempt with the corresponding status.

Keyboard shortcuts should be implemented so that an entire problem sheet can
be marked with minimal mouse interaction.

Custom keybinds may be implemented later.

---

## Notes and Bookmarks

Three levels of notes are supported:

- Question-set note
- Question note
- Attempt note

Question and attempt notes should normally appear only when clicked or hovered
rather than permanently occupying tracker space.

Questions (including parents) may carry multiple independent bookmark types.
Migration 006 creates UUID-backed Bookmark, Revisit and Important type records
and a question_bookmarks join table. Every old true bookmark becomes a Bookmark
assignment; old false values remain unbookmarked. The old boolean column and
Question.bookmarked are compatibility summaries maintained by assignment
triggers, not independent writable bookmark state. Deleting questions or types
cascades only the corresponding assignments.

The existing Tracker/Marking bookmark button quickly toggles the first type in
stored order. The visible dropdown arrow opens checkable type actions and a small management
dialog. Names are trimmed, non-empty and case-insensitively unique. Renames
preserve identity. Deletion is confirmed and removes only that type's markers;
the dialog clears Undo/Redo history so commands cannot reference a deleted type.
If the first type is deleted, the next becomes the quick-toggle type; if all are
deleted, clicking opens management to create a type. The editor's checkbox also
toggles this first type, without affecting other types.

Bookmark toggles use the existing shared undo stack and restore only the chosen
type. History transfers union all types. Review's Bookmarked filter includes
any marker, and individual type filters use stored type names. Service APIs
also query all bookmarked questions or one type independently.

Bookmarks are independent of attempt colour/status.

---

## Completion and Mastery

A question set is considered Attempted/Complete when every leaf question has
at least one attempt.

A question set is Mastered when every leaf question's latest attempt is green.

Orange, red or dark-red questions therefore do not make a question set
unattempted, but they prevent it from being mastered.

Unconfigured PDFs are shown separately as "Needs setup" rather than incomplete.

Colour filtering should use each question's latest attempt.

Future functionality should include sorting/filtering by colour to quickly
find questions requiring reattempts.

---

## Main UI

### Home Screen

The home screen contains module cards.

Modules are separated by their structured managed-library academic year, with
an Other heading for an empty year. Cards wrap into additional rows as the
available width decreases. The module panel scrolls vertically and retains its
persisted splitter sizes and a usable minimum height.

Each year can have optional organisational groups (for example Physics or
Maths). New Group, Rename and Delete actions manage them. Group names are
trimmed, non-empty and unique within a year ignoring case. Groups and modules
are displayed alphabetically; there is no manual ordering in this milestone.

Use a module's “Drag to group” handle to move it between groups or back to
Ungrouped in the same year. Cross-year drops are rejected. Dragging is separate
from opening a module, and is also available for modules awaiting setup.

These groups are persistent organisation metadata, not physical library
folders. Migration 005 adds UUID-backed module_groups and a separate assignment
table keyed by the existing case-folded year/module identity. Renaming keeps
the group UUID; deleting a group removes only its assignments, returning modules
to Ungrouped. No PDFs, module folders, question sets or tracking records move or
change. Scanning/importing files leaves assignments intact. The Managed Library
tree continues to represent the physical hierarchy and keeps its existing
session expansion behaviour. An external module/year folder rename changes
the existing identity; group assignments are not automatically inferred for it.

Each module shows counts for problem sheets and past papers, including
completion information.

Clicking a module opens the Question Tracker screen.

Below the module cards is a navigable quick-access file browser.

Below that is an upload/import section for adding:

- Problem Sheets
- Solutions
- Past Papers

Users should also be able to manipulate folders/files directly inside the
managed file library.

---

## Question Tracker Screen

The Question Tracker screen contains:

Left:
    Collapsible file/module tree.

Right:
    Core question tracker with topic headings and coloured attempt boxes.

The tree should support:

- Expand/collapse
- Expand all
- Navigation between modules and question sets

Solution PDFs are hidden from this screen because they are primarily used
during marking.

The tracker provides an Edit function for modifying question structure and
topics.

Colour/status controls remain visible while using the tracker.

---

## Marking Workspace

The marking workspace is primarily for checking the user's work against
solutions and rapidly recording results.

It contains two independent PDF/file panes.

Each pane can initially display a file directory.

Selecting a document opens the PDF in that pane.

A "<" / back control returns the pane to its file browser so another document
can be selected.

Solutions are visible within this workspace.

The interface should support commonly expected PDF controls including:

- Page navigation
- Zoom
- Fit to width
- Fit to page
- Rotate
- Search
- Jump to page

A compact/collapsible question-tracker strip should also be available in the
marking workspace so the user can record attempts while viewing both PDFs.

---

## Future UI Features

Not required for the first build but the architecture should not prevent:

- Progress statistics
- Colour filtering/sorting
- Reattempt workflow
- Configurable colours
- Configurable keybinds
- Improved graphical question-structure builder
- Filename-based solution suggestions

---

## Persistence

All changes should be saved automatically to SQLite as they occur.

The application should support Undo and Redo.

Default shortcuts:

Ctrl+Z = Undo
Ctrl+Y = Redo

Visible Undo/Redo controls should also be available.

The implementation should distinguish between correcting/editing an existing
attempt and undoing the creation of an attempt.

---

## Backups

The application should automatically create periodic database backups.

A limited number of recent backups should be retained so storage usage does
not grow indefinitely.

Exact backup frequency and retention count can be decided during
implementation.

---

## Export and Import

Two export types are desirable:

### Database-only export

Exports tracking data without PDFs.

### Portable export

Creates a portable archive approximately:

QuestionTrackerExport.zip
├── question-tracker.db
└── files/
    ├── Year 1/
    ├── Year 2/
    └── ...

The portable bundle should be usable when moving between computers and,
eventually, operating systems.

Initial portable import behaviour may replace the current library.

Merging two libraries can be implemented later.

Application preferences such as custom colours, keybinds and window layout do
not need to be included initially.

---

## Development Principles

The first build should prioritise:

- Correct data modelling
- Reliable persistence
- Basic file management
- Basic question tracking
- Fast keyboard marking
- Maintainable architecture

Advanced visual polish and optional features should be added after the core
workflow is reliable.

# Persistent UI state and mouse navigation

Machine-local UI preferences use QSettings under versioned `ui/v1/` keys,
separately from the database and tracker appearance values. Tracker zoom is
global, defaults to 60%, and restores within the supported 50–160% range.
Zoom is saved when the user changes it.

Home's module strip, library/right-hand panels, and Needs Setup/all-sets panels
use draggable splitters. Their states are saved when handles move and restored
on reopening. Invalid states fall back to defaults; panels cannot be collapsed,
and the module strip keeps enough height for its buttons.

Home's per-folder Expand all action remains active for that branch during the
current run, including newly discovered descendants and returns to Home. This
intent is not saved across launches.

The main window maintains back/forward screen history through its existing
screen-opening methods. Unconsumed Back/Forward mouse-button releases navigate
that history; embedded widgets accepting these events keep control. Ordinary
mouse buttons are unchanged. New destinations discard forward history; bundle
replacement resets history. History records tracker question selection and
Review module/filter state when leaving a screen, rather than retaining old
widgets. It does not record every internal pane, scroll, or tree interaction.

The bookmark control distinguishes default-on from other-types-only and lists active types in its tooltip. Dropdown checkmarks reload for the selected question on opening, including after Undo/Redo.

Used labels share one horizontal grid band in Tracker and compact Marking. Each contiguous run keeps its column span; non-contiguous assignments never span intervening questions with another label. Unused labels add no widget.
